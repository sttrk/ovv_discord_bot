# ==============================================
# bot.py - JST対応 / FINALのみ / boot_log安定版
# ==============================================

import os
import json
import discord
from discord.ext import commands
from typing import Optional, List
from datetime import datetime, timedelta, timezone

# JST定義
JST = timezone(timedelta(hours=9))
def now_jst():
    return datetime.now(JST)

# ============================================================
# Imports
# ============================================================
from openai import OpenAI
from notion_client import Client
from debug.debug_router import route_debug_message

# PostgreSQL 管理
import database.pg as db_pg

# Notion CRUD
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# Ovv Call Layer
from ovv.ovv_call import call_ovv, SYSTEM_PROMPT, OVV_CORE, OVV_EXTERNAL

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
# Runtime Memory / Thread Brain (Proxy to db_pg)
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain


# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

BOOT_LOG_CHANNEL_ID = 1446060807044468756


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
# on_message（FINALのみ出力）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # スレッド作成通知などを無視
    if message.type != discord.MessageType.default:
        return

    # Debug
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Command
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # Memory
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # Thread Brain
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # Ovv Core
    raw_ans = call_ovv(ck, message.content, mem)

    # FINALのみ抽出
    final_ans = raw_ans
    if "[FINAL]" in raw_ans:
        final_ans = raw_ans.split("[FINAL]", 1)[1].strip()

    await message.channel.send(final_ans)


# ============================================================
# Commands (BR / BS / Ping)
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
# Boot Log (JST対応)
# ============================================================
async def send_boot_log():
    channel = bot.get_channel(BOOT_LOG_CHANNEL_ID)
    ts = now_jst().isoformat()

    msg = f"""
Ovv Boot Summary

起動ログを報告します。

**ENV**
True

**PostgreSQL**
{db_pg.PG_CONN is not None}

**Notion**
{notion is not None}

**OpenAI**
{openai_client is not None}

**Context Ready**
{all([OVV_CORE, OVV_EXTERNAL, SYSTEM_PROMPT])}

`timestamp: {ts}`
""".strip()

    if channel:
        await channel.send(msg)
    else:
        print("[BOOT_LOG ERROR] boot_log channel not found")


@bot.event
async def on_ready():
    await send_boot_log()
    print("=== Ovv Bot Ready ===")


# ============================================================
# Run Bot
# ============================================================
bot.run(DISCORD_BOT_TOKEN)
