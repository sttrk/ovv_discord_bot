# debug/debug_router.py
"""
Debug Router v2.1 (BIS Compatible)
"""

async def route_debug_message(bot, message):

    content = message.content.strip()

    # prefix validation
    if not content.startswith("!dbg"):
        return False

    # NOTE:
    # すべての "!dbg_x" は bot.process_commands に委譲するだけでよい。
    # ここでは router が「debug コマンドである」ことだけ判定する。

    try:
        await bot.process_commands(message)
        return True
    except Exception as e:
        await message.channel.send(f"[DEBUG] Router Error: {repr(e)}")
        return True