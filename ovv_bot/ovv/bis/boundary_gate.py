"""
[MODULE CONTRACT]
NAME: boundary_gate
ROLE: GATE
INPUT:
  - Discord Message (discord.Message)
OUTPUT:
  - BoundaryPacket | None
SIDE EFFECTS:
  - None（DB / Notion / Core 呼び出し禁止）
MUST:
  - Discord の生メッセージを Ovv 圏内の安全な BoundaryPacket に変換する
  - Bot / System / 空メッセージは入口で排除する
  - Discord 固有構造（guild / thread 等）を Ovv から隠蔽する
MUST NOT:
  - runtime_memory / thread_brain に触れない
  - Ovv-Core / Stabilizer / Interface_Box を呼び出さない
  - Discord 送信を行わない
DEPENDENCY:
  - discord.py のみ
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord


# ============================================================
# [GATE] BoundaryPacket 型
# ============================================================
@dataclass
class BoundaryPacket:
    """
    Ovv へ渡すための「標準化境界パケット」。
    Boundary_Gate はこの構造への変換のみを行う。
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

    created_at: str
    message_id: int

    attachments_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# [GATE] 内部ユーティリティ
# ============================================================
def _compute_context_key(message: discord.Message) -> int:
    """
    [GATE]
    スレッド / チャンネル / DM を一意識別する context_key を算出する。
    """
    ch = message.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if message.guild is None:
        return ch.id
    return (message.guild.id << 32) | ch.id


# ------------------------------------------------------------
# [GATE] task チャンネル判定
# ------------------------------------------------------------
def _is_task_channel(message: discord.Message) -> bool:
    """
    'task_' で始まるチャンネル（または親）を task チャンネル扱いする。
    """
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return bool(parent and parent.name.lower().startswith("task_"))
    return ch.name.lower().startswith("task_")


# ------------------------------------------------------------
# [GATE] メッセージ本文抽出
# ------------------------------------------------------------
def _extract_text(message: discord.Message, max_len: int = 2000) -> str:
    """
    本文を抽出し整形する。
    空文字またはスタンプのみの場合は "" を返す。
    """
    raw = message.content or ""
    text = raw.strip()
    if not text:
        return ""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


# ------------------------------------------------------------
# [GATE] created_at 正規化
# ------------------------------------------------------------
def _normalize_timestamp(dt: Optional[datetime]) -> str:
    """
    Discord の created_at を ISO8601（UTC）に正規化する。
    """
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


# ============================================================
# [GATE] Public API
# ============================================================
def build_boundary_packet(message: discord.Message) -> Optional[BoundaryPacket]:
    """
    Discord → BoundaryPacket の唯一の正式入口。
    Ovv が扱わないメッセージは None を返し、後段へ流さない。
    """

    # -----------------------------------------
    # [GATE] Bot 自身は処理対象外
    # -----------------------------------------
    if message.author.bot:
        return None

    # -----------------------------------------
    # [GATE] System メッセージは対象外
    # -----------------------------------------
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return None

    # -----------------------------------------
    # [GATE] 本文抽出
    # -----------------------------------------
    text = _extract_text(message)
    if not text:
        return None

    # -----------------------------------------
    # [GATE] コンテキスト情報
    # -----------------------------------------
    context_key = _compute_context_key(message)
    session_id = str(context_key)
    is_task = _is_task_channel(message)

    guild_id: Optional[int] = message.guild.id if message.guild is not None else None
    channel_id: int = message.channel.id
    thread_id: Optional[int] = message.channel.id if isinstance(message.channel, discord.Thread) else None

    author_id = message.author.id
    author_name = getattr(message.author, "display_name", None) or message.author.name

    created_at_str = _normalize_timestamp(getattr(message, "created_at", None))

    attachments_count = len(getattr(message, "attachments", []) or [])

    # -----------------------------------------
    # [GATE] BoundaryPacket 構築
    # -----------------------------------------
    packet = BoundaryPacket(
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