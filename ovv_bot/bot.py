# ovv_bot/bot.py  — FINAL-only reply + ignore system messages

import os
import json
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List

from openai import OpenAI
from notion_client import Client

# ============================================================
# Debug Router
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# PG / Notion / ENV
# ============================================================
import database.pg as db_pg
from notion.notion_api import create_task, start_session, end_session, append_logs
from config import DISCORD_BOT_TOKEN, OPENAI_API_KEY, NOTION_API_KEY

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

BOOT_LOG_CHANNEL_ID = 1446060807044468756

# ============================================================
# BootLog Formatter（C-style）
# ============================================================
def format_boot_log(env_ok, pg_ok, notion_ok, openai_ok, ctx_ok):
    ts = datetime.now(timezone.utc).isoformat()
    def _b(v): return "OK" if v else "NG"

    box = [
        "╔══════════════════════════════╗",
        "║        Ovv Boot Summary       ║",
        "║      起動ログを報告します      ║",
        "╠══════════════════════════════╣",
        f"║ ENV: {_b(env_ok):<24}║",
        f"║ PostgreSQL: {_b(pg_ok):<16}║",
        f"║ Notion: {_b(notion_ok):<20}║",
        f"║ OpenAI: {_b(openai_ok):<21}║",
        f"║ Context Ready: {_b(ctx_ok):<13}║",
        "╠══════════════════════════════╣",
        f"║ timestamp: {ts[:19]:<13}║",
        "╚══════════════════════════════╝",
    ]
    return "```\n" + "\n".join(box) + "\n```"


async def send_boot_log(bot):
    await bot.wait_until_ready()

    # ENV checks
    env_ok = all([DISCORD_BOT_TOKEN, OPENAI_API_KEY, NOTION_API_KEY])

    # PG
    pg_ok = bool(db_pg.PG_CONN)

    # Notion
    try:
        notion.users.list()
        notion_ok = True
    except:
        notion_ok = False

    # OpenAI
    try:
        openai_client.models.list()
        openai_ok = True
    except:
        openai_ok = False

    ctx_ok = all([openai_client, notion, db_pg.PG_CONN])

    text = format_boot_log(env_ok, pg_ok, notion_ok, openai_ok, ctx_ok)

    ch = bot.get_channel(BOOT_LOG_CHANNEL_ID)
    if ch:
        await ch.send(text)
    else:
        print("[BOOT_LOG] Channel Not Found")
        print(text)


# ============================================================
# PG Init
# ============================================================
print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)
log_audit = db_pg.log_audit

# Runtime Memory
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# Thread Brain
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Ovv Call Layer
# ============================================================
from ovv.ovv_call import call_ovv

# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# Context Key
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
# on_message — FINAL reply only, ignore system messages
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # ① BOT・Webhook・System はすべて拒否
    if message.author.bot:
        return
    if message.webhook_id:
        return

    # ② システムメッセージは無視（スレッド作成・ピン留めなど）
    if message.type != discord.MessageType.default:
        return

    # Debug Router
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # Commands
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

    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # FINAL 返答のみ
    ans = call_ovv(ck, message.content, mem)
    await message.channel.send(ans)
    return


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
# BootLog Dispatch
# ============================================================
@bot.event
async def on_ready():
    await send_boot_log(bot)


# ============================================================
# Run Bot
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)
