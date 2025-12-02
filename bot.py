import os
import json
import discord
from discord import MessageType
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import Dict, List, Optional
from datetime import datetime, timezone

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

import psycopg2
import psycopg2.extras

PG_CONN = None  # グローバル接続

def pg_connect():
    """
    POSTGRES_URL から ovv_postgres_bqhb に接続し、グローバル PG_CONN を更新。
    """
    global PG_CONN

    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set, skip PG")
        PG_CONN = None
        return None

    print("[PG] Connecting via:", POSTGRES_URL[:120], "...")

    try:
        PG_CONN = psycopg2.connect(POSTGRES_URL)
        PG_CONN.autocommit = True
        print("[PG] PostgreSQL connected OK")
        return PG_CONN
    except Exception as e:
        print("[PG] Connection failed:", e)
        PG_CONN = None
        return None


def get_pg_conn():
    """
    どこからでも呼べる PG 接続ハンドラ。
    切断されていたら再接続を試みる。
    """
    global PG_CONN

    if PG_CONN is None:
        return pg_connect()

    try:
        if PG_CONN.closed:
            print("[PG] Connection closed, reconnecting...")
            return pg_connect()
    except Exception as e:
        print("[PG] Connection state check failed:", e)
        return pg_connect()

    return PG_CONN


def init_db():
    """
    ovv.runtime_memory / ovv.audit_log テーブルを保証。
    """
    print("=== [PG] init_db() CALLED ===")

    conn = get_pg_conn()
    if conn is None:
        print("[PG] init_db skipped (no connection)")
        return

    try:
        with conn.cursor() as cur:
            # 永続メモリ
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                    session_id TEXT PRIMARY KEY,
                    memory_json JSONB NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)

            # 監査ログ
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ovv.audit_log (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    details JSONB,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)

        print("[PG] init_db OK")
    except Exception as e:
        print("[PG] init_db FAILED:", e)


# ============================================================
# 2. Notion CRUD  （今は未使用だが維持）
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
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {"rich_text": [{"text": {"content": log["id"]}}]},
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", e)
        return False


# ============================================================
# 3. Ovv Memory（Phase 1: SQL 永続化）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40


def load_memory_from_db(session_id: str) -> List[Dict[str, str]]:
    """
    ovv.runtime_memory から該当 session_id のメモリを取得。
    失敗時は空リスト。
    """
    conn = get_pg_conn()
    if conn is None:
        return []

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT memory_json FROM ovv.runtime_memory WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()
            if row and row.get("memory_json") is not None:
                # JSONB → Python (すでに dict/list のはず)
                return row["memory_json"]
    except Exception as e:
        print("[PG] load_memory_from_db error:", e)

    return []


def save_memory_to_db(session_id: str, memory: List[Dict[str, str]]):
    """
    OVV_MEMORY の内容をそのまま JSONB として upsert。
    """
    conn = get_pg_conn()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    memory_json = EXCLUDED.memory_json,
                    updated_at  = EXCLUDED.updated_at;
                """,
                (session_id, psycopg2.extras.Json(memory)),
            )
    except Exception as e:
        print("[PG] save_memory_to_db error:", e)


def get_memory(context_key: int) -> List[Dict[str, str]]:
    """
    メモリアクセスの唯一の入り口。
    - プロセス内に無ければ DB からロードしてキャッシュ。
    """
    if context_key in OVV_MEMORY:
        return OVV_MEMORY[context_key]

    sid = str(context_key)
    mem = load_memory_from_db(sid)
    OVV_MEMORY[context_key] = mem
    return mem


def push_mem(key: int, role: str, content: str):
    """
    - プロセスメモリ更新
    - OVV_MEMORY_LIMIT にトリム
    - 即座に SQL へ upsert（Phase 1 はシンプルに毎回）
    """
    mem = get_memory(key)
    mem.append({"role": role, "content": content})
    if len(mem) > OVV_MEMORY_LIMIT:
        mem[:] = mem[-OVV_MEMORY_LIMIT:]

    OVV_MEMORY[key] = mem
    save_memory_to_db(str(key), mem)


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
Ovv Soft-Core を最優先し、過剰な厳格化を避ける。
"""


# ============================================================
# 5. Ovv Call
# ============================================================

def call_ovv(context_key: int, text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    # DB 永続化されたメモリも含めて投入
    msgs.extend(get_memory(context_key))
    msgs.append({"role": "user", "content": text})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.7,
    )

    ans = res.choices[0].message.content.strip()
    push_mem(context_key, "assistant", ans)
    return ans[:1900]


# ============================================================
# 6. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id


# ============================================================
# 7. on_message
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # コマンドはそのまま commands へ
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    ck = get_context_key(message)
    push_mem(ck, "user", message.content)

    ans = call_ovv(ck, message.content)
    await message.channel.send(ans)


# ============================================================
# 8. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
pg_connect()
init_db()

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
