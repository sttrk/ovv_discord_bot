# ============================================================
# [MODULE CONTRACT]
# NAME: bot
# ROLE: GATE + IO Adapter
# ============================================================

import discord
from discord.ext import commands

# Debug router
from debug.debug_router import route_debug_message

# Debug commands registration
from debug.debug_commands import register_debug_commands

# Boundary Gate
from ovv.bis.boundary_gate import build_input_packet

# Pipeline
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary


# ============================================================
# [IO] Bot Instance
# ============================================================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# register debug commands
register_debug_commands(bot)


# ============================================================
# [GATE] on_message — Discord → Boundary Gate Entry
# ============================================================
@bot.event
async def on_message(message: discord.Message):

    # -----------------------------------------
    # [GATE] Bot 自身を無視
    # -----------------------------------------
    if message.author.bot:
        return

    # -----------------------------------------
    # [GATE] System メッセージ除外
    # -----------------------------------------
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return

    # -----------------------------------------
    # [GATE] Debug Router（最優先）
    #       → pipeline へは流さないが、
    #         Discord commands (!dbg_xxx) は必ず実行する
    # -----------------------------------------
    is_debug = await route_debug_message(bot, message)
    if is_debug:
        await bot.process_commands(message)   # ← これが必須
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
    # [PIPELINE] Boundary → Interface → Core → Stabilizer
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

    print("=== Booting Discord Ovv (BIS v1.1) ===")
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    bot.run(TOKEN)