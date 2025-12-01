import os
print("=== [BOOT] bot.py start ===")

# 強制バッファ flush
import sys
sys.stdout.flush()

# psycopg2 import debug
print("=== [BOOT] importing psycopg2 ===")
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    print("=== [BOOT] psycopg2 import OK ===")
except Exception as e:
    print("=== [BOOT] psycopg2 import FAILED ===", e)

print("=== [BOOT] importing discord / notion / openai ===")
import discord
from discord import MessageType
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
print("=== [BOOT] imports OK ===")


from typing import Dict, List, Optional
from datetime import datetime, timezone


# ============================================================
# 1. Environment
# ============================================================

print("=== [BOOT] Loading environment variables ===")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
POSTGRES_URL = os.getenv("POSTGRES_URL")

# PostgreSQL URL はセキュリティのため一部マスクして出力
if POSTGRES_URL:
    masked = POSTGRES_URL[:12] + "***" + POSTGRES_URL[-4:]
    print(f"=== [ENV] POSTGRES_URL detected: {masked}")
else:
    print("=== [ENV] POSTGRES_URL NOT SET ===")

print("=== [BOOT] Checking env for Discord and OpenAI ===")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 未設定")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY 未設定")

print("=== [BOOT] Env OK ===")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID")
NOTION_SESSIONS_DB_ID = os.getenv("NOTION_SESSIONS_DB_ID")
NOTION_LOGS_DB_ID = os.getenv("NOTION_LOGS_DB_ID")

print("=== [BOOT] Checking Notion env ===")

if not NOTION_API_KEY:
    raise RuntimeError("NOTION_API_KEY 未設定")
if not NOTION_TASKS_DB_ID:
    raise RuntimeError("NOTION_TASKS_DB_ID 未設定")
if not NOTION_SESSIONS_DB_ID:
    raise RuntimeError("NOTION_SESSIONS_DB_ID 未設定")
if not NOTION_LOGS_DB_ID:
    raise RuntimeError("NOTION_LOGS_DB_ID 未設定")

notion = Client(auth=NOTION_API_KEY)
print("=== [BOOT] Notion env OK ===")


# ============================================================
# 1.5 PostgreSQL
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")

pg_conn = None

def pg_connect():
    global pg_conn
    print("=== [PG] pg_connect() called ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL 未設定 → skip")
        return

    try:
        print("[PG] Trying psycopg2.connect ...")
        pg_conn = psycopg2.connect(
            POSTGRES_URL,
            cursor_factory=RealDictCursor,
            sslmode="require"
        )
        print("[PG] PostgreSQL connected OK")
    except Exception as e:
        print("[PG] PostgreSQL connect FAILED:", e)
        pg_conn = None


def init_db():
    print("=== [PG] init_db() called ===")

    if pg_conn is None:
        print("[PG] init_db skipped (no connection)")
        return

    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runtime_memory (
                    id SERIAL PRIMARY KEY,
                    thread_key BIGINT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            pg_conn.commit()
            print("[PG] init_db OK")
    except Exception as e:
        print("[PG] init_db FAILED:", e)


# ============================================================
# 以降はあなたの元コードそのまま（中略）
# ============================================================

# ...（Notion CRUD、Ovv、Discord設定などは変更不要なので省略）...


# ============================================================
# PostgreSQL Connect + init_db
# ============================================================

print("=== [BOOT] Calling pg_connect() ===")
pg_connect()
print("=== [BOOT] Calling init_db() ===")
init_db()
print("=== [BOOT] Database setup finished ===")

# 強制 flush
sys.stdout.flush()


# ============================================================
# 14. Run
# ============================================================

def main():
    print("=== [RUN] Starting Discord bot ===")
    sys.stdout.flush()
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
