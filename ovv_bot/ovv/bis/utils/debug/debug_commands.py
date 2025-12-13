# debug/debug_commands.py
# ============================================================
# MODULE CONTRACT: Gate-Assist Debug Layer v1.1
#
# ROLE:
#   - Ovv の内部状態（BIS / Core / Persist / Packet）を確認するための
#     開発用デバッグコマンドを提供する。
#
# STABLE PUBLIC API:
#   register_debug_commands(bot)
#
# すべてのデバッグコマンドはここに統合され、
# Ovv 内部構造が変わっても可能な限り壊れないように設計されている。
# ============================================================

from __future__ import annotations

import importlib
import discord
from discord.ext import commands

from database import pg as db_pg

# dbg_packet 用
try:
    from ovv.bis.capture_interface_packet import get_last_interface_packet
except Exception:
    get_last_interface_packet = None


# ------------------------------------------------------------
# Helper: モジュール存在チェック
# ------------------------------------------------------------

def _check_module(path: str) -> str:
    try:
        importlib.import_module(path)
        return "OK"
    except Exception as e:
        return f"ERROR: {repr(e)}"


# ------------------------------------------------------------
# Public Entry
# ------------------------------------------------------------

def register_debug_commands(bot: commands.Bot) -> None:
    """
    この関数を bot.py で一度呼ぶだけで、
    全 debug コマンドが Discord 上で利用可能になる。
    """

    # ========================================================
    # 1. Boot Summary: !bs
    # ========================================================
    @bot.command(name="bs")
    async def bs(ctx: commands.Context):
        env_ok = bool(db_pg.PG_URL)
        pg_ok = db_pg.conn is not None

        lines = [
            "=== Ovv Boot Summary ===",
            f"ENV(PostgreSQL URL): {env_ok}",
            f"PG Connection      : {pg_ok}",
            "",
            f"channel_id: {ctx.channel.id}",
        ]

        await ctx.send(f"```\n" + "\n".join(lines) + "\n```")

    # ========================================================
    # 2. BIS Flow Check: !dbg_flow
    # ========================================================
    @bot.command(name="dbg_flow")
    async def dbg_flow(ctx: commands.Context):

        checks = {
            "[GATE]   bot.py": "bot",
            "[BIS]    ovv.bis.boundary_gate": "ovv.bis.boundary_gate",
            "[BIS]    ovv.bis.interface_box": "ovv.bis.interface_box",
            "[CORE]   ovv.core.ovv_core": "ovv.core.ovv_core",
            "[STAB]   ovv.bis.stabilizer": "ovv.bis.stabilizer",
            "[PERSIST]database.pg": "database.pg",
            "[NOTION] ovv.external_services.notion.ops.executor":
                "ovv.external_services.notion.ops.executor",
        }

        lines = ["=== BIS FLOW CHECK ===", ""]

        for label, mod in checks.items():
            lines.append(f"{label:45} {_check_module(mod)}")

        await ctx.send(f"```\n" + "\n".join(lines) + "\n```")

    # ========================================================
    # 3. dbg_packet — Pipeline 入力パケット確認
    # ========================================================
    @bot.command(name="dbg_packet")
    async def dbg_packet(ctx: commands.Context):

        if get_last_interface_packet is None:
            await ctx.send("capture_interface_packet 未導入のため使用不可。")
            return

        packet = get_last_interface_packet()

        if not packet:
            await ctx.send("No packet captured yet.")
            return

        out = str(packet)
        if len(out) > 1900:
            out = out[:1900]

        await ctx.send("```\n" + out + "\n```")

    # ========================================================
    # 4. dbg_mem — RuntimeMemory Dump
    # ========================================================
    @bot.command(name="dbg_mem")
    async def dbg_mem(ctx: commands.Context):
        session_id = str(ctx.channel.id)

        try:
            mem = db_pg.load_runtime_memory(session_id)
        except Exception:
            mem = None

        if not mem:
            await ctx.send("runtime_memory: (empty)")
            return

        out = str(mem)
        if len(out) > 1900:
            out = out[:1900]

        await ctx.send("```\n" + out + "\n```")

    # ========================================================
    # 5. dbg_all — TB + Memory dump
    # ========================================================
    @bot.command(name="dbg_all")
    async def dbg_all(ctx: commands.Context):

        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        try:
            tb = db_pg.load_thread_brain(context_key)
        except Exception:
            tb = None

        try:
            mem = db_pg.load_runtime_memory(session_id)
        except Exception:
            mem = None

        lines = [
            "=== DEBUG ALL ===",
            "",
            "[ThreadBrain]",
            str(tb)[:800] if tb else "(none)",
            "",
            "[RuntimeMemory]",
            str(mem)[:800] if mem else "(empty)",
        ]

        await ctx.send("```\n" + "\n".join(lines) + "\n```")

    # ========================================================
    # 6. wipe — TB + RuntimeMemory reset
    # ========================================================
    @bot.command(name="wipe")
    async def wipe(ctx: commands.Context):

        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        try:
            db_pg.wipe_runtime_memory(session_id)
        except Exception:
            pass

        try:
            db_pg.wipe_thread_brain(context_key)
        except Exception:
            pass

        await ctx.send("Memory + ThreadBrain wiped.")
