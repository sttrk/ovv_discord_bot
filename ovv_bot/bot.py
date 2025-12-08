# ============================================================
# [MODULE CONTRACT]
# NAME: bot
# ROLE: GATE + IO Adapter
#
# INPUT:
#   - Discord Message (discord.Message)
#
# OUTPUT:
#   - Discord Message (send)
#
# MUST:
#   - Boundary_Gate から処理が始まること
#   - Pipeline に処理を委譲し、Core/Stabilizer をここで扱わない
#   - Discord 入出力以外の責務を持たない
#
# MUST NOT:
#   - Persistence（PG/Notion）へ直接アクセスしない
#   - Core ロジックを実装しない
#
# DEPENDENCY:
#   - debug_router
#   - debug_boot
#   - boundary_gate.build_input_packet
#   - pipeline.run_ovv_pipeline_from_boundary
# ============================================================

import discord
from discord.ext import commands

# ============================================================
# [DEBUG BOOT] Debug context を必ず最初に注入
# ============================================================
from debug.debug_boot import inject_debug_context
inject_debug_context()

# Debug router
from debug.debug_router import route_debug_message

# Boundary Gate
from ovv.bis.boundary_gate import build_input_packet

# Main Pipeline
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary


# ============================================================
# [IO] Bot Instance
# ============================================================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# [GATE] on_message — Discord → Boundary Gate Entry
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # -----------------------------------------
    # [GATE] Bot 自身
    # -----------------------------------------
    if message.author.bot:
        return

    # -----------------------------------------
    # [GATE] System メッセージ除外
    # -----------------------------------------
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return

    # -----------------------------------------
    # [GATE] Debug Router (最優先)
    # -----------------------------------------
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # -----------------------------------------
    # [GATE/IO] Discord コマンド (!xxx)
    # -----------------------------------------
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # -----------------------------------------
    # [GATE] BoundaryPacket 生成
    # -----------------------------------------
    boundary_packet = build_input_packet(message)
    if boundary_packet is None:
        return

    # -----------------------------------------
    # [PIPELINE] Main Stream Dispatch
    # -----------------------------------------
    try:
        final_text = run_ovv_pipeline_from_boundary(boundary_packet)
    except Exception as e:
        final_text = f"Ovv の処理中に予期しないエラーが発生しました: {e}"

    # -----------------------------------------
    # [IO] Discord 出力
    # -----------------------------------------
    if final_text:
        await message.channel.send(final_text)


# ============================================================
# [ENTRYPOINT]
# ============================================================
if __name__ == "__main__":
    import os

    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    print("=== Booting Discord Ovv (BIS v1.1) ===")
    bot.run(TOKEN)