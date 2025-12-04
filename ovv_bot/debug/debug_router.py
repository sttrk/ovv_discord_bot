# debug/debug_router.py

from .debug_commands import run_debug_command

async def route_debug_message(bot, message):
    content = message.content.strip()

    # debug プレフィックスでない → 通常フローへ
    if not content.startswith("!dbg"):
        return False

    parts = content.split()

    # "!dbg" 単体 → usage を返して終了（事故防止）
    if len(parts) == 1:
        await message.channel.send(
            "[DEBUG] usage: !dbg <command> [args ...]"
        )
        return True

    cmd = parts[1]
    args = parts[2:]

    try:
        resp = await run_debug_command(message, cmd, args)

        if resp is None:
            resp = f"[DEBUG] command '{cmd}' executed (no output)"

        await message.channel.send(resp)

    except Exception as e:
        await message.channel.send(
            f"[DEBUG] Router Error: {repr(e)}"
        )

    return True
