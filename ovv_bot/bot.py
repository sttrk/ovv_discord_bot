# bot.py - Ovv Discord Bot (BIS / 6-Layer / MODULE CONTRACT Edition)

"""
[MODULE CONTRACT]
NAME: bot
ROLE: GATE + IO Adapter
INPUT:
  - Discord Message (discord.Message)
OUTPUT:
  - Discord チャンネルへのメッセージ送信（text）
SIDE EFFECTS:
  - PostgreSQL: runtime_memory append, thread_brain read/write（ただし直接操作は pipeline が担当）
  - Notion: debug_context 用の参照のみ
MUST:
  - 全処理を Boundary_Gate から開始すること
  - Ovv-Core を直接呼ばないこと
  - Stabilizer を経由して Discord に出力すること（実際の呼び出しは pipeline 層に委譲）
MUST NOT:
  - Persistence 層（PG / Notion）へここから直接アクセスしない
  - Core ロジックや推論ロジックをここに書かない
DEPENDENCY:
  - debug_router
  - ovv.bis.boundary_gate.InputPacket / build_input_packet
  - ovv.bis.pipeline.run_ovv_pipeline_from_boundary
"""

# ============================================================
# [BOOT] Environment & Clients
# ============================================================

import os
import json
from datetime import datetime, timezone

from typing import List

import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# [PERSIST] PostgreSQL 接続
# ============================================================
import database.pg as db_pg

print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

# runtime_memory / thread_brain 系は pipeline 側から利用する
log_audit = db_pg.log_audit

# ============================================================
# [DEBUG CONTEXT] Debug 用依存注入
# ============================================================
from debug.debug_context import debug_context

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = openai_client

debug_context.load_mem = db_pg.load_runtime_memory
debug_context.save_mem = db_pg.save_runtime_memory
debug_context.append_mem = db_pg.append_runtime_memory

debug_context.brain_gen = db_pg.generate_thread_brain
debug_context.brain_load = db_pg.load_thread_brain
debug_context.brain_save = db_pg.save_thread_brain

from ovv.ovv_call import (
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

debug_context.ovv_core = OVV_CORE
debug_context.ovv_external = OVV_EXTERNAL
debug_context.system_prompt = SYSTEM_PROMPT

print("[DEBUG] debug_context injection complete.")

# ============================================================
# [DEBUG] Router
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# [GATE] Boundary Gate API
# ============================================================
from ovv.bis.boundary_gate import (
    InputPacket,
    build_input_packet,
)

# ============================================================
# [PIPELINE] Ovv Flow Dispatcher
# ============================================================
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary

# ============================================================
# [BOOT] Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# ============================================================
# [BOOT] 起動メッセージ
# ============================================================
from debug.debug_boot import send_boot_message


@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    await send_boot_message(bot)


# ============================================================
# [GATE] on_message — Discord → Boundary Gate Entry
#  目的:
#    - 生メッセージの検疫
#    - デバッグコマンド・通常コマンドを仕分け
#    - Ovv 圏内へ入る最初の関門
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # -----------------------------------------
    # [GATE] Bot 自身の発言は無視
    # -----------------------------------------
    if message.author.bot:
        return

    # -----------------------------------------
    # [GATE] Discord デフォルト以外の特殊メッセージは破棄
    # -----------------------------------------
    if message.type is not discord.MessageType.default:
        return

    # -----------------------------------------
    # [GATE] Debug router
    # -----------------------------------------
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # -----------------------------------------
    # [GATE/IO] Discord コマンド (!xxx)
    #   - ここから先は commands 担当
    # -----------------------------------------
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # -----------------------------------------
    # [GATE] Boundary_Gate: Discord → InputPacket
    # -----------------------------------------
    packet: InputPacket | None = build_input_packet(message)
    if packet is None:
        # Ovv に回すべきでないメッセージ（空・スタンプなど）
        return

    # -----------------------------------------
    # [PIPELINE] Ovv Main Stream Dispatch
    #  Boundary → Interface → Core → Stabilizer
    # -----------------------------------------
    try:
        final_ans = run_ovv_pipeline_from_boundary(packet)
    except Exception as e:
        final_ans = f"Ovv の処理中に予期しないエラーが発生しました: {e}"

    # -----------------------------------------
    # [IO] Discord に安定化済み出力を送信
    # -----------------------------------------
    if final_ans:
        await message.channel.send(final_ans)


# ============================================================
# Commands（既存のものはここに保持）
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx: commands.Context):
    # ThreadBrain 再生成用（既存ロジックを維持）
    from ovv.bis.boundary_gate import _compute_context_key as _ck_compute

    ck = _ck_compute(ctx.message)
    session_id = str(ck)

    mem = db_pg.load_runtime_memory(session_id)
    summary = db_pg.generate_thread_brain(ck, mem)

    if summary:
        from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

        summary = filter_constraints_from_thread_brain(summary)
        db_pg.save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("thread_brain の再生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx: commands.Context):
    from ovv.bis.boundary_gate import _compute_context_key as _ck_compute

    ck = _ck_compute(ctx.message)
    summary = db_pg.load_thread_brain(ck)

    if not summary:
        await ctx.send("thread_brain はまだありません。")
        return

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    await ctx.send(f"```json\n{text}\n```")


# ============================================================
# [BOOT] Run
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)