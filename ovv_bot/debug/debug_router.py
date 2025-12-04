# debug/debug_router.py

from .debug_commands import run_debug_command

# ============================================================
# Utility: find text channel by name
# ============================================================

def find_text_channel_by_name(guild, name: str):
    """
    guild 内のテキストチャンネルを名前で検索する。
    見つからなければ None を返す。
    """
    name = name.lower()
    for ch in guild.text_channels:
        if ch.name.lower() == name:
            return ch
    return None


# ============================================================
# Debug router
# ============================================================

async def route_debug_message(bot, message):
    content = message.content.strip()

    if not content.startswith("!dbg"):
        return False  # not a debug message

    parts = content.split()
    cmd = parts[1] if len(parts) > 1 else "ping"
    args = parts[2:]

    try:
        resp = await run_debug_command(message, cmd, args)
        await message.channel.send(resp)
    except Exception as e:
        await message.channel.send(f"[DEBUG] Router Error: {repr(e)}")

    return True
