import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import discord
from discord import MessageType
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

import psycopg2
import psycopg2.extras

# ============================================================
# 1. Environment
# ============================================================

print("=== [BOOT] Loading environment variables ===")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID")
NOTION_SESSIONS_DB_ID = os.getenv("NOTION_SESSIONS_DB_ID")
NOTION_LOGS_DB_ID = os.getenv("NOTION_LOGS_DB_ID")
POSTGRES_URL = os.getenv("POSTGRES_URL")

if not DISCORD_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Discord/OpenAI env missing")

if not NOTION_API_KEY:
    raise RuntimeError("NOTION_API_KEY missing")

print("=== [ENV] Env OK ===")
print("=== [ENV] POSTGRES_URL detected:", str(POSTGRES_URL)[:80], "...")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1.5 PostgreSQL（ovv schema）接続 + init
# ============================================================

PG_CONN = None  # type: ignore


def pg_connect():
    """
    グローバル PG_CONN を初期化する。
    失敗しても例外を外に出さず、PG_CONN = None のままにする。
    """
    global PG_CONN
    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set, skip PG")
        PG_CONN = None
        return

    print("[PG] Connecting via:", POSTGRES_URL[:120], "...")

    try:
        # POSTGRES_URL 側に sslmode=require が含まれている想定
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        PG_CONN = conn
        print("[PG] PostgreSQL connected OK")
    except Exception as e:
        print("[PG] Connection failed:", e)
        PG_CONN = None


def init_db():
    """
    ovv.runtime_memory / ovv.audit_log の 2 テーブルを保証する。
    PG_CONN が無い場合はスキップ。
    """
    print("=== [PG] init_db() CALLED ===")

    if PG_CONN is None:
        print("[PG] init_db skipped (no connection)")
        return

    try:
        cur = PG_CONN.cursor()

        # 永続メモリ
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # 監査ログ
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        cur.close()
        print("[PG] init_db OK")
    except Exception as e:
        print("[PG] init_db failed:", e)


# ============================================================
# 1.6 Audit Middleware
# ============================================================

def audit(event_type: str, details: dict, *, chain_id: Optional[str] = None):
    """
    監査ミドルウェア。
    ・例外は絶対に外に出さない
    ・PG_CONN がなくても何もしないで戻る
    ・details は JSONB として ovv.audit_log に格納
    """
    if PG_CONN is None:
        return

    try:
        record = dict(details) if details is not None else {}
        # 共通フィールドを付与
        if chain_id is not None:
            record.setdefault("chain_id", chain_id)
        record.setdefault("event_type", event_type)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())

        cur = PG_CONN.cursor()
        cur.execute(
            """
            INSERT INTO ovv.audit_log (event_type, details)
            VALUES (%s, %s::jsonb);
            """,
            (event_type, json.dumps(record, ensure_ascii=False)),
        )
        cur.close()
    except Exception as e:
        # 監査失敗は標準出力には出すが、処理を止めない
        print("[AUDIT] failed:", e)


# ============================================================
# 2. Notion CRUD（今は既存タスク系の下地として残す）
# ============================================================

async def create_task(name, goal, thread_id, channel_id):
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(channel_id)}}]},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return page["id"]
    except Exception as e:
        print("[ERROR create_task]", e)
        audit(
            "notion_error",
            {
                "op": "create_task",
                "name": name,
                "thread_id": thread_id,
                "channel_id": channel_id,
                "error": str(e),
            },
        )
        return None


async def start_session(task_id, name, thread_id):
    now = datetime.now(timezone.utc)
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "start_time": {"date": {"start": now.isoformat()}},
                "created_at": {"date": {"start": now.isoformat()}},
                "updated_at": {"date": {"start": now.isoformat()}},
            },
        )
        return page["id"]
    except Exception as e:
        print("[ERROR start_session]", e)
        audit(
            "notion_error",
            {
                "op": "start_session",
                "task_id": task_id,
                "thread_id": thread_id,
                "error": str(e),
            },
        )
        return None


async def end_session(session_id, summary):
    now = datetime.now(timezone.utc).isoformat()
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": now}},
                "summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
                "updated_at": {"date": {"start": now}},
            },
        )
        return True
    except Exception as e:
        print("[ERROR end_session]", e)
        audit(
            "notion_error",
            {
                "op": "end_session",
                "session_id": session_id,
                "error": str(e),
            },
        )
        return False


async def append_logs(session_id, logs):
    try:
        for log in logs:
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {
                        "rich_text": [
                            {"text": {"content": log["content"][:2000]}}
                        ]
                    },
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {
                        "rich_text": [{"text": {"content": log["id"]}}]
                    },
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", e)
        audit(
            "notion_error",
            {
                "op": "append_logs",
                "session_id": session_id,
                "count": len(logs),
                "error": str(e),
            },
        )
        return False


# ============================================================
# 3. Ovv Memory（Runtime + PG 永続化）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40


def save_runtime_memory(context_key: int):
    """
    OVV_MEMORY[context_key] を ovv.runtime_memory に UPSERT する。
    PG が落ちていても例外は外に出さない。
    """
    if PG_CONN is None:
        return

    try:
        mem = OVV_MEMORY.get(context_key, [])
        session_id = str(context_key)
        data = json.dumps(mem, ensure_ascii=False)

        cur = PG_CONN.cursor()
        cur.execute(
            """
            INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (session_id)
            DO UPDATE SET
                memory_json = EXCLUDED.memory_json,
                updated_at = EXCLUDED.updated_at;
            """,
            (session_id, data),
        )
        cur.close()

        audit(
            "runtime_memory_persist",
            {"session_id": session_id, "items": len(mem)},
        )
    except Exception as e:
        print("[PG] save_runtime_memory failed:", e)


def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]
    # 各更新ごとに PG 永続化（規模的に許容）
    save_runtime_memory(key)


# ============================================================
# 4. Load core
# ============================================================

def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

SYSTEM_PROMPT = """
あなたは Discord 上で動作するアシスタント。
Ovv Soft-Core を最優先する。
"""


# ============================================================
# 5. Ovv Call
# ============================================================

def call_ovv(context_key: int, text: str, *, chain_id: Optional[str] = None) -> str:
    """
    Ovv コア呼び出し。前後で audit を行う。
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]
    messages.extend(OVV_MEMORY.get(context_key, []))
    messages.append({"role": "user", "content": text})

    audit(
        "ovv_call_request",
        {
            "context_key": context_key,
            "user_preview": text[:120],
            "history_len": len(OVV_MEMORY.get(context_key, [])),
        },
        chain_id=chain_id,
    )

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        push_mem(context_key, "assistant", ans)

        audit(
            "ovv_call_response",
            {
                "context_key": context_key,
                "answer_preview": ans[:120],
                "answer_len": len(ans),
            },
            chain_id=chain_id,
        )

        return ans[:1900]

    except Exception as e:
        print("[ERROR call_ovv]", e)
        audit(
            "error",
            {
                "where": "call_ovv",
                "context_key": context_key,
                "error": str(e),
            },
            chain_id=chain_id,
        )
        return "Ovv 通信エラーが発生しました。"


# ============================================================
# 6. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    # guild 単位でネームスペース化
    return (msg.guild.id << 32) | msg.channel.id


# ============================================================
# 7. on_message
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    # 1) bot 自身は無視
    if message.author.bot:
        return

    # 2) Discord イベントごとの chain_id
    chain_id = str(uuid.uuid4())

    # 3) Discord メッセージ監査
    try:
        guild_id = message.guild.id if message.guild else None
        thread_id = message.channel.id if isinstance(message.channel, discord.Thread) else None

        audit(
            "discord_message",
            {
                "author_id": message.author.id,
                "author_name": message.author.display_name,
                "content_preview": message.content[:120],
                "channel_id": message.channel.id,
                "thread_id": thread_id,
                "guild_id": guild_id,
                "is_command": message.content.startswith("!"),
            },
            chain_id=chain_id,
        )
    except Exception as e:  # 念のため
        print("[WARN] discord_message audit failed:", e)

    # 4) コマンドはそのまま commands へ（audit 済み）
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # 5) 通常メッセージ → Ovv 応答
    context_key = get_context_key(message)
    push_mem(context_key, "user", message.content)

    ans = call_ovv(context_key, message.content, chain_id=chain_id)
    await message.channel.send(ans)


# ============================================================
# 8. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    chain_id = str(uuid.uuid4())
    audit(
        "command",
        {
            "name": "ping",
            "author_id": ctx.author.id,
            "channel_id": ctx.channel.id,
        },
        chain_id=chain_id,
    )
    await ctx.send("pong")


@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    """
    明示的に Ovv を叩くコマンド。
    on_message と同じく audit + runtime_memory 対応。
    """
    chain_id = str(uuid.uuid4())

    audit(
        "command",
        {
            "name": "o",
            "author_id": ctx.author.id,
            "channel_id": ctx.channel.id,
            "question_preview": question[:120],
        },
        chain_id=chain_id,
    )

    msg = ctx.message
    context_key = get_context_key(msg)
    push_mem(context_key, "user", question)

    ans = call_ovv(context_key, question, chain_id=chain_id)
    await ctx.send(ans)


# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
pg_connect()
init_db()
print("=== [RUN] Starting Discord bot ===")

bot.run(DISCORD_BOT_TOKEN)
