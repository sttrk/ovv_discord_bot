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
# 1.5 PostgreSQL（ovv schema）接続 + init + audit_log API + A-1
# ============================================================

import psycopg2
import psycopg2.extras

PG_CONN = None
AUDIT_READY = False

def pg_connect():
    """
    POSTGRES_URL を使って PostgreSQL に接続。
    失敗しても bot は動作継続（PG 無効モード）。
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
    audit_log は Phase1 で本格運用。
    """
    global AUDIT_READY

    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                session_id TEXT,
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


def log_audit(event_type: str, details: Optional[dict] = None, session_id: Optional[int] = None):
    """
    audit_log への書き込み。
    """
    if details is None:
        details = {}

    try:
        print(f"[AUDIT] {event_type} :: {details}")
    except Exception:
        pass

    if not AUDIT_READY or PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.audit_log (session_id, event_type, details)
                VALUES (%s, %s, %s::jsonb)
                """,
                (str(session_id) if session_id else None,
                 event_type,
                 json.dumps(details)),
            )
    except Exception as e:
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# A-1. audit_log 抽出ユーティリティ（Ovv v2.2）
# ============================================================

def get_audit_log(context_key: int, limit: Optional[int] = None):
    """
    audit_log から context_key (= session_id) のログ取得。
    時系列 ASC。
    """
    if PG_CONN is None:
        print("[PG] get_audit_log skipped (no connection)")
        return []

    try:
        cur = PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if limit:
            sql = """
                SELECT session_id, event_type, details, created_at
                FROM ovv.audit_log
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s;
            """
            cur.execute(sql, (str(context_key), limit))
        else:
            sql = """
                SELECT session_id, event_type, details, created_at
                FROM ovv.audit_log
                WHERE session_id = %s
                ORDER BY created_at ASC;
            """
            cur.execute(sql, (str(context_key),))

        rows = cur.fetchall()
        cur.close()
        return rows

    except Exception as e:
        print("[PG ERROR get_audit_log]", e)
        return []


# ============================================================
# 2. Notion CRUD（未使用でも audit は行う）
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
        log_audit(
            "notion_error",
            {
                "op": "create_task",
                "name": name,
                "goal": goal,
                "error": repr(e),
            },
        )
        return None

# (start_session / end_session / append_logs は省略。上と同じ構造。)


# ============================================================
# 3. In-memory Memory（直近 40 件）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
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
1. MUST keep user experience primary.
2. MUST avoid strictness beyond user intent.
3. MUST avoid hallucination.
4. MUST respect user scope.
5. SHOULD decompose → reconstruct.
6. MUST NOT mix reasoning and answer.
7. MAY use CDC when needed.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上のアシスタントです。
以下の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. call_ovv（audit + エラー握り）
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
            session_id=context_key,
        )

        return ans[:1900]

    except Exception as e:
        log_audit(
            "openai_error",
            {
                "context_key": context_key,
                "text": text[:500],
                "error": repr(e),
            },
            session_id=context_key,
        )
        return "Ovv との通信エラーが発生しました。少し時間をおいて再試行してください。"


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

    try:
        # ovv-* チャンネル以外はスルー
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        if message.content.startswith("!"):
            log_audit(
                "command",
                {
                    "command": message.content.split()[0],
                    "author": str(message.author),
                },
                session_id=get_context_key(message),
            )
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        push_mem(ck, "user", message.content)

        log_audit(
            "user_message",
            {
                "author": str(message.author),
                "length": len(message.content),
            },
            session_id=ck,
        )

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        log_audit(
            "discord_error",
            {
                "where": "on_message",
                "error": repr(e),
            },
        )
        try:
            await message.channel.send("内部エラーが発生しました。再試行してください。")
        except:
            pass


# ============================================================
# 8. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        log_audit("command", {"command": "!ping"}, session_id=ck)
        await ctx.send("pong")
    except Exception as e:
        log_audit("discord_error", {"where": "command_ping", "error": repr(e)})


# ============================================================
# 9. Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
