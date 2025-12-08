# bot.py (BIS v1.1 + Debug Commands Enabled)

import discord
from discord.ext import commands

# Debug Router
from debug.debug_router import route_debug_message

# Debug Commands
from debug.debug_commands import register_debug_commands

# Boundary Gate
from ovv.bis.boundary_gate import build_input_packet

# Pipeline
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary


intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# 必須：Bot 生成後に Debug Commands を登録
register_debug_commands(bot)


@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return

    # Debug 判定（!dbg_* のみ True）
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # 通常コマンド処理
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # ここから Ovv Main Stream
    boundary_packet = build_input_packet(message)
    if boundary_packet is None:
        return

    try:
        final_text = run_ovv_pipeline_from_boundary(boundary_packet)
    except Exception as e:
        final_text = f"Ovv の処理中に予期しないエラーが発生しました: {e}"

    if final_text:
        await message.channel.send(final_text)


if __name__ == "__main__":
    import os
    print("=== Booting Discord Ovv (BIS v1.1 + Debug) ===")
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    bot.run(TOKEN)