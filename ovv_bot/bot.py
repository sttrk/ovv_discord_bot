# ovv_bot/bot.py
# Ovv Discord Bot - September Stable Edition + State Manager v1 Integrated

import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List

from openai import OpenAI
from notion_client import Client

# ============================================================
# [DEBUG HOOK]
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# PostgreSQL MODULE IMPORT（絶対に from-import しない）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion Module Import（完全分離済み）
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# Environment Variables
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# PostgreSQL Init
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

# ============================================================
# Runtime Memory (Proxy)
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# ============================================================
# Thread Brain
# ============================================================
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Ovv Core / External / Call
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

# ============================================================
# State Manager（軽量ステートマシン）
# ============================================================
from ovv.state_manager import decide_state

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
        return parent.name.lower().startswith("task_") if parent else False
    return ch.name.lower().startswith("task_")


# ============================================================
# Boot Log Sender（手動トリガー方式）
# ============================================================

BOOT_LOG_CHANNEL_ID = 1446060807044468756  # ←指定されたチャンネル

async def send_boot_log(bot: discord.Client):
    now = datetime.now(timezone.utc).isoformat()
    pg_ok = bool(db_pg.PG_CONN)

    msg = (
        "【Ovv Bot boot_log】\n"
        f"- time: {now}\n"
        f"- PG: {'OK' if pg_ok else 'NG'}\n"
        f"- Notion: {'OK' if notion is not None else 'NG'}\n"
    )

    ch = bot.get_channel(BOOT_LOG_CHANNEL_ID)

    if ch is None:
        print("[BOOT] boot_log channel not found")
        return

    try:
        await ch.send(msg)
    except Exception as e:
        print("[BOOT] Failed to send boot_log:", repr(e))


# ============================================================
# on_ready（boot_log 出力）
# ============================================================
@bot.event
async def on_ready():
    print("[BOOT] Discord Connected. Sending boot_log...")
    await send_boot_log(bot)


# ============================================================
# on_message（DEBUG → MEMORY → STATE → Ovv）
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

    # ======================================================
    # Memory
    # ======================================================
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)
    task_mode = is_task_channel(message)

    # ======================================================
    # Thread Brain（task_ チャンネルでのみ作動）
    # ======================================================
    if task_mode:
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # ======================================================
    # State Manager（数字カウントなど軽量ステート）
    # ======================================================
    state_hint = decide_state(
        context_key=ck,
        user_text=message.content,
        recent_mem=mem,
        task_mode=task_mode,
    )

    # ======================================================
    # Ovv Core
    # ======================================================
    ans = call_ovv(
        context_key=ck,
        text=message.content,
        recent_mem=mem,
        state_hint=state_hint,
    )

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
