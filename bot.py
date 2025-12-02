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
# 1.5 PostgreSQL（ovv schema）接続 + init + audit_log API
# ============================================================

import psycopg2
import psycopg2.extras

PG_CONN = None          # psycopg2 connection
AUDIT_READY = False     # audit_log テーブルが使える状態か

def pg_connect():
    """
    POSTGRES_URL を使って PostgreSQL に接続。
    失敗しても bot 自体は動かす（PG 無効モード）。
    """
    global PG_CONN
    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set, skip PG")
        PG_CONN = None
        return None

    print("[PG] Connecting via:", POSTGRES_URL[:120], "...")

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        PG_CONN = conn
        print("[PG] PostgreSQL connected OK")
        return conn
    except Exception as e:
        print("[PG] Connection failed:", repr(e))
        PG_CONN = None
        return None


def init_db(conn):
    """
    ovv.runtime_memory / ovv.audit_log を保証。
    audit_log は Phase1 で本格利用。
    """
    global AUDIT_READY

    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # 永続メモリ（将来用、今は init のみ）
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

        cur.close()
        AUDIT_READY = True
        print("[PG] init_db OK")

    except Exception as e:
        print("[PG] init_db ERROR:", repr(e))
        AUDIT_READY = False


def log_audit(event_type: str, details: Optional[dict] = None):
    """
    audit_log への書き込み。
    - PG 接続なし / 初期化前は print のみで握る。
    - details は JSONB として保存。
    """
    if details is None:
        details = {}

    # print は常に出す（デバッグ用）
    try:
        print(f"[AUDIT] {event_type} :: {details}")
    except Exception:
        pass  # details の print で失敗しても無視

    if not AUDIT_READY or PG_CONN is None:
        # DB に書けない状況ではログだけ残す
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.audit_log (event_type, details)
                VALUES (%s, %s::jsonb)
                """,
                (event_type, json.dumps(details)),
            )
    except Exception as e:
        # audit 自体の失敗は再帰しない
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# 2. Notion CRUD（今は未使用だが、エラーは audit する）
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
        print("[ERROR create_task]", repr(e))
        log_audit(
            "notion_error",
            {
                "op": "create_task",
                "name": name,
                "thread_id": thread_id,
                "channel_id": channel_id,
                "error": repr(e),
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
        print("[ERROR start_session]", repr(e))
        log_audit(
            "notion_error",
            {
                "op": "start_session",
                "task_id": task_id,
                "thread_id": thread_id,
                "error": repr(e),
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
        print("[ERROR end_session]", repr(e))
        log_audit(
            "notion_error",
            {
                "op": "end_session",
                "session_id": session_id,
                "error": repr(e),
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
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {"rich_text": [{"text": {"content": log["id"]}}]},
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", repr(e))
        log_audit(
            "notion_error",
            {
                "op": "append_logs",
                "session_id": session_id,
                "log_count": len(logs),
                "error": repr(e),
            },
        )
        return False

# ============================================================
# 3. Ovv Memory（in-memory。PG 永続化は Phase2 以降）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]

# ============================================================
# 4. Load core
# ============================================================

def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]

1. MUST keep user experience primary; MUST NOT become over-strict.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination / unjustified assumptions / over-generalization.
4. MUST respect scope boundaries; MUST NOT add requirements user did not ask.
5. SHOULD decompose → reconstruct for stable answers.
6. MUST NOT mix reasoning and answer (phase-mixing).
7. MAY trigger CDC if needed, but MUST NOT overuse it.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタントです。
ユーザー体験を最優先し、過剰な厳格化を避けてください。
次の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# 5. Ovv Call（OpenAI エラーを audit + graceful fallback）
# ============================================================

def call_ovv(context_key: int, text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]
    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()
        push_mem(context_key, "assistant", ans)

        log_audit(
            "assistant_reply",
            {
                "context_key": context_key,
                "length": len(ans),
            },
        )

        return ans[:1900]

    except Exception as e:
        print("[ERROR call_ovv]", repr(e))
        log_audit(
            "openai_error",
            {
                "context_key": context_key,
                "user_text": text[:500],
                "error": repr(e),
            },
        )
        # ユーザーには簡潔なメッセージだけ返す
        return "Ovv との通信中にエラーが発生しました。少し時間をおいて再実行してください。"

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
# 7. on_message（エラーも audit して握りこむ）
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    # bot 自身や system message は無視
    if message.author.bot:
        return

    try:
        # ovv-* チャンネルのみ対象
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        # コマンドはそのまま commands へ
        if message.content.startswith("!"):
            log_audit(
                "command",
                {
                    "command": message.content.split()[0],
                    "author": str(message.author),
                    "channel_id": message.channel.id,
                    "guild_id": message.guild.id if message.guild else None,
                },
            )
            await bot.process_commands(message)
            return

        # 通常メッセージ
        ck = get_context_key(message)
        push_mem(ck, "user", message.content)

        log_audit(
            "user_message",
            {
                "context_key": ck,
                "author": str(message.author),
                "channel_id": message.channel.id,
                "guild_id": message.guild.id if message.guild else None,
                "length": len(message.content),
            },
        )

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        # on_message レベルの予期せぬ例外
        print("[ERROR on_message]", repr(e))
        log_audit(
            "discord_error",
            {
                "where": "on_message",
                "message_id": message.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id if message.guild else None,
                "error": repr(e),
            },
        )
        try:
            await message.channel.send("内部エラーが発生しました。少し待ってから再度お試しください。")
        except Exception:
            # ここでさらにエラーしても握りつぶす
            pass

# ============================================================
# 8. Commands（例: ping）
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    try:
        log_audit(
            "command",
            {
                "command": "!ping",
                "author": str(ctx.author),
                "channel_id": ctx.channel.id,
                "guild_id": ctx.guild.id if ctx.guild else None,
            },
        )
        await ctx.send("pong")
    except Exception as e:
        print("[ERROR command ping]", repr(e))
        log_audit(
            "discord_error",
            {
                "where": "command_ping",
                "error": repr(e),
            },
        )

# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
