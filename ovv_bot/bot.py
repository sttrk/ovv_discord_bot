# ovv_bot/bot.py  (September Stable Edition – Phase 2-A)
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
# PostgreSQL MODULE IMPORT（絶対 from-import しない）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion CRUD 外部化
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

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
# Ovv Core Loader
# ============================================================
from ovv.core_loader import load_core, load_external
from ovv.ovv_call import call_ovv   # ← 新 ovv_call を正式使用

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary
2. MUST use Clarify only when needed
3. MUST avoid hallucination
4. MUST respect boundaries
5. SHOULD decompose → reconstruct
6. MUST NOT phase-mix
7. MAY trigger CDC sparingly
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上の Ovv です。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()

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
# on_message（DEBUG HOOK → Memory → Ovv）
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

    # ===== Memory update =====
    ck = get_context_key(message)
    session_id = str(ck)

    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # ===== Thread Brain update =====
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # ===== Ovv Core Call (完全外部化版) =====
    ans = call_ovv(
        openai_client=openai_client,
        system_prompt=SYSTEM_PROMPT,
        core_text=OVV_CORE,
        external_text=OVV_EXTERNAL,
        context_key=ck,
        text=message.content,
        recent_mem=mem,
        append_runtime_memory=append_runtime_memory,
        log_audit=log_audit,
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
