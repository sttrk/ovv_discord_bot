# bot.py - Ovv Discord Bot (BIS Integrated Edition / Module-Contracted)

"""
[MODULE CONTRACT]
NAME: bot
ROLE: Boundary_Gate / Dispatcher (B in BIS)

INPUT:
  - Discord Message (discord.Message)
  - Discord Command Context (commands.Context)

OUTPUT:
  - Discord チャンネルへのメッセージ送信（text）
  - PostgreSQL / Notion への副作用（audit_log, runtime_memory, thread_brain 等）

MUST:
  - Discord からの全イベント入力を一元的に受け取り、BIS の流れに沿って処理する:
        Boundary_Gate → Interface_Box → Ovv Core → Stabilizer → Discord
  - runtime_memory / thread_brain などの状態更新を Ovv に隠蔽したまま行う。
  - Ovv Core には常に InputPacket（構造化済み入力）だけを渡す。
  - Stabilizer から返ってきた最終テキストのみを Discord に送信する。
  - デバッグ系 (!dbg …) は必ず debug_router に委譲し、ここでは個別実装しない。

MUST NOT:
  - Ovv Core の出力内容を書き換えない。
  - 推論ロジックを実装しない。
  - Notion・PG のデータ構造を bot.py 内で設計・変更しない。
  - Interface_Box / Stabilizer / ThreadBrain / Constraint Filter の内部仕様に依存するコードを持たない。

BOUNDARY:
  - このモジュールは BIS の「B」層であり、入口処理・分岐処理のみ担当する。
"""

import os
import json
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
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
# PostgreSQL Module
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
# Ovv Core / BIS
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

from ovv.bis.boundary_gate import build_input_packet as build_boundary_packet
from ovv.bis.interface_box import build_input_packet as build_interface_packet
from ovv.bis.stabilizer import extract_final_answer
from ovv.bis.state_manager import decide_state

# ★ 追加修正ポイント（TB v3 正規化用）
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

# ============================================================
# Debug Context
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
# Debug Router
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# Notion API Wrapper
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
        return bool(parent and parent.name.lower().startswith("task_"))
    return ch.name.lower().startswith("task_")

# ============================================================
# on_ready
# ============================================================
@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    await send_boot_message(bot)

# ============================================================
# on_message — Boundary_Gate
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    if message.type is not discord.MessageType.default:
        return

    handled = await route_debug_message(bot, message)
    if handled:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    boundary_packet = build_boundary_packet(message)
    if boundary_packet is None:
        return

    context_key = boundary_packet.context_key
    session_id = boundary_packet.session_id
    is_task = boundary_packet.is_task_channel
    user_text = boundary_packet.text

    append_runtime_memory(
        session_id,
        "user",
        user_text,
        limit=40 if is_task else 12,
    )

    mem = load_runtime_memory(session_id)

    tb_summary = None
    if is_task:
        tb_summary = generate_thread_brain(context_key, mem)
        if tb_summary:
            # ★ 修正（TB v3 正規化）
            tb_summary = filter_constraints_from_thread_brain(tb_summary)
            save_thread_brain(context_key, tb_summary)
    else:
        tb_summary = load_thread_brain(context_key)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)

    state_hint = decide_state(
        context_key=context_key,
        user_text=user_text,
        recent_mem=mem,
        task_mode=is_task,
    )

    input_packet = build_interface_packet(
        user_text=user_text,
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    raw_ans = call_ovv(context_key, input_packet)

    final_ans = extract_final_answer(raw_ans)

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    await message.channel.send(final_ans)

# ============================================================
# Commands
# ============================================================
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx: commands.Context):
    boundary_packet = build_boundary_packet(ctx.message)
    if boundary_packet is None:
        await ctx.send("このメッセージから context_key を生成できません。")
        return

    ck = boundary_packet.context_key
    session_id = boundary_packet.session_id

    mem = load_runtime_memory(session_id)
    summary = generate_thread_brain(ck, mem)

    if summary:
        # ★ 修正（TB v3 正規化）
        summary = filter_constraints_from_thread_brain(summary)
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("thread_brain の再生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx: commands.Context):
    ck = get_context_key(ctx.message)
    summary = load_thread_brain(ck)

    if not summary:
        await ctx.send("thread_brain はまだありません。")
        return

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    await ctx.send(f"```json\n{text}\n```")


@bot.command(name="test_thread")
async def test_thread(ctx: commands.Context):
    boundary_packet = build_boundary_packet(ctx.message)
    if boundary_packet is None:
        await ctx.send("このメッセージから context_key を生成できません。")
        return

    ck = boundary_packet.context_key
    session_id = boundary_packet.session_id

    mem = load_runtime_memory(session_id)
    summary = generate_thread_brain(ck, mem)

    if not summary:
        await ctx.send("thread_brain 生成失敗")
        return

    # ★ 修正（TB v3 正規化）
    summary = filter_constraints_from_thread_brain(summary)
    save_thread_brain(ck, summary)

    await ctx.send("test OK: summary saved")

# ============================================================
# Run
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)