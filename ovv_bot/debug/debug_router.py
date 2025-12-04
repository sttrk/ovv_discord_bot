# debug/debug_router.py

"""
Discord 側の debug チャンネル／スレッドを名前ベースで探すための
小さなユーティリティ。
"""

from typing import Optional
import discord

# チャンネル名 → debug 種別のマッピング（必要に応じて拡張）
DEBUG_CHANNEL_NAME_MAP = {
    "boot_log": "boot_log",
    "psql": "psql",
    "logs": "logs",
    "thread_brain": "thread_brain",
    "notion": "notion",
    "core": "core",
    "render": "render",
}


def find_text_channel_by_name(
    guild: discord.Guild,
    name: str,
) -> Optional[discord.TextChannel]:
    """
    ギルド内から名前一致のテキストチャンネルを一つ見つける。
    - name はチャンネル名（例: 'boot_log', 'debug' など）
    """
    lowered = name.lower()
    for ch in guild.text_channels:
        if ch.name.lower() == lowered:
            return ch
    return None
