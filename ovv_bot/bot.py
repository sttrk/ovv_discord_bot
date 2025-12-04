# ovv_bot/bot.py
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
# PostgreSQL MODULE IMPORT（絶対に from-import しない）
# ============================================================
import database.pg as db_pg

# ============================================================
# Notion Module Import（完全外部化）
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# OVV CALL IMPORT（新設）
# ============================================================
from ovv.ovv_call import call_ovv   # ← ここが今回の重要変更ポイント

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
# Runtime Memory（PG に完全外部化）
# ============================================================
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# ============================================================
# Thread Brain（PG に完全外部化）
# ============================================================
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# Ovv Core Loader
# ============================================================
from ovv.core_loader import load_core, load_external
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
# on_message（DEBUG HOOK → Ovv ルーティング）
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

    # Thread Context Key
    ck = get_context_key(message)
    session_id = str(ck)

    # Runtime Memory 追記
    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # Thread Brain 更新（task_ 系のみ）
    if is_task_channel(message):
        summary = generate_thread_brain(ck, mem)
        if summary:
            save_thread_brain(ck, summary)

    # ========================================================
    # Ovv Core Call（今回の変更点：完全外部化 call_ovv を利用）
    # ========================================================
    ans = call_ovv(
        openai_client=openai_client,
        system_prompt=SYSTEM_PROMPT,
        core=OVV_CORE,
        external=OVV_EXTERNAL,
        recent_mem=mem,
        context_key=ck,
        user_text=message.content,
    )

    if ans is None:
        log_audit("openai_error", {"context_key": ck})
        await message.channel.send("Ovv との通信中にエラーが発生しました。")
        return

    # メモリ保存（副作用）
    append_runtime_memory(str(ck), "assistant", ans)
    log_audit("assistant_reply", {"context_key": ck})

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
# Boot Complete
# ============================================================
print("[BOOT] PG Connected =", bool(db_pg.PG_CONN))
print("[BOOT] Starting Discord Bot")

bot.run(DISCORD_BOT_TOKEN)
