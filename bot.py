import os
import json
import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import Dict, List
from datetime import datetime, timezone

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
# PostgreSQL connect + init
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
# Persistent memory helpers
# ============================================================

def load_memory_from_db(conn, session_id):
    """Return list of dicts (memory)"""
    if conn is None:
        return []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT memory_json FROM ovv.runtime_memory WHERE session_id=%s;", (session_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            print(f"[PG] Loaded memory for {session_id}, len={len(row['memory_json'])}")
            return row["memory_json"]
        print(f"[PG] No memory found for {session_id}")
        return []
    except Exception as e:
        print("[PG] load_memory error:", e)
        return []


def save_memory_to_db(conn, session_id, memory_json):
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (session_id)
            DO UPDATE SET memory_json = EXCLUDED.memory_json,
                          updated_at = NOW();
        """, (session_id, json.dumps(memory_json)))
        cur.close()
        print(f"[PG] Saved memory for {session_id}, len={len(memory_json)}")
    except Exception as e:
        print("[PG] save_memory error:", e)


def write_audit_log(conn, event_type, details):
    if conn is None:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ovv.audit_log(event_type, details)
            VALUES (%s, %s);
        """, (event_type, json.dumps(details)))
        cur.close()
        print(f"[PG] audit_log: {event_type}")
    except Exception as e:
        print("[PG] audit_log error:", e)

# ============================================================
# Ovv Core loading
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
# Ovv call wrapper
# ============================================================

def call_ovv(conn, session_id, user_text):
    # 1. Load memory from DB
    mem = load_memory_from_db(conn, session_id)

    # 2. Compose messages
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]
    msgs.extend(mem)
    msgs.append({"role": "user", "content": user_text})

    # 3. Call model
    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.7,
    )
    ans = res.choices[0].message.content.strip()

    # 4. Push updated memory
    mem.append({"role": "user", "content": user_text})
    mem.append({"role": "assistant", "content": ans})

    # 5. Save memory to DB
    save_memory_to_db(conn, session_id, mem[-40:])

    # 6. audit log
    write_audit_log(conn, "user_message", {"session_id": session_id, "content": user_text})
    write_audit_log(conn, "assistant_reply", {"session_id": session_id, "content": ans})

    return ans[:1900]

# ============================================================
# Discord setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def get_context_key(msg):
    if isinstance(msg.channel, discord.Thread):
        return str(msg.channel.id)
    return str((msg.guild.id << 32) | msg.channel.id)

# ============================================================
# Event: on_message
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    session_id = get_context_key(message)
    reply = call_ovv(conn, session_id, message.content)
    await message.channel.send(reply)

# ============================================================
# Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")

# ============================================================
# Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
