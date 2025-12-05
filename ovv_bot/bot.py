# bot.py - BIS Prototype A1
# Boundary Gate / Interface Box / Stabilizer 入口実装
# ========================================================

import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone

from openai import OpenAI
from notion_client import Client

# ============================================================
# Environment
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# PostgreSQL
# ============================================================
import database.pg as db_pg

print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Ovv Core 呼び出し
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    SYSTEM_PROMPT,
)

# ============================================================
# Debug Context / Router
# ============================================================
from debug.debug_context import debug_context
from debug.debug_router import route_debug_message

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = openai_client
debug_context.load_mem = load_runtime_memory
debug_context.save_mem = save_runtime_memory
debug_context.append_mem = append_runtime_memory
debug_context.brain_gen = generate_thread_brain
debug_context.brain_load = load_thread_brain
debug_context.brain_save = save_thread_brain
debug_context.system_prompt = SYSTEM_PROMPT

# ============================================================
# Notion CRUD
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# Debug boot
# ============================================================
from debug.debug_boot import send_boot_message


# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ============================================================
# BIS: Boundary Gate Helper
# ============================================================
def get_context_key(msg: discord.Message) -> int:
    """スレッド/チャンネル/DM を一意に識別するキー"""
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


def is_task_channel(msg: discord.Message) -> bool:
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return parent and parent.name.lower().startswith("task_")
    return ch.name.lower().startswith("task_")


# ============================================================
# BIS: Stabilizer（出口） FINAL抽出
# ============================================================
def extract_final(raw: str) -> str:
    if not raw:
        return "（出力なし）"

    if "[FINAL]" in raw:
        return raw.split("[FINAL]", 1)[1].strip()

    return raw.strip()


# ============================================================
# Event: on_ready
# ============================================================
@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    await send_boot_message(bot)


# ============================================================
# BIS: on_message（入口 → 中間 → 出口）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # -------------------------------
    # Boundary Gate: フィルタリング
    # -------------------------------
    if message.author.bot:
        return

    if message.type is not discord.MessageType.default:
        return

    # Debug Router
    if await route_debug_message(bot, message):
        return

    # Discord commands (!xxxx)
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # -------------------------------
    # Interface Box: InputPacket形成
    # -------------------------------
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # Task 系は ThreadBrain を更新
    if is_task_channel(message):
        tb = generate_thread_brain(ck, mem)
        if tb:
            save_thread_brain(ck, tb)

    # -------------------------------
    # Ovv Core 呼び出し（推論）
    # -------------------------------
    raw = call_ovv(ck, message.content, mem)

    # -------------------------------
    # Stabilizer
    # -------------------------------
    final_ans = extract_final(raw)
    await message.channel.send(final_ans)


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
# Run
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)