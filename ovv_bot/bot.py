# bot.py - Ovv Discord Bot (September Stable + JST Boot Log + FINAL-only)

import os
import json
from datetime import datetime, timedelta, timezone
from typing import List

import discord
from discord.ext import commands

from notion_client import Client  # for type hints only

# ============================================================
# DEBUG HOOK
# ============================================================
from debug.debug_router import route_debug_message
from debug.debug_context import debug_context

# ============================================================
# PostgreSQL MODULE（必ず module import）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion API（CRUD は外部モジュール）
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
    notion_client,
)

# ============================================================
# Ovv Core / Call Layer（推論専用）
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
    openai_client,
)

# ============================================================
# Environment
# ============================================================
from config import DISCORD_BOT_TOKEN

print("=== [BOOT] Loading environment variables ===")

# ============================================================
# PostgreSQL Connect + Init
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

# ============================================================
# JST Helper
# ============================================================
JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

BOOT_LOG_CHANNEL_ID = 1446060807044468756  # #boot_log


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
# Boot Log（JST 対応 / Embed）
# ============================================================
async def send_boot_log_embed(bot_client: discord.Client):
    channel = bot_client.get_channel(BOOT_LOG_CHANNEL_ID)
    if channel is None:
        print(f"[BOOT] boot_log channel not found (id={BOOT_LOG_CHANNEL_ID})")
        return

    env_keys = [
        "DISCORD_BOT_TOKEN",
        "OPENAI_API_KEY",
        "NOTION_API_KEY",
        "NOTION_TASKS_DB_ID",
        "NOTION_SESSIONS_DB_ID",
        "NOTION_LOGS_DB_ID",
        "POSTGRES_URL",
    ]
    env_ok = all(os.getenv(k) for k in env_keys)

    pg_ok = bool(db_pg.PG_CONN)
    notion_ok = notion_client is not None
    openai_ok = openai_client is not None
    context_ready = env_ok and pg_ok and notion_ok and openai_ok

    embed = discord.Embed(
        title="Ovv Boot Summary",
        description="起動ログを報告します。",
    )
    embed.add_field(name="ENV", value=str(env_ok), inline=False)
    embed.add_field(name="PostgreSQL", value=str(pg_ok), inline=False)
    embed.add_field(name="Notion", value=str(notion_ok), inline=False)
    embed.add_field(name="OpenAI", value=str(openai_ok), inline=False)
    embed.add_field(name="Context Ready", value=str(context_ready), inline=False)
    embed.timestamp = now_jst()

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print("[BOOT] failed to send boot_log embed:", repr(e))


@bot.event
async def on_ready():
    print(f"[BOOT] Logged in as {bot.user} (id={bot.user.id})")
    try:
        await send_boot_log_embed(bot)
    except Exception as e:
        print("[BOOT] boot_log send error:", repr(e))


# ============================================================
# Runtime Memory / Thread Brain (db_pg Proxy)
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain


# ============================================================
# on_message（DEBUG → Commands → Ovv）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # Bot 自身 / 他 Bot は無視
    if message.author.bot:
        return

    # スレッド作成通知など "通常メッセージ以外" は無視
    if message.type is not discord.MessageType.default:
        return

    # 1. Debug Routing
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # 2. Commands
    if message.content.startswith("!"):
        db_pg.log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # 3. Normal message → Memory → Thread Brain → Ovv
    ck = get_context_key(message)
    session_id = str(ck)

    # User メモリ
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

    # 推論
    ans = call_ovv(ck, message.content, mem)

    # Assistant メモリ
    append_runtime_memory(
        session_id,
        "assistant",
        ans,
        limit=40 if is_task_channel(message) else 12,
    )

    db_pg.log_audit("assistant_reply", {"context_key": ck, "length": len(ans)})

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
# Debug Context Injection
# ============================================================
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

print("[DEBUG] context injection complete.")

# ============================================================
# Run
# ============================================================
print("[BOOT] PG Connected =", bool(db_pg.PG_CONN))
print("[BOOT] Starting Discord Bot (JST boot_log)")
bot.run(DISCORD_BOT_TOKEN)
