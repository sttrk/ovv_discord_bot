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
# 1.5 PostgreSQL（ovv schema）接続 + init + audit_log API
# ============================================================

import psycopg2
import psycopg2.extras

PG_CONN = None
AUDIT_READY = False


def pg_connect():
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
    global AUDIT_READY

    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

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
    except Exception:
        pass

    if not AUDIT_READY or PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.audit_log (event_type, details)
                VALUES (%s, %s::jsonb)
            """, (event_type, json.dumps(details)))
    except Exception as e:
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# 2. Notion CRUD（未使用）
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
# 3. Ovv Memory（short-term）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40


def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 4. Load Core
# ============================================================

def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.6]

1. MUST keep user experience primary
2. MUST NOT become over-strict
3. MUST avoid hallucination and unjustified assumptions
4. MUST keep boundaries clean
5. MUST separate reasoning phases
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する Ovv です。
次の Ovv Soft-Core を常に保持し動作します。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. call_ovv
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

        log_audit("assistant_reply", {"context_key": context_key, "length": len(ans)})

        return ans[:1900]

    except Exception as e:
        log_audit("openai_error",
                  {"context_key": context_key, "user_text": text[:300], "error": repr(e)})
        return "Ovv 内部でエラーが発生しました。再試行してください。"


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
        # ovv-* チャンネル以外は無視
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        # commands
        if message.content.startswith("!"):
            log_audit("command", {
                "command": message.content,
                "author": str(message.author)
            })
            await bot.process_commands(message)
            return

        # user message
        ck = get_context_key(message)
        push_mem(ck, "user", message.content)

        log_audit("user_message", {
            "context_key": ck,
            "author": str(message.author),
            "length": len(message.content),
        })

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        log_audit("discord_error", {"where": "on_message", "error": repr(e)})
        try:
            await message.channel.send("内部エラーが発生しました。")
        except:
            pass


# ============================================================
# 8. Commands（ping + test suite）
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")


# -------------------------
# A. PG Write Test
# -------------------------
@bot.command(name="test_pg_write")
async def test_pg_write(ctx):
    log_audit("command", {"command": "!test_pg_write"})

    try:
        if PG_CONN is None:
            return await ctx.send("PG connection is None")

        with PG_CONN.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.runtime_memory (session_id, memory_json)
                VALUES ('test-session', '[{\"role\":\"system\",\"content\":\"ok\"}]')
                ON CONFLICT (session_id)
                DO UPDATE SET memory_json = EXCLUDED.memory_json, updated_at = NOW();
            """)

        await ctx.send("PG write OK")
    except Exception as e:
        log_audit("discord_error", {"where": "test_pg_write", "error": repr(e)})
        await ctx.send("ERROR")


# -------------------------
# B. PG Read Test
# -------------------------
@bot.command(name="test_pg_read")
async def test_pg_read(ctx):
    log_audit("command", {"command": "!test_pg_read"})

    try:
        if PG_CONN is None:
            return await ctx.send("PG connection is None")

        with PG_CONN.cursor() as cur:
            cur.execute("""
                SELECT session_id, jsonb_array_length(memory_json), updated_at
                FROM ovv.runtime_memory
                ORDER BY updated_at DESC
                LIMIT 20;
            """)
            rows = cur.fetchall()

        lines = [f"{r[0]} | {r[1]} items | {r[2]}" for r in rows]
        await ctx.send("PG read:\n" + "\n".join(lines))

    except Exception as e:
        log_audit("discord_error", {"where": "test_pg_read", "error": repr(e)})
        await ctx.send("ERROR")


# -------------------------
# C. Audit Tail Test
# -------------------------
@bot.command(name="test_audit_tail")
async def test_audit_tail(ctx):
    log_audit("command", {"command": "!test_audit_tail"})

    try:
        if PG_CONN is None:
            return await ctx.send("PG connection is None")

        with PG_CONN.cursor() as cur:
            cur.execute("""
                SELECT id, event_type, created_at
                FROM ovv.audit_log
                ORDER BY id DESC
                LIMIT 20;
            """)
            rows = cur.fetchall()

        lines = [f"{r[0]} | {r[1]} | {r[2]}" for r in rows]
        await ctx.send("Audit tail:\n" + "\n".join(lines))

    except Exception as e:
        log_audit("discord_error", {"where": "test_audit_tail", "error": repr(e)})
        await ctx.send("ERROR")


# -------------------------
# D. Pipeline Test
# -------------------------
@bot.command(name="test_pipeline")
async def test_pipeline(ctx):
    log_audit("command", {"command": "!test_pipeline"})

    try:
        ck = get_context_key(ctx.message)
        ram_count = len(OVV_MEMORY.get(ck, []))

        msg = (
            "Pipeline structure:\n"
            f"SYSTEM\n"
            f"CORE: {len(OVV_CORE)} chars\n"
            f"EXTERNAL: {len(OVV_EXTERNAL)} chars\n"
            f"AUDIT_SUMMARY: (not implemented yet)\n"
            f"PG_MEMORY: (Phase2)\n"
            f"RAM_MEMORY: {ram_count} entries\n"
        )

        await ctx.send(msg)

    except Exception as e:
        log_audit("discord_error", {"where": "test_pipeline", "error": repr(e)})
        await ctx.send("ERROR")


# -------------------------
# E. Error Test
# -------------------------
@bot.command(name="test_error")
async def test_error(ctx):
    log_audit("command", {"command": "!test_error"})

    try:
        raise RuntimeError("Intentional test error")

    except Exception as e:
        log_audit("discord_error", {"where": "test_error", "error": repr(e)})
        await ctx.send("Error test complete (caught)")


# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
