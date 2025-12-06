# bot.py - Ovv Discord Bot (BIS Integrated Edition + Constraint_Filter v2.0)

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
# Ovv Core 呼び出しレイヤ / BIS レイヤ
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

from ovv.bis.interface_box import build_input_packet
from ovv.bis.stabilizer import extract_final_answer
from ovv.bis.state_manager import decide_state
from ovv.bis.constraint_filter import apply_constraint_filter

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
    await send_boot_message(bot)


# ============================================================
# Event: on_message（Boundary_Gate / BIS 統合）
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

    # ③ 通常メッセージ → Boundary_Gate
    ck = get_context_key(message)
    session_id = str(ck)

    # runtime_memory: user 追記
    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # Task チャンネルでは thread_brain を更新、それ以外は最新をロード
    tb_summary = None
    if is_task_channel(message):
        tb_summary = generate_thread_brain(ck, mem)
        if tb_summary:
            save_thread_brain(ck, tb_summary)
    else:
        tb_summary = load_thread_brain(ck)

    # 軽量ステート決定（数字カウントゲームなどの状態ヒント）
    state_hint = decide_state(
        context_key=ck,
        user_text=message.content,
        recent_mem=mem,
        task_mode=is_task_channel(message),
    )

    # ---------- Constraint_Filter（曖昧入力の除去） ----------
    filter_result = apply_constraint_filter(
        user_text=message.content,
        runtime_memory=mem,
        thread_brain=tb_summary,
    )

    if filter_result["status"] == "reject":
        # 破棄: Core には流さない
        await message.channel.send(filter_result["message"])
        return

    if filter_result["status"] == "clarify":
        # 追加情報要求: Core には流さない
        await message.channel.send(filter_result["message"])
        return

    # status == ok → 安全なテキストを使用
    safe_text = filter_result["clean_text"]

    # Interface_Box: InputPacket 構築
    input_packet = build_input_packet(
        user_text=safe_text,
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    # Ovv Core 呼び出し（InputPacket）
    raw_ans = call_ovv(ck, input_packet)

    # Stabilizer: FINAL 抽出
    final_ans = extract_final_answer(raw_ans)

    # 念のため空防止
    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

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