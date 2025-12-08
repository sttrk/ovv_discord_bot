# debug/debug_commands.py
"""
[MODULE CONTRACT]
NAME: debug_commands
ROLE: Gate-Assist (Debug Command Handler)

MUST:
  - read-only access to DB except wipe
  - never call Ovv-Core
  - never mutate ThreadBrain except wipe
"""

import discord
from discord.ext import commands
import database.pg as db_pg


def register_debug_commands(bot: commands.Bot):

    # ------------------------------------------------------------
    # !bs — Boot Summary
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
        await ctx.send(f"```txt\n" + "\n".join(lines) + "\n```")

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

        await ctx.send(f"```txt\n{out}\n```")

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

        await ctx.send(f"```txt\n{out}\n```")

    # ------------------------------------------------------------
    # !dbg_all — TB + runtime memory
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

        await ctx.send(f"```txt\n" + "\n".join(text) + "\n```")

    # ------------------------------------------------------------
    # !wipe — delete TB + runtime memory
    # ------------------------------------------------------------
    @bot.command(name="wipe")
    async def wipe(ctx: commands.Context):

        context_key = ctx.channel.id
        session_id = str(ctx.channel.id)

        db_pg.wipe_runtime_memory(session_id)
        db_pg.wipe_thread_brain(context_key)

        await ctx.send("Memory + ThreadBrain wiped.")