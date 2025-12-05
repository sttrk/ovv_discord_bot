# bot.py - Ovv Discord Bot (A4-R3 Stable Edition)

import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List

# ============================================================
# Debug Router
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# PostgreSQL (module import ONLY)
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion API
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# Ovv Call Layer (A4-R3)
# ============================================================
from ovv.ovv_call import call_ovv     # ← A4-R3 context-aware

# ============================================================
# Environment
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

from openai import OpenAI
from notion_client import Client

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)


# ============================================================
# PostgreSQL Init
# ============================================================
print("=== [BOOT] PostgreSQL Connecting ===")
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
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = False

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
# Boot Log (boot_log チャンネルへ通知)
# ============================================================

BOOTLOG_CHANNEL_ID = 1446060807044468756   # 固定

async def send_boot_log(bot):
    ch = bot.get_channel(BOOTLOG_CHANNEL_ID)

    env_ok = True
    pg_ok = bool(db_pg.PG_CONN)
    notion_ok = notion is not None
    openai_ok = openai_client is not None
    ctx_ok = all([
        load_runtime_memory,
        save_runtime_memory,
        generate_thread_brain,
    ])

    msg = (
        "**Ovv Boot Summary**\n"
        "\n"
        "起動ログを報告します。\n\n"
        f"**ENV**\n{env_ok}\n\n"
        f"**PostgreSQL**\n{pg_ok}\n\n"
        f"**Notion**\n{notion_ok}\n\n"
        f"**OpenAI**\n{openai_ok}\n\n"
        f"**Context Ready**\n{ctx_ok}\n\n"
        f"`timestamp: {datetime.now(timezone.utc).isoformat()}`"
    )

    if ch is None:
        print("[BOOTLOG] boot_log channel NOT FOUND.")
        return

    try:
        await ch.send(msg)
    except Exception as e:
        print("[BOOTLOG ERROR]", repr(e))


# ============================================================
# FINAL ONLY フィルタ（UI版と同等）
# ============================================================
def extract_final(text: str) -> str:
    """
    Ovvは FINALブロックのみ返答する（UI版互換）
    """
    if "FINAL:" not in text:
        return text.strip()

    # FINAL: xxx のみ抽出
    out = []
    for line in text.splitlines():
        if line.startswith("FINAL:"):
            out.append(line.replace("FINAL:", "").strip())
    return "\n".join(out).strip()


# ============================================================
# Event Hooks
# ============================================================

@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user}")
    await send_boot_log(bot)


@bot.event
async def on_thread_create(thread: discord.Thread):
    # thread create に反応しない（UI版互換）
    return


@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    # Debug Hook
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Commands
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # --- Main Ovv Flow ---
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # task thread → thread_brain 更新
    summary = None
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)
    else:
        summary = load_thread_brain(ck)

    ans = call_ovv(ck, message.content, mem, summary)

    # FINAL 抽出
    ans = extract_final(ans)

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
        text = text[:1900] + "...\n[truncated]"

    await ctx.send(f"```json\n{text}\n```")


@bot.command(name="tt")
async def test_thread(ctx):
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))

    summary = generate_thread_brain(ck, mem)
    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("test OK: summary saved")
    else:
        await ctx.send("thread_brain 生成失敗")


# ============================================================
# RUN
# ============================================================
print("[BOOT] Starting Discord Bot...")
bot.run(DISCORD_BOT_TOKEN)
