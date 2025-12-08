# ============================================================
# [MODULE CONTRACT]
# NAME: bot
# LAYER: GATE + IO Adapter
#
# ROLE:
#   - Discord のイベントを受け取り、BIS-1（Boundary_Gate）に渡す最初の入口。
#   - Debug Router / commands フレームワークの両方を尊重する。
#
# MUST:
#   - Persistence / Core / Stabilizer に直接触れない（Pipeline に委譲）
#   - Discord の send 以外の I/O を持たない（print は除く）
# ============================================================

import os
import discord
from discord.ext import commands

from debug.debug_router import route_debug_message
from debug.debug_commands import register_debug_commands
from debug.health_monitor import run_health_monitor   # ← ADDED

from ovv.bis.boundary_gate import build_input_packet
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary


# ============================================================
# Bot Instance
# ============================================================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Debug Commands 登録
register_debug_commands(bot)


# ============================================================
# on_message — Discord → Boundary Gate Entry
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    # Bot 自身
    if message.author.bot:
        return

    # System message 除外
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return

    # Debug Router（!dbg 系）
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # commands フレームワーク（!xxx）
    if message.content.startswith("!"):
        await bot.process_commands(message)
        # ※ commands 実行後も health monitor は呼ぶべき
        await run_health_monitor(bot)          # ← ADDED
        return

    # Boundary Packet 生成
    boundary_packet = build_input_packet(message)
    if boundary_packet is None:
        await run_health_monitor(bot)          # ← ADDED
        return

    # Pipeline 実行
    try:
        final_text = run_ovv_pipeline_from_boundary(boundary_packet)
    except Exception as e:
        final_text = f"Ovv の処理中に予期しないエラーが発生しました: {e}"

    if final_text:
        await message.channel.send(final_text)

    # =======================================================
    # Auto BIS Health Check（自動自己診断）
    # =======================================================
    await run_health_monitor(bot)              # ← ADDED


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    print("=== Booting Discord Ovv (BIS v1.1, Health Monitor Enabled) ===")
    token = os.getenv("DISCORD_BOT_TOKEN")
    bot.run(token)