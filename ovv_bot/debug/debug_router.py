# debug/debug_router.py
# ============================================================
# [MODULE CONTRACT]
# NAME: debug_router
# LAYER: Gate-Assist (Router)
#
# ROLE:
#   - on_message 入口で「!dbg 系メッセージ」を捕捉し、
#     必要なら簡易レスポンスを返す。
#
# NOTE:
#   - !dbg_mem / !dbg_all / !bs / !br / !wipe は
#     discord.ext.commands によるコマンドとして定義されているため、
#     本ルータでは処理しない。
# ============================================================

import discord


async def route_debug_message(bot, message: discord.Message) -> bool:
    content = (message.content or "").strip()

    # "!dbg" で始まらないものは関知しない
    if not content.startswith("!dbg"):
        return False

    # "!dbg_mem" / "!dbg_all" 等はコマンドとして処理させる
    if content.startswith("!dbg_"):
        # on_message 内で process_commands が呼ばれるので、ここでは何もしない
        return False

    # "!dbg" or "!dbg_help" などの簡易ヘルプ
    if content in ("!dbg", "!dbg_help"):
        help_text = (
            "[DEBUG COMMANDS]\n"
            "!bs       - Boot Summary（DB接続など）\n"
            "!br       - ThreadBrain dump\n"
            "!dbg_mem  - runtime_memory dump\n"
            "!dbg_all  - TB + memory dump\n"
            "!wipe     - TB / memory の削除\n"
        )
        await message.channel.send(f"```txt\n{help_text}\n```")
        return True

    # その他未知の !dbgXXX は警告だけ出す
    await message.channel.send(f"[DEBUG] Unknown debug command: {content}")
    return True