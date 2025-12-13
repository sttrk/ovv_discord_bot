# ovv/bis/utils/debug/bot_notifier.py
from __future__ import annotations

import os
import traceback
from typing import Dict, Optional

import discord
from discord.ext import commands


DEPLOY_CHANNEL_ID = os.getenv("OVV_DEPLOY_NOTIFY_CHANNEL_ID")


async def notify_deploy_ok_via_bot(
    bot: commands.Bot,
    *,
    checks: Optional[Dict[str, str]] = None,
) -> None:
    """
    Bot 自身から Discord チャンネルへデプロイ通知を送る。
    観測専用・例外は絶対に伝播しない。
    """
    if not DEPLOY_CHANNEL_ID:
        return

    try:
        channel = bot.get_channel(int(DEPLOY_CHANNEL_ID))
        if channel is None:
            return

        lines = [
            "✅ **Ovv Deploy OK**",
        ]

        if checks:
            for k, v in checks.items():
                lines.append(f"- {k}: {v}")

        await channel.send("\n".join(lines))

    except Exception:
        traceback.print_exc()