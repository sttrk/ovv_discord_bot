# debug/debug_router.py
# Debug Router v3.0 (BIS/OVFS Ready)
#
# ROLE:
#   - "!dbg_xxx" を検知し、debug_commands に委譲する Dispatcher。
#
# MUST:
#   - "!dbg" prefix のみに反応
#   - すべての Debug I/O はここで完結させる（Pipeline へ流さない）
#   - commands 側 API にハード依存しない
#
# DEPENDENCY:
#   - debug_commands.register_debug_commands()
#   - debug_static_messages.DEBUG_HELP
#

from debug import debug_commands
from debug.debug_static_messages import DEBUG_HELP_TEXT


async def route_debug_message(bot, message):
    """Return True if handled."""

    content = message.content.strip()

    # Debug prefix のみ対象
    if not content.startswith("!dbg"):
        return False

    # コマンド部分抽出
    parts = content.split()
    cmd = parts[0]
    args = parts[1:]

    # Debug command registry（debug_commands 側にある dict）
    registry = getattr(debug_commands, "DEBUG_COMMAND_REGISTRY", {})

    # "!dbg_help" or "!dbg"
    if cmd in ("!dbg", "!dbg_help"):
        await message.channel.send(f"```txt\n{DEBUG_HELP_TEXT}\n```")
        return True

    # 通常の debug コマンド
    if cmd in registry:
        try:
            handler = registry[cmd]
            await handler(bot, message, *args)
        except Exception as e:
            await message.channel.send(f"[DEBUG ERROR] {repr(e)}")
        return True

    # 未知コマンド
    await message.channel.send(f"[DEBUG] Unknown command: {cmd}")
    return True