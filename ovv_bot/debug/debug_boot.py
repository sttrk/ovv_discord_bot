# debug/debug_boot.py
"""
起動時に boot_log チャンネルへステータスを投稿する。
"""

import os
from datetime import datetime, timezone

import discord

import database.pg as db_pg
from notion import notion_api

# 固定 boot_log チャンネル ID
BOOT_LOG_CHANNEL_ID = 1446060807044468756


def _check_env_ok() -> bool:
    keys = [
        "DISCORD_BOT_TOKEN",
        "OPENAI_API_KEY",
        "NOTION_API_KEY",
        "NOTION_TASKS_DB_ID",
        "NOTION_SESSIONS_DB_ID",
        "NOTION_LOGS_DB_ID",
        "POSTGRES_URL",
    ]
    return all(os.getenv(k) for k in keys)


def _check_pg_ok() -> bool:
    return db_pg.PG_CONN is not None


def _check_notion_ok() -> bool:
    try:
        # notion_api.client を公開している前提
        _ = notion_api.client
        return True
    except Exception:
        return False


def _check_openai_ok() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


async def send_boot_message(bot: discord.Client):
    """
    bot.py の on_ready から呼び出される想定。
    スクショと同じ形式の Boot Summary を 1 件だけ送信する。
    """
    now = datetime.now(timezone.utc).isoformat()

    env_ok = _check_env_ok()
    pg_ok = _check_pg_ok()
    notion_ok = _check_notion_ok()
    openai_ok = _check_openai_ok()
    context_ok = env_ok and pg_ok and notion_ok and openai_ok

    lines = [
        "Ovv Boot Summary",
        "",
        "起動ログを報告します。",
        "",
        "**ENV**",
        str(env_ok),
        "",
        "**PostgreSQL**",
        str(pg_ok),
        "",
        "**Notion**",
        str(notion_ok),
        "",
        "**OpenAI**",
        str(openai_ok),
        "",
        "**Context Ready**",
        str(context_ok),
        "",
        f"`timestamp: {now}`",
    ]
    text = "\n".join(lines)

    ch = bot.get_channel(BOOT_LOG_CHANNEL_ID)
    if ch is None:
        print("[BOOT_LOG] boot_log channel (ID=1446060807044468756) not found. Skip send.")
        return

    try:
        await ch.send(text)
    except Exception as e:
        print("[BOOT_LOG] send failed:", repr(e))
