# ovv_bot/bot.py
import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List

# ============================================================
# ENV
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

# ============================================================
# External Modules
# ============================================================
from openai import OpenAI
from notion_client import Client

# Debug Router
from debug.debug_router import route_debug_message
from debug.debug_context import debug_context

# PostgreSQL Module (絶対に from-import しない)
import database.pg as db_pg

# Notion CRUD (分離版)
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# Ovv Call Layer
from ovv.ovv_call import call_ovv, OVV_CORE, OVV_EXTERNAL, SYSTEM_PROMPT

# ============================================================
# Client Init
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# PostgreSQL Boot
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")

conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

# Runtime Memory
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# Thread-brain
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Inject Debug Context
# ============================================================
debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = openai_client

debug_context.load_mem = load_runtime_memory
debug_context.save_mem = save_runtime_memory
debug_context.append_mem = append_runtime_memory

debug_context.brain_gen = generate_thread_brain
debug_context.brain_load = load_thread_brain
debug_context.brain_save = save_thread_brain

debug_context.ovv_core = OVV_CORE
debug_context.ovv_external = OVV_EXTERNAL
debug_context.system_prompt = SYSTEM_PROMPT

print("[DEBUG] context injection complete.")


# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ============================================================
# ContextKey / Task-Channel 判定
# ============================================================
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
# IGNORE BOT-UNRELATED EVENTS
# ============================================================
IGNORED_MESSAGE_TYPES = {
    discord.MessageType.thread_created,
    discord.MessageType.thread_starter_message,
    discord.MessageType.recipient_add,
    discord.MessageType.recipient_remove,
    discord.MessageType.call,
    discord.MessageType.channel_name_change,
    discord.MessageType.channel_icon_change,
    discord.MessageType.pins_add,
}


def should_ignore_message(msg: discord.Message) -> bool:
    if msg.type in IGNORED_MESSAGE_TYPES:
        return True
    return False


# ============================================================
# on_ready → BootLog (Cフォーマット)
# ============================================================
BOOT_LOG_CHANNEL_ID = 1446060807044468756

@bot.event
async def on_ready():
    print("[STATUS] on_ready triggered")

    status = {
        "env": True,
        "pg": bool(db_pg.PG_CONN),
        "notion": True,
        "openai": True,
        "context": all([
            debug_context.pg_conn,
            debug_context.notion,
            debug_context.openai_client,
            debug_context.ovv_core,
            debug_context.ovv_external,
            debug_context.system_prompt
        ])
    }

    msg = (
        "【Ovv Bot boot_log】\n"
        f"- time: {datetime.now(timezone.utc).isoformat()}\n"
        f"- PG: {'OK' if status['pg'] else 'NG'}\n"
        f"- Notion: {'OK' if status['notion'] else 'NG'}"
    )

    ch = bot.get_channel(BOOT_LOG_CHANNEL_ID)
    if ch:
        await ch.send(msg)
    else:
        print(f"[BOOT_LOG] Channel Not Found: {BOOT_LOG_CHANNEL_ID}")


# ============================================================
# on_message
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # thread 作成通知などを無視
    if should_ignore_message(message):
        return

    # Debug layer
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Commands
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # Normal Ovv Message Flow
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


# ============================================================
# Commands (Ping / BR / BS / TT)
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
# Run
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)
