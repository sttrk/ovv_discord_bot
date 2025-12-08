# ============================================================
# [MODULE CONTRACT]
# NAME: boundary_gate
# LAYER: BIS-1 (Boundary Gate)
# ROLE:
#   - Discord の生メッセージから Ovv が扱える BoundaryPacket を生成する唯一の入口。
#   - I/O と入力正規化のみを担当。
#
# INPUT:
#   - discord.Message
#
# OUTPUT:
#   - InputPacket (dict/dataclass)
#
# MUST:
#   - Discord 生メッセージ構造を Ovv 内部表現に正規化する
#   - Bot・他 Bot・System メッセージをフィルタする
#   - Ovv に進ませるべき通常メッセージのみ通過させる
#
# MUST NOT:
#   - Core / Pipeline / Interface_Box を直接呼ばない
#   - DB / Notion にアクセスしない
#   - 推論・要約・LLM 関連処理を行わない
#
# BIS LOG:
#   - print("[BIS-1] BoundaryGate: packet built:", packet.session_id)
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord


@dataclass
class InputPacket:
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

    def to_dict(self):
        return asdict(self)


# ============================================================
# Internal helpers
# ============================================================

def _compute_context_key(message: discord.Message) -> int:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if message.guild is None:
        return ch.id
    return (message.guild.id << 32) | ch.id


def _is_task_channel(message: discord.Message) -> bool:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return bool(parent and parent.name.lower().startswith("task_"))
    return message.channel.name.lower().startswith("task_")


def _extract_text(message: discord.Message, max_len=2000) -> str:
    raw = message.content or ""
    text = raw.strip()
    if not text:
        return ""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _normalize_timestamp(dt: Optional[datetime]) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


# ============================================================
# Public API — build_input_packet
# ============================================================

def build_input_packet(message: discord.Message) -> Optional[InputPacket]:

    # Bot / 他 Bot
    if message.author.bot:
        return None

    # System message
    if getattr(message, "type", discord.MessageType.default) is not discord.MessageType.default:
        return None

    text = _extract_text(message)
    if not text:
        return None

    context_key = _compute_context_key(message)
    session_id = str(context_key)
    is_task = _is_task_channel(message)

    guild_id = message.guild.id if message.guild else None
    channel_id = message.channel.id
    thread_id = message.channel.id if isinstance(message.channel, discord.Thread) else None

    author_id = message.author.id
    author_name = getattr(message.author, "display_name", None) or message.author.name

    created_at_str = _normalize_timestamp(message.created_at)
    attachments_count = len(getattr(message, "attachments", []) or [])

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

    print(f"[BIS-1] BoundaryGate: Packet built (ctx={context_key}, msg={message.id})")

    return packet