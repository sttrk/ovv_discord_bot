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
  - runtime_memory / thread_brain などの状態更新を **Ovv に隠蔽したまま** 行う。
  - Ovv Core には常に InputPacket（構造化済み入力）だけを渡す。
  - Stabilizer から返ってきた **最終テキストのみ** を Discord に送信する。
  - デバッグ系 (!dbg …) は必ず debug_router に委譲し、ここでは個別実装しない。

MUST NOT:
  - Ovv Core の出力内容を書き換えない（文意の改変・再生成・補完は禁止）。
  - 推論ロジックを実装しない（意味解釈・方針決定は Ovv / ThreadBrain に委譲）。
  - Notion・PG のデータ構造をこのファイル内で設計・変更しない（I/O 呼び出しのみ）。
  - Interface_Box / Stabilizer / ThreadBrain / Constraint Filter の内部仕様に依存する分岐を書かない。

BOUNDARY:
  - このモジュールは BIS の「B（Boundary_Gate）」層および Discord Command Dispatcher としてのみ振る舞う。
  - Interface_Box / Stabilizer / Ovv Core / Storage（PG・Notion）との境界を厳守し、
    それらの内部構造に依存するロジックを持たない。
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

# Thread Brain（PG 内蔵版）
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

# BIS: Boundary_Gate / Interface_Box / Stabilizer / State_Manager
# - NOTE:
#   - boundary_gate: Discord Message → 標準化 InputPacket（入口）
#   - interface_box: ThreadBrain / RuntimeMemory / State → OvvInputPacket（中間）
#   - stabilizer   : [FINAL] 抽出（出口）
#   - state_manager: 軽量ステート推定（中間）
from ovv.bis.boundary_gate import build_input_packet as build_boundary_packet
from ovv.bis.interface_box import build_input_packet as build_interface_packet
from ovv.bis.stabilizer import extract_final_answer
from ovv.bis.state_manager import decide_state

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
# Notion CRUD（循環依存なし / I/O 専用）
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
    """
    Discord メッセージから context_key を一意に算出する。
    - スレッド: thread.id
    - DM: channel.id
    - ギルドチャンネル: (guild.id << 32) | channel.id
    """
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


def is_task_channel(msg: discord.Message) -> bool:
    """
    「task_」プレフィックスでタスクチャンネルとみなす簡易判定。
    スレッドの場合は parent.name を見る。
    """
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return bool(parent and parent.name.lower().startswith("task_"))
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

    # ① Debug Router（!dbg ...）
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # ② コマンド（! で始まるもの）→ Discord Commands に委譲
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # ③ 通常メッセージ → Boundary_Gate 処理開始
    ck = get_context_key(message)
    session_id = str(ck)

    # --- Runtime Memory: user 追記 ---
    append_runtime_memory(
        session_id,
        "user",
        message.content,
        limit=40 if is_task_channel(message) else 12,
    )

    mem = load_runtime_memory(session_id)

    # --- Thread Brain 更新 / 取得 ---
    tb_summary = None
    if is_task_channel(message):
        # Task チャンネルでは毎回更新
        tb_summary = generate_thread_brain(ck, mem)
        if tb_summary:
            save_thread_brain(ck, tb_summary)
    else:
        # 通常チャンネルは既存 TB があれば利用
        tb_summary = load_thread_brain(ck)

    # --- 軽量ステート決定（数字カウント等の simple_sequence 含む） ---
    state_hint = decide_state(
        context_key=ck,
        user_text=message.content,
        recent_mem=mem,
        task_mode=is_task_channel(message),
    )

    # --- Interface_Box: InputPacket 構築 ---
    input_packet = build_interface_packet(
        user_text=message.content,
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    # --- Ovv Core 呼び出し（InputPacket） ---
    raw_ans = call_ovv(ck, input_packet)

    # --- Stabilizer: [FINAL] 抽出 & 崩れ補正 ---
    final_ans = extract_final_answer(raw_ans)

    # 念のため空防止（Stabilizer 側で全落ちした場合のフェイルセーフ）
    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    await message.channel.send(final_ans)


# ============================================================
# Commands（BIS 外だが、Boundary_Gate の一部として扱う）
# ============================================================
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx: commands.Context):
    """
    現在 context_key の runtime_memory から thread_brain を再生成して保存。
    """
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx: commands.Context):
    """
    現在 context_key に紐づく thread_brain を JSON で表示。
    """
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
async def test_thread(ctx: commands.Context):
    """
    テスト用：現在 context_key の runtime_memory から thread_brain を生成し、
    保存できるかどうかを確認する。
    """
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