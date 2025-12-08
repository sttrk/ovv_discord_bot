# debug/debug_commands.py
"""
[MODULE CONTRACT]
NAME: debug_commands
LAYER: Gate-Assist (Debug Command Handler)

ROLE:
  - Discord からの debug コマンドを処理し、人間可読な内部状態を返す

MUST:
  - register_debug_commands(bot) を呼び出すだけで全ての debug コマンドが使える
  - DB や TB（ThreadBrain）を読み込むが、書き換えは wipe のみ
  - Ovv-Core / Interface-Box / Stabilizer には触れない
"""

import discord
from discord.ext import commands

import database.pg as db_pg
from ovv.bis.capture_interface_packet import get_last_interface_packet

# ------------------------------------------------------------
# register_debug_commands
# ------------------------------------------------------------

def register_debug_commands(bot: commands.Bot):

    # ------------------------------------------------------------
    # !bs — Boot Summary（環境と PG 接続）
    # ------------------------------------------------------------
    @bot.command(name="bs")
    async def bs(ctx: commands.Context):
        env_ok = bool(db_pg.PG_URL)
        pg_ok = db_pg.conn is not None

        lines = [
            "Ovv Boot Summary (Debug)",
            "",
            f"ENV: {env_ok}",
            f"PostgreSQL: {pg_ok}",
            "",
            f"session_id: {ctx.channel.id}",
        ]
        await ctx.send("```\n" + "\n".join(lines) + "\n```")

    # ------------------------------------------------------------
    # !br — ThreadBrain dump
    # ------------------------------------------------------------
    @bot.command(name="br")
    async def br(ctx: commands.Context):

        context_key = ctx.channel.id
        tb = db_pg.load_thread_brain(context_key)

        if not tb:
            await ctx.send("ThreadBrain: (none)")
            return

        out = str(tb)
        if len(out) > 1900:
            out = out[:1900]

        await ctx.send("```\n" + out + "\n```")

    # ------------------------------------------------------------
    # !dbg_mem — runtime memory dump
    # ------------------------------------------------------------
    @bot.command(name="dbg_mem")
    async def dbg_mem(ctx: commands.Context):

        session_id = str(ctx.channel.id)
        mem = db_pg.load_runtime_memory(session_id)

        if not mem:
            await ctx.send("runtime_memory: (empty)")
            return

        out = str(mem)
        if len(out) > 1900:
            out = out[:1900]

        await ctx.send("```\n" + out + "\n```")

    # ------------------------------------------------------------
    # !dbg_all — TB + memory dump
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
            str(tb)[:800] if tb else "(none)",
            "",
            "[RuntimeMemory]",
            str(mem)[:800] if mem else "(empty)",
        ]

        await ctx.send("```\n" + "\n".join(text) + "\n```")

    # ------------------------------------------------------------
    # !dbg_packet — InterfacePacket dump（pipeline → core 手前）
    # ------------------------------------------------------------
    @bot.command(name="dbg_packet")
    async def dbg_packet(ctx: commands.Context):

        packet = get_last_interface_packet()

        if not packet:
            await ctx.send("No InterfacePacket has been captured yet.")
            return

        out = str(packet)
        if len(out) > 1900:
            out = out[:1900]

        await ctx.send("```\n" + out + "\n```")

    # ------------------------------------------------------------
    # !dbg_flow — BIS 各レイヤの静的チェック
    # ------------------------------------------------------------
    @bot.command(name="dbg_flow")
    async def dbg_flow(ctx: commands.Context):

        import importlib

        checks = {
            "[GATE] bot.py": "bot",
            "[IFACE] interface_box": "ovv.bis.interface_box",
            "[CORE] ovv_call": "ovv.ovv_call",
            "[STAB] stabilizer": "ovv.bis.stabilizer",
            "[PERSIST] pg.py": "database.pg",
        }

        lines = ["=== BIS FLOW CHECK ==="]

        for label, module_path in checks.items():
            try:
                importlib.import_module(module_path)
                lines.append(f"{label} OK")
            except Exception as e:
                lines.append(f"{label} ERROR: {repr(e)}")

        lines.append("\n※ 実際のパイプライン通過状況は Render Log を参照してください。")

        await ctx.send("```\n" + "\n".join(lines) + "\n```")

    # ------------------------------------------------------------
    # !wipe — TB + runtime_memory wipe
    # ------------------------------------------------------------
    @bot.command(name="wipe")
    async def wipe(ctx: commands.Context):

        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        db_pg.wipe_runtime_memory(session_id)
        db_pg.wipe_thread_brain(context_key)

        await ctx.send("Memory + ThreadBrain wiped.")