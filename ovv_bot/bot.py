import os
import json
import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import Dict, List, Optional
from datetime import datetime, timezone
import psycopg2
import psycopg2.extras

# ============================================================
# [DEBUG HOOK] imports
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# 0. PostgreSQL Module (IMPORTANT: module import only)
# ============================================================
# PG_CONN を from-import すると値が同期されないため、必ず module import で保持する
import database.pg as db_pg


# ============================================================
# 1. Environment
# ============================================================

print("=== [BOOT] Loading environment variables ===")

from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
    NOTION_TASKS_DB_ID,
    NOTION_SESSIONS_DB_ID,
    NOTION_LOGS_DB_ID,
    POSTGRES_URL,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)


# ============================================================
# 1.5 PostgreSQL connect + init + audit_log
# ============================================================

print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)


def log_audit(event_type: str, details: Optional[dict] = None):
    """Wrapper for db_pg.log_audit"""
    return db_pg.log_audit(event_type, details)


# ============================================================
# 2. Notion CRUD
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
        log_audit("notion_error", {"op": "start_session", "error": repr(e)})
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
        log_audit("notion_error", {"op": "end_session", "error": repr(e)})
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
                    "created_at": {"date": {"start": log["created_at"]}}},
            )
        return True

    except Exception as e:
        log_audit("notion_error", {"op": "append_logs", "error": repr(e)})
        return False


# ============================================================
# 3. Runtime Memory
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    return db_pg.load_runtime_memory(session_id)


def save_runtime_memory(session_id: str, mem: List[dict]):
    return db_pg.save_runtime_memory(session_id, mem)


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    return db_pg.append_runtime_memory(session_id, role, content, limit)


# ============================================================
# 4. Soft-Core / Core / External
# ============================================================

from ovv.core_loader import load_core, load_external
OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary
2. MUST use Clarify only when needed
3. MUST avoid hallucination
4. MUST respect boundaries
5. SHOULD decompose → reconstruct
6. MUST NOT phase-mix
7. MAY trigger CDC sparingly
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタントです。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. thread_brain utilities
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
    return db_pg.load_thread_brain(context_key)


def save_thread_brain(context_key: int, summary: dict) -> bool:
    return db_pg.save_thread_brain(context_key, summary)


def generate_thread_brain(context_key: int, mem: List[dict]):
    return db_pg.generate_thread_brain(context_key, mem)


# ============================================================
# 6. Ovv Call
# ============================================================

def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        append_runtime_memory(str(context_key), "assistant", ans)
        log_audit("assistant_reply", {"context_key": context_key})

        return ans[:1900]

    except Exception as e:
        log_audit("openai_error", {"context_key": context_key, "error": repr(e)})
        return "Ovv との通信中にエラーが発生しました。"


# ============================================================
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


def is_task_channel(message: discord.Message) -> bool:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return parent.name.lower().startswith("task_") if parent else False
    return ch.name.lower().startswith("task_")


# ============================================================
# 8. on_message + DEBUG HOOK
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    handled = await route_debug_message(bot, message)
    if handled:
        return

    try:
        if message.content.startswith("!"):
            log_audit("command", {"cmd": message.content})
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        session_id = str(ck)

        append_runtime_memory(
            session_id,
            "user",
            message.content,
            limit=40 if is_task_channel(message) else 12,
        )

        mem = load_runtime_memory(session_id)

        if is_task_channel(message):
            summary = generate_thread_brain(ck, mem)
            if summary:
                save_thread_brain(ck, summary)

        ans = call_ovv(ck, message.content, mem)
        await message.channel.send(ans)

    except Exception as e:
        print("[on_message error]", repr(e))
        await message.channel.send("内部エラーが発生しました。")


# ============================================================
# 9. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx):
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx):
    ck = get_context_key(ctx.message)
    summary = load_thread_brain(ck)

    if not summary:
        await ctx.send("thread_brain はまだありません。")
        return

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"
    await ctx.send(f"```json\n{text}\n```")


@bot.command(name="tt")
async def test_thread(ctx):
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if not summary:
        await ctx.send("thread_brain 生成失敗")
        return

    save_thread_brain(ck, summary)
    await ctx.send("test OK: summary saved")


# ============================================================
# 10. Final Boot Log
# ============================================================

print("[DEBUG] Boot complete. PG connected? =", bool(db_pg.PG_CONN))

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
