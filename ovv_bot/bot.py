# ============================================================
# [MODULE CONTRACT]
# NAME: bot
# ROLE: GATE + IO Adapter
# INPUT:
#   - Discord Message (discord.Message)
# OUTPUT:
#   - Discord Message (send)
# SIDE EFFECTS:
#   - runtime_memory append
#   - thread_brain update
# MUST:
#   - 全処理を Boundary_Gate から開始すること
#   - Ovv-Core を直接呼ばないこと
#   - Stabilizer を経由して Discord に出力すること
# MUST NOT:
#   - Persistence 層へ直接アクセスしない（呼ぶのは pipeline 側）
#   - Core ロジックをここに書かない
# DEPENDENCY:
#   - debug_router
#   - boundary_gate
#   - pipeline (run_ovv_pipeline_from_boundary)
# ============================================================

import discord
from discord.ext import commands

from debug.debug_router import route_debug_message
from ovv.bis.boundary_gate import build_boundary_packet
from ovv.bis.pipeline import run_ovv_pipeline_from_boundary


# ============================================================
# [IO] Bot Instance
# ============================================================
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())


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
    # [GATE] Bot自身の発言は無視
    # -----------------------------------------
    if message.author.bot:
        return

    # -----------------------------------------
    # [GATE] Discordデフォルト以外の特殊メッセージは破棄
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
    # [GATE/IO] Discordコマンド (!xxx)
    # -----------------------------------------
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # -----------------------------------------
    # [GATE] Boundary Packet 生成
    # -----------------------------------------
    boundary_packet = build_boundary_packet(message)
    if boundary_packet is None:
        return

    # -----------------------------------------
    # [PIPELINE] Ovv Main Stream Dispatch
    #  Boundary → Interface → Core → Stabilizer
    # -----------------------------------------
    try:
        final_ans = run_ovv_pipeline_from_boundary(boundary_packet)
    except Exception as e:
        final_ans = f"Ovv の処理中に予期しないエラーが発生しました: {e}"

    # -----------------------------------------
    # [IO] Discord に安定化済み出力を送信
    # -----------------------------------------
    if final_ans:
        await message.channel.send(final_ans)