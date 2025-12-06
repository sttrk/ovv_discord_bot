# debug/debug_router.py
# Debug Router v2.0 (BIS/OVFS Compatible)
#
# ROLE:
#   - "!dbg_xxx" コマンドを安全に受け取り、
#     debug_commands の該当関数へ委譲する Dispatcher。
#
# MUST:
#   - "!dbg" から始まるメッセージのみ処理する
#   - 壊れにくいパーサを使う（parts[1] を直接取らない）
#   - debug_commands の関数に直接ディスパッチする
#   - Discord へは必ず str を返す
#
# MUST NOT:
#   - run_debug_command のような旧 API を使わない
#   - OvvCore に影響する処理を書かない
#   - ThreadBrain や runtime_memory を変更しない
#
# COMMANDS:
#   - !dbg_mem
#   - !dbg_all
#   - !dbg_help


from debug.debug_commands import dbg_mem, dbg_all
from debug.debug_context import debug_context


# ============================================================
# Debug router (safe dispatcher)
# ============================================================

async def route_debug_message(bot, message):
    content = message.content.strip()

    # Debug prefix check
    if not content.startswith("!dbg"):
        return False  # Not a debug message

    # Tokenize
    parts = content.split()
    cmd = parts[0]      # "!dbg_mem" など
    args = parts[1:]    # 将来の拡張用

    try:
        # -----------------------------------------
        # Routing table（完全一致順）
        # -----------------------------------------
        if cmd == "!dbg_mem":
            await dbg_mem(bot, message)
            return True

        if cmd == "!dbg_all":
            await dbg_all(bot, message)
            return True

        if cmd in ("!dbg", "!dbg_help"):
            help_text = (
                "[DEBUG COMMANDS]\n"
                "!dbg_mem  - runtime_memory を表示\n"
                "!dbg_all  - memory + thread_brain を表示\n"
                "!dbg_help - このヘルプを表示"
            )
            await message.channel.send(f"```txt\n{help_text}\n```")
            return True

        # -----------------------------------------
        # Unknown command
        # -----------------------------------------
        await message.channel.send(f"[DEBUG] Unknown command: {cmd}")
        return True

    except Exception as e:
        await message.channel.send(f"[DEBUG] Router Error: {repr(e)}")
        return True