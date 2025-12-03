import os
import json
import discord
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
# 1.5 PostgreSQL connect + init + audit_log
# ============================================================

import psycopg2
import psycopg2.extras

PG_CONN = None
AUDIT_READY = False

def pg_connect():
    global PG_CONN
    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set")
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
    global AUDIT_READY
    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS ovv;
        """)

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
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.thread_brain (
                context_key BIGINT PRIMARY KEY,
                summary JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.close()
        AUDIT_READY = True
        print("[PG] init_db OK")

    except Exception as e:
        print("[PG] init_db ERROR:", repr(e))
        AUDIT_READY = False


def log_audit(event_type: str, details: Optional[dict] = None):
    if details is None:
        details = {}

    try:
        print(f"[AUDIT] {event_type} :: {details}")
    except:
        pass

    if not AUDIT_READY or PG_CONN is None:
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
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# 2. Notion CRUD（今は未使用だが動く形で保持）
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
        log_audit("notion_error", {"op": "create_task", "error": repr(e)})
        return None

# ============================================================
# 3. In-memory OVV memory
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

1. MUST keep user experience primary.
2. MUST avoid hallucination.
3. MUST avoid phase-mixing.
4. MUST keep boundaries stable.
5. MUST decompose → reconstruct.
6. Clarify only when necessary.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上の Ovv アシスタントです。
次の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# 5. thread_brain load / save
# ============================================================

def load_thread_brain(context_key: int):
    if PG_CONN is None:
        return None

    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT summary
                FROM ovv.thread_brain
                WHERE context_key = %s
                """,
                (context_key,)
            )
            row = cur.fetchone()
            if row:
                summary = row["summary"]
                log_audit("thread_brain_loaded", {
                    "context_key": context_key,
                    "exists": True
                })
                return summary
            else:
                log_audit("thread_brain_loaded", {
                    "context_key": context_key,
                    "exists": False
                })
                return None
    except Exception as e:
        log_audit("pg_error", {"op": "load_thread_brain", "error": repr(e)})
        return None


def save_thread_brain(context_key: int, summary_json: dict):
    if PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (context_key)
                DO UPDATE SET summary = EXCLUDED.summary,
                              updated_at = NOW()
                """,
                (context_key, json.dumps(summary_json))
            )
        log_audit("thread_brain_saved", {
            "context_key": context_key,
            "size": len(json.dumps(summary_json))
        })

    except Exception as e:
        log_audit("pg_error", {"op": "save_thread_brain", "error": repr(e)})

# ============================================================
# 6. Ovv call
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

        log_audit("assistant_reply", {
            "context_key": context_key,
            "length": len(ans)
        })

        return ans[:1900]

    except Exception as e:
        log_audit("openai_error", {"error": repr(e)})
        return "Ovv との通信中にエラーが発生しました。"

# ============================================================
# 7. Discord setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id

# ============================================================
# 8. on_message
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    try:
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        if message.content.startswith("!"):
            log_audit("command", {
                "command": message.content.split()[0],
                "author": str(message.author),
                "channel_id": message.channel.id,
            })
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        push_mem(ck, "user", message.content)

        log_audit("user_message", {
            "context_key": ck,
            "length": len(message.content)
        })

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        log_audit("discord_error", {"error": repr(e)})
        try:
            await message.channel.send("内部エラーが発生しました。")
        except:
            pass

# ============================================================
# 9. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")


@bot.command(name="test_thread")
async def test_thread(ctx):
    ck = get_context_key(ctx.message)

    summary = load_thread_brain(ck)
    if summary is None:
        summary = {"test": "initial", "updated": str(datetime.now().isoformat())}
        save_thread_brain(ck, summary)
        await ctx.send(f"[thread_brain] created: {summary}")
    else:
        summary["last_test"] = str(datetime.now().isoformat())
        save_thread_brain(ck, summary)
        await ctx.send(f"[thread_brain] updated: {summary}")


# ============================================================
# 10. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
