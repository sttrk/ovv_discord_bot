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
# Ovv Core 呼び出しレイヤ（PG 初期化後に読み込む）
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

# ============================================================
# Debug Context Injection（必須：debug_commands の cfg 用）
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

print("[DEBUG] debug_context injection complete.")

# ============================================================
# Debug Router（※必ず context injection 後にロード）
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
# Boot Log（debug_boot に一本化）
# ============================================================
from debug.debug_boot import send_boot_message

# ============================================================
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# ============================================================
# Context Key utilities
# ============================================================
def get_context_key(msg: discord.Message) -> int:
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
# Event: on_ready（boot_log 送信）
# ============================================================
@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    # boot_log 送信は debug_boot 側に委譲
    await send_boot_message(bot)


# ============================================================
# Event: on_message（Final Only & system message filter）
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # Bot 自身・他の Bot には反応しない
    if message.author.bot:
        return

    # スレッド作成通知など「通常メッセージ以外」には反応しない
    if message.type is not discord.MessageType.default:
        return

    # ① Debug Router
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # ② コマンド（! で始まるもの）
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # ③ 通常メッセージ → Ovv 推論
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # Task チャンネルでは thread_brain を更新
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # Ovv Core 呼び出し
    raw_ans = call_ovv(ck, message.content, mem)

    # FINAL 以外を切り落とすフィルタ
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
