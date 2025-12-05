# bot.py (Ovv Discord Bot - September Stable + Boot Log)

import os
import json
from datetime import datetime, timezone
from typing import Optional, List

import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

# ============================================================
# [DEBUG HOOK]
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# PostgreSQL MODULE IMPORT（module import only）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion Module Import（CRUD は外部モジュール）
#   ※現状このファイル内では未使用だが、将来のために import 済み
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# Ovv Call Layer（Core / External / System Prompt）
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
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
# Runtime Memory (Proxy to db_pg)
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# ============================================================
# Thread Brain（完全外部化済み）
# ============================================================
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

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
# Boot Log Sender
# ============================================================

BOOT_LOG_CHANNEL_ID = 1446060807044468756


async def send_boot_log():
    """起動ログを boot_log チャンネルへ送信。
       チャンネルが見つからない場合は Render ログに警告を出す。
    """
    ch = bot.get_channel(BOOT_LOG_CHANNEL_ID)

    if ch is None:
        # Discord に送れないため Render に必ず通知する
        print(f"[BOOT_LOG WARNING] Boot log channel not found (ID={BOOT_LOG_CHANNEL_ID}).")
        print("[BOOT_LOG WARNING] Please verify channel ID or Discord permissions.")
        return

    # ===== 正常時の動作 =====
    env_ok = all([DISCORD_BOT_TOKEN, OPENAI_API_KEY, NOTION_API_KEY])
    pg_ok = bool(db_pg.PG_CONN)

    # Notion の接続確認
    notion_ok = True
    try:
        notion.users.list()
    except Exception as e:
        notion_ok = False
        print("[BOOT_LOG WARNING] Notion check failed:", repr(e))

    embed = discord.Embed(
        title="Ovv Boot Summary",
        description="起動ログを報告します。",
        color=0x00FFCC,
    )
    embed.add_field(name="ENV", value=str(env_ok))
    embed.add_field(name="PostgreSQL", value=str(pg_ok))
    embed.add_field(name="Notion", value=str(notion_ok))
    embed.add_field(name="OpenAI", value=str(openai_client is not None))
    embed.add_field(name="Context Ready", value=str(pg_ok and notion_ok))

    try:
        await ch.send(embed=embed)
        print("[BOOT_LOG] Sent successfully")
    except Exception as e:
        print("[BOOT_LOG ERROR] Failed to send boot log:", repr(e))


# ============================================================
# Debug Context Injection（September Stable Required）
# ============================================================
from debug.debug_context import debug_context

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
# Events: on_ready / on_message
# ============================================================

@bot.event
async def on_ready():
    print(f"[BOOT] Logged in as {bot.user} (ID: {bot.user.id})")
    # 起動ログを boot_log へ送信
    await send_boot_log()


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

    # Memory & Thread Brain
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

    # Ovv Core Call
    ans = call_ovv(ck, message.content, mem)

    await message.channel.send(ans)


# ============================================================
# Commands (BR / BS / Ping / TT)
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx: commands.Context):
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx: commands.Context):
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
async def test_thread(ctx: commands.Context):
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
