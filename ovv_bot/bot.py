# bot.py - Ovv Discord Bot (Stable Full Edition A4-R3)

import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import List

from openai import OpenAI
from notion_client import Client

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
# PostgreSQL Module（絶対に from-import しない）
# ============================================================
import database.pg as db_pg


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

# Thread Brain
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain


# ============================================================
# Ovv Core 呼び出しレイヤ（これは PG 初期化後に読み込む）
# ============================================================
from ovv.ovv_call import call_ovv


# ============================================================
# Debug Router（※必ず Ovv / PG / Notion 初期化後にロード）
# ============================================================
from debug.debug_router import route_debug_message


# ============================================================
# Notion CRUD（循環依存なし）
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)


# ============================================================
# Boot Log
# ============================================================
BOOT_CHANNEL_ID = 1446060807044468756

async def send_boot_log(bot: commands.Bot):
    ch = bot.get_channel(BOOT_CHANNEL_ID)
    ts = datetime.now(timezone.utc).isoformat()

    ENV_OK = True
    PG_OK = bool(db_pg.PG_CONN)
    NOTION_OK = notion is not None
    OPENAI_OK = openai_client is not None

    CTX_OK = all([
        load_runtime_memory,
        save_runtime_memory,
        append_runtime_memory,
        load_thread_brain,
        save_thread_brain,
        generate_thread_brain,
    ])

    text = (
        "Ovv Boot Summary\n\n"
        "起動ログを報告します。\n\n"
        "**ENV**\n"
        f"{ENV_OK}\n\n"
        "**PostgreSQL**\n"
        f"{PG_OK}\n\n"
        "**Notion**\n"
        f"{NOTION_OK}\n\n"
        "**OpenAI**\n"
        f"{OPENAI_OK}\n\n"
        "**Context Ready**\n"
        f"{CTX_OK}\n\n"
        f"`timestamp: {ts}`"
    )

    if ch:
        await ch.send(text)
    else:
        print("[BOOT_LOG] Channel not found. Skip.")


# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ============================================================
# Context Key
# ============================================================
def get_context_key(msg: discord.Message) -> int:
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


# Task channel?
def is_task_channel(msg: discord.Message) -> bool:
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return parent and parent.name.lower().startswith("task_")
    return ch.name.lower().startswith("task_")


# ============================================================
# Event: on_ready
# ============================================================
@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    await send_boot_log(bot)


# ============================================================
# Event: on_message（Final Only 対応 Call → Filter）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # Debug Router
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Command
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # 通常メッセージ
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12
    )

    mem = load_runtime_memory(session_id)

    # Thread Brain 更新
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # Ovv Core 呼び出し（Final only）
    raw_ans = call_ovv(ck, message.content, mem)

    # Final Only フィルタリング
    final_ans = raw_ans
    if "[FINAL]" in raw_ans:
        final_ans = raw_ans.split("[FINAL]", 1)[1].strip()

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
