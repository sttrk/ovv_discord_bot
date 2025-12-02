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
print("=== [ENV] POSTGRES_URL detected:", str(POSTGRES_URL)[:40], "...")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1.5 PostgreSQL（ovv schema）接続 + init
# ============================================================

import psycopg2
import psycopg2.extras

def pg_connect():
    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set, skip PG")
        return None

    print("[PG] Connecting via:", POSTGRES_URL[:60], "...")

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        print("[PG] PostgreSQL connected OK")
        return conn

    except Exception as e:
        print("[PG] Connection failed:", e)
        return None


def init_db(conn):
    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        return

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
            event_type TEXT NOT NULL,
            details JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)

    cur.close()
    print("[PG] init_db OK")

# ============================================================
# 1.6 Audit（完全版）
# ============================================================

def audit(conn, event_type: str, **details):
    """
    PostgreSQL audit logger.
    details: dict → JSONB に格納される。
    """
    if conn is None:
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ovv.audit_log(event_type, details) VALUES (%s, %s)",
            (event_type, json.dumps(details, ensure_ascii=False))
        )
        cur.close()
    except Exception as e:
        print("[AUDIT ERROR]", e)


# ============================================================
# 2. Notion CRUD（変更なし）
# ============================================================

async def create_task(name, goal, thread_id, channel_id):
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
            },
        )
        return page["id"]
    except Exception as e:
        print("[ERROR create_task]", e)
        return None

# 必要部分のみ保持
# （あなたの Notion コードはそのままで動作するため省略していない）

# ============================================================
# 3. Ovv Memory
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key, role, content):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]

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
# 5. Ovv Call（audit 統合）
# ============================================================

def call_ovv(context_key, text, conn, message_obj=None):
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]
    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": text})

    # Audit user message
    if message_obj:
        audit(conn,
              "user_message",
              content_preview=text[:200],
              discord_message_id=str(message_obj.id),
              author=str(message_obj.author),
              context_key=str(context_key),
              channel_id=str(message_obj.channel.id),
              guild_id=str(message_obj.guild.id) if message_obj.guild else None)

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.7,
    )

    ans = res.choices[0].message.content.strip()
    push_mem(context_key, "assistant", ans)

    # Audit assistant reply
    audit(conn,
          "assistant_reply",
          answer_preview=ans[:200],
          context_key=str(context_key))

    return ans[:1900]

# ============================================================
# 6. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def get_context_key(msg):
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id

# ============================================================
# 7. on_message（audit 統合）
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    # command → audit command
    if message.content.startswith("!"):
        audit(conn, "command", command=message.content, discord_message_id=str(message.id))
        await bot.process_commands(message)
        return

    ck = get_context_key(message)

    ans = call_ovv(ck, message.content, conn, message)
    await message.channel.send(ans)

# ============================================================
# 8. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    audit(conn, "command", command="ping", channel=str(ctx.channel.id))
    await ctx.send("pong")

# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)

print("=== [RUN] Starting Discord bot ===")

bot.run(DISCORD_BOT_TOKEN)
