# bot.py - Ovv Discord Bot (September Stable Edition - Cycle Free)

import os
import json
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

# ============================================================
# DEBUG ROUTER
# ============================================================
from debug.debug_router import route_debug_message

# Boot Log 用
from debug.debug_boot import send_boot_message

# ============================================================
# PostgreSQL MODULE（絶対に from-import しない）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion API（循環依存なし版）
# ============================================================
import notion.notion_api as notion_api

# ============================================================
# Environment Variables
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
    NOTION_TASKS_DB_ID,
    NOTION_SESSIONS_DB_ID,
    NOTION_LOGS_DB_ID,
)

# ============================================================
# OpenAI / Notion Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion_client = Client(auth=NOTION_API_KEY)

# Notion API にクライアントを注入
notion_api.inject_notion_client(notion_client)

# ============================================================
# PostgreSQL Init
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

# Notion API に log_audit を注入
notion_api.log_audit = log_audit

# ============================================================
# Runtime Memory（PG Proxy）
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# ============================================================
# Thread Brain（PG Proxy）
# ============================================================
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Ovv Call Layer
# ============================================================
from ovv.ovv_call import call_ovv, OVV_CORE, OVV_EXTERNAL, SYSTEM_PROMPT

# ============================================================
# Debug Context Injection
# ============================================================
from debug.debug_context import debug_context

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion_client
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

print("[DEBUG] debug_context injection complete.")

# ============================================================
# Discord Setup
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
        return parent and parent.name.lower().startswith("task_")
    return ch.name.lower().startswith("task_")


# ============================================================
# on_ready（Boot Log）
# ============================================================
@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await send_boot_message(bot)
    except Exception as e:
        print("[BOOT] send_boot_message failed:", repr(e))


# ============================================================
# on_message（DEBUG → Command → Ovv）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # Debug Layer
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Command Layer
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # Ovv Layer
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
# Commands
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
# Boot Complete
# ============================================================
print("[BOOT] PG Connected =", bool(db_pg.PG_CONN))
print("[BOOT] Starting Discord Bot")

bot.run(DISCORD_BOT_TOKEN)
