# debug/debug_router.py

from .debug_commands import run_debug_command

async def route_debug_message(bot, message):
    content = message.content.strip()

    # --------------------------------------------------------
    # [1] debug prefix でない → 通常フローへ
    # --------------------------------------------------------
    if not content.startswith("!dbg"):
        return False

    parts = content.split()

    # --------------------------------------------------------
    # [2] "!dbg" 単体は usage を返す（事故防止）
    # --------------------------------------------------------
    if len(parts) == 1:
        await message.channel.send(
            "[DEBUG] usage: !dbg <command> [args ...]"
        )
        return True

    cmd = parts[1]
    args = parts[2:]

    try:
        # ----------------------------------------------------
        # [3] debug コマンド処理
        # ----------------------------------------------------
        resp = await run_debug_command(message, cmd, args)

        # run_debug_command が None を返した場合の防御
        if resp is None:
            resp = f"[DEBUG] command '{cmd}' executed (no output)"

        await message.channel.send(resp)

    except Exception as e:
        # ----------------------------------------------------
        # [4] router レベルで例外を吸収
        # ----------------------------------------------------
        await message.channel.send(
            f"[DEBUG] Router Error: {repr(e)}"
        )

    return True
