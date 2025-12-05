# bot.py - Ovv Discord Bot (September Stable + Boot Summary + FINAL-only)

import json
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands

import database.pg as db_pg
from notion.notion_api import create_task, start_session, end_session, append_logs
from debug.debug_router import route_debug_message
from debug.debug_boot import send_boot_message

from config import (
    DISCORD_BOT_TOKEN,
    NOTION_API_KEY,
)

from notion_client import Client

# Ovv コア呼び出し
from ovv.ovv_call import call_ovv, OVV_CORE, OVV_EXTERNAL, SYSTEM_PROMPT

# ============================================================
# Notion Client
# ============================================================
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# PostgreSQL Init
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

# Runtime Memory proxy
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# Thread Brain proxy
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
# on_ready → Boot Summary 送信
# ============================================================
@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user} (id={bot.user.id})")
    await send_boot_message(bot)


# ============================================================
# on_message（DEBUG HOOK → Ovv）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # Bot 自身 / 他の Bot は無視
    if message.author.bot:
        return

    # スレッド作成メッセージなど、システムメッセージは無視
    if message.type not in (
        discord.MessageType.default,
        discord.MessageType.reply,
    ):
        return

    # 空メッセージ（添付のみ等）は無視
    if not message.content:
        return

    # Debug ルーティング（!dbg ...）
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # 通常コマンド（!ping / !tt など）
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # Ovv 本体フロー
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
# Commands (BR / BS / TT / ping)
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
from debug.debug_context import debug_context

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = None  # ovv_call 内で管理

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
print("[BOOT] PG Connected =", bool(db_pg.PG_CONN))
print("[BOOT] Starting Discord Bot")

bot.run(DISCORD_BOT_TOKEN)
