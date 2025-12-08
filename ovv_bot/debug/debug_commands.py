"""
[MODULE CONTRACT]
NAME: debug_commands
ROLE: Debug Command Handler (Gate-Assist)

INPUT:
  - bot (discord.ext.commands.Bot)
OUTPUT:
  - Discord messages（人間可読な debug 出力）

MUST:
  - debug_router から呼び出される前提で安全に動作
  - PG / TB / Memory の read-only 操作が中心
  - 破壊操作がある場合は wipe のみ許可
  - 主処理の中で Core / Interface を呼ばない
  - BIS のレイヤーと責務を侵害しない

MUST NOT:
  - Ovv Core を呼ぶ / LLM を叩く
  - Interface_Box / Stabilizer / Boundary_Gate を呼ぶ
  - ThreadBrain の構造を勝手に変える
"""

import json
import discord
from discord.ext import commands

import database.pg as db_pg

# BIS logger（内部用）
from ovv.bis.bis_logger import gate as log_gate
from ovv.bis.bis_logger import persist as log_persist


# ============================================================
# 追加：IFACE パケット保存（debug 用）
# pipeline.py から set_latest_iface(packet) が呼ばれる
# ============================================================

_latest_iface_packet = None

def set_latest_iface(packet: dict):
    global _latest_iface_packet
    _latest_iface_packet = packet


# ============================================================
# register_debug_commands
# ============================================================

def register_debug_commands(bot: commands.Bot):

    # ------------------------------------------------------------
    # !bs — Boot Summary（環境と接続状況）
    # ------------------------------------------------------------
    @bot.command(name="bs")
    async def bs(ctx: commands.Context):
        env_ok = bool(db_pg.PG_URL)
        pg_ok = db_pg.conn is not None

        text = [
            "Ovv Boot Summary (Debug Mode)",
            "",
            f"ENV Loaded: {env_ok}",
            f"PostgreSQL Connected: {pg_ok}",
            "",
            f"session_id: {ctx.channel.id}",
        ]
        await ctx.send("```\n" + "\n".join(text) + "\n```")

    # ------------------------------------------------------------
    # !br — Thread Brain dump
    # ------------------------------------------------------------
    @bot.command(name="br")
    async def br(ctx: commands.Context):
        context_key = ctx.channel.id
        tb = db_pg.load_thread_brain(context_key)

        if not tb:
            await ctx.send("ThreadBrain: (none)")
            return

        out = json.dumps(tb, ensure_ascii=False, indent=2)
        out = out[:1900]
        await ctx.send(f"```\n{out}\n```")

    # ------------------------------------------------------------
    # !dbg_mem — runtime_memory dump
    # ------------------------------------------------------------
    @bot.command(name="dbg_mem")
    async def dbg_mem(ctx: commands.Context):
        session_id = str(ctx.channel.id)
        mem = db_pg.load_runtime_memory(session_id)

        if not mem:
            await ctx.send("runtime_memory: (empty)")
            return

        out = json.dumps(mem, ensure_ascii=False, indent=2)
        out = out[:1900]
        await ctx.send(f"```\n{out}\n```")

    # ------------------------------------------------------------
    # !dbg_all — TB + memory のまとめ
    # ------------------------------------------------------------
    @bot.command(name="dbg_all")
    async def dbg_all(ctx: commands.Context):
        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        tb = db_pg.load_thread_brain(context_key)
        mem = db_pg.load_runtime_memory(session_id)

        text = [
            "=== DEBUG ALL ===",
            "",
            "[ThreadBrain]",
            json.dumps(tb, ensure_ascii=False, indent=2)[:800] if tb else "(none)",
            "",
            "[RuntimeMemory]",
            json.dumps(mem, ensure_ascii=False, indent=2)[:800] if mem else "(empty)",
        ]

# 追加インポート
from ovv.bis.capture_interface_packet import get_last_interface_packet

@bot.command(name="dbg_packet")
async def dbg_packet(ctx: commands.Context):
    pkt = get_last_interface_packet()
    if not pkt:
        await ctx.send("No InterfacePacket has been captured yet.")
        return

    text = str(pkt)
    if len(text) > 1900:
        text = text[:1900] + " ... (truncated)"

    await ctx.send(f"```\n{text}\n```")
    
    # ------------------------------------------------------------
    # !dbg_flow — BIS パイプラインの構造チェック
    # ------------------------------------------------------------
    @bot.command(name="dbg_flow")
    async def dbg_flow(ctx: commands.Context):
        text = [
            "=== BIS FLOW CHECK ===",
            "[GATE] bot.py OK",
            "[IFACE] interface_box OK",
            "[CORE] ovv_call OK",
            "[STAB] stabilizer OK",
            "[PERSIST] pg.py OK",
            "",
            "※ 実行中の流れ（各レイヤ通過状況）は Render Log の BIS:* で確認可能。",
        ]
        await ctx.send("```\n" + "\n".join(text) + "\n```")

    # ------------------------------------------------------------
    # !wipe — TB + memory の削除
    # ------------------------------------------------------------
    @bot.command(name="wipe")
    async def wipe(ctx: commands.Context):

        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        db_pg.wipe_runtime_memory(session_id)
        db_pg.wipe_thread_brain(context_key)

        await ctx.send("Memory + ThreadBrain wiped.")