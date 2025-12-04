# boot # debug/debug_boot.py

"""
Bot 起動時に boot_log チャンネルへステータスを投稿する処理。
"""

from datetime import datetime, timezone
from typing import Optional

import discord

from debug.debug_router import find_text_channel_by_name
from debug.debug_static_messages import DEBUG_STATIC_MESSAGES

# PG / Notion の疎通状況をざっくり知るために import
from database import pg as db_pg
from notion import notion_api


async def _get_pg_status() -> str:
    """
    非破壊な PG の簡易ヘルスチェック。
    例外は握りつぶして "NG" を返す。
    """
    try:
        conn = db_pg.PG_CONN or db_pg.pg_connect()
        if conn is None:
            return "NG (no connection)"
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        return "OK"
    except Exception as e:
        return f"NG ({type(e).__name__})"


async def _get_notion_status() -> str:
    """
    Notion の簡易ヘルスチェック。
    例外は握りつぶして "NG" を返す。
    """
    try:
        # かなり軽い操作として、自分のワークスペース情報を読む（実装に応じて変更可）
        notion_api.client  # 存在チェック用（client を公開している想定）
        return "OK"
    except Exception as e:
        return f"NG ({type(e).__name__})"


async def _ensure_boot_static_message(channel: discord.TextChannel):
    """
    boot_log チャンネルに、未ピン留めなら固定メッセージを 1 回だけ送る。
    """
    static = DEBUG_STATIC_MESSAGES.get("boot_log")
    if not static:
        return

    try:
        pins = await channel.pins()
        for msg in pins:
            if msg.author.bot and "boot_log" in msg.content:
                # 既にそれっぽいものがピン留めされていれば何もしない
                return
    except Exception:
        # pins() に失敗しても致命的ではないので無視
        pass

    try:
        msg = await channel.send(static)
        try:
            await msg.pin()
        except Exception:
            pass
    except Exception:
        pass


async def send_boot_message(bot: discord.Client):
    """
    bot.py の on_ready から呼び出すことを想定。
    全 guild の boot_log チャンネルを探し、起動メッセージを投稿する。
    """
    now = datetime.now(timezone.utc).isoformat()

    pg_status = await _get_pg_status()
    notion_status = await _get_notion_status()

    for guild in bot.guilds:
        ch = find_text_channel_by_name(guild, "boot_log")
        if ch is None:
            continue

        # 固定メッセージ（説明）のピン留め
        await _ensure_boot_static_message(ch)

        text = (
            "【Ovv Bot boot_log】\n"
            f"- guild: {guild.name} ({guild.id})\n"
            f"- time:  {now}\n"
            f"- PG:    {pg_status}\n"
            f"- Notion:{notion_status}\n"
        )

        try:
            await ch.send(text)
        except Exception:
            # boot_log がなくても致命ではないので握りつぶす
            continuemessage auto sender
