# ovv_runtime/boundary/boundary_gate.py
# Boundary_Gate v0.1 – Minimal Working Edition for Ovv BIS
#
# 目的:
#   - Discord の「生メッセージ」を Ovv 推論系に渡すための標準化 InputPacket に変換する唯一の入口レイヤー。
#   - Bot / Debug / Command / System メッセージを入口でふるいにかけ、Ovv 推論系が扱う対象だけを通す。
#
# 想定利用箇所（bot.py 側・on_message 内のイメージ）:
#
#   from ovv_runtime.boundary.boundary_gate import build_input_packet
#
#   @bot.event
#   async def on_message(message: discord.Message):
#       # ① debug ルータ・コマンド処理などは先に実施
#       handled = await route_debug_message(bot, message)
#       if handled:
#           return
#       if message.content.startswith("!"):
#           ...
#           return
#
#       # ② Boundary_Gate で InputPacket を構築
#       packet = build_input_packet(message)
#       if packet is None:
#           return  # Ovv に回さない
#
#       # ③ ここから先は Interface_Box / Stabilizer に委譲
#       #    例: ans = call_ovv_with_packet(packet)
#       #        await message.channel.send(ans)
#
# このファイルは Discord / Ovv / PG / Notion への依存を最小限に抑え、
# 「入口でのフィルタリングと標準化」にのみ責務を限定する。


from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord


# ============================================================
# InputPacket 型
# ============================================================

@dataclass
class InputPacket:
    """
    Ovv 推論系に渡すための標準化入力パケット。

    Boundary_Gate の責務:
      - Discord の Message → InputPacket への"型変換"
      - Ovv 側は InputPacket を前提に設計すればよい（Discord の生構造を意識しない）
    """
    context_key: int
    session_id: str

    guild_id: Optional[int]
    channel_id: int
    thread_id: Optional[int]

    author_id: int
    author_name: str

    text: str
    is_task_channel: bool

    created_at: str  # ISO8601 (UTC)
    message_id: int

    # 将来拡張用フィールド（添付ファイル / 画像 / メタ情報）
    attachments_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """辞書として扱いたい場合用。"""
        return asdict(self)


# ============================================================
# 内部ユーティリティ
# ============================================================

def _compute_context_key(message: discord.Message) -> int:
    """
    既存 bot.py の get_context_key と同じロジック。
    スレッド単位 / チャンネル単位 / DM を一意に識別するキー。
    """
    ch = message.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if message.guild is None:
        # DM の場合は channel.id のみ
        return ch.id
    # guild + channel を 64bit にパック
    return (message.guild.id << 32) | ch.id


def _is_task_channel(message: discord.Message) -> bool:
    """
    既存 bot.py の is_task_channel と同じロジック。
    'task_' で始まるチャンネル（または親チャンネル）をタスクチャネル扱いにする。
    """
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return bool(parent and parent.name.lower().startswith("task_"))
    return message.channel.name.lower().startswith("task_")


def _extract_text(message: discord.Message, max_len: int = 2000) -> str:
    """
    メッセージ本文を抽出し、前後の空白をトリムし、長すぎる場合は切り捨てる。
    Discord の単一メッセージ上限 2000 をデフォルトとする。
    """
    raw = message.content or ""
    text = raw.strip()
    if not text:
        return ""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _normalize_timestamp(dt: Optional[datetime]) -> str:
    """
    Discord Message.created_at は基本 UTC naive なので、
    明示的に UTC に変換し ISO8601 文字列で返す。
    """
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


# ============================================================
# Public API
# ============================================================

def build_input_packet(message: discord.Message) -> Optional[InputPacket]:
    """
    Discord の生メッセージから InputPacket を構築する。

    戻り値:
      - InputPacket: Ovv に渡すべき通常メッセージ
      - None: Ovv に渡すべきでないもの（Bot / System / 空メッセージなど）

    ※ 注意:
      - Debug コマンド（!dbg ...）や通常コマンド（!xxx）は、
        bot.py 側で先に処理されている前提。
      - ここでは「Ovv に回すべき通常メッセージかどうか」だけを見る。
    """

    # 1) Bot 自身・他 Bot のメッセージは対象外
    if message.author.bot:
        return None

    # 2) Discord のシステムメッセージは対象外
    #    例: thread_created, guild_member_join, etc.
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return None

    # 3) テキスト抽出 & バリデーション
    text = _extract_text(message)
    if not text:
        # 空メッセージ（スタンプのみ等）は Ovv に渡さない
        return None

    # 4) コンテキストキー / チャネル情報
    context_key = _compute_context_key(message)
    session_id = str(context_key)
    is_task = _is_task_channel(message)

    guild_id: Optional[int] = message.guild.id if message.guild is not None else None
    channel_id: int = message.channel.id
    thread_id: Optional[int] = message.channel.id if isinstance(message.channel, discord.Thread) else None

    # 5) 著者情報
    author_id = message.author.id
    # display_name があればそれを優先
    author_name = getattr(message.author, "display_name", None) or message.author.name

    # 6) タイムスタンプ
    created_at_str = _normalize_timestamp(getattr(message, "created_at", None))

    # 7) 添付ファイル数（将来の AutoShell / OCR 連携用ヒント）
    attachments_count = len(getattr(message, "attachments", []) or [])

    # 8) InputPacket 構築
    packet = InputPacket(
        context_key=context_key,
        session_id=session_id,
        guild_id=guild_id,
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        text=text,
        is_task_channel=is_task,
        created_at=created_at_str,
        message_id=message.id,
        attachments_count=attachments_count,
    )

    return packet
