"""
Boundary_Gate
Discord イベント(on_message)から BIS / Core への入口を担うモジュール。
"""

from __future__ import annotations

from typing import Optional

from .types import InputPacket
from .interface_box import handle_request


# ------------------------------------------------------------
# Command 判定
# ------------------------------------------------------------

def _detect_command_type(raw: str) -> Optional[str]:
    """
    Discord メッセージから Ovv コマンド種別を判定する。

    戻り値:
      - "task_create" / "task_start" / "task_paused" / "task_end"
      - None → Ovv 管理対象外（無視）
    """
    if not raw:
        return None

    head = raw.strip().split()[0].lower()
    mapping = {
        # 新コマンド
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",
        # 旧互換コマンド（必要に応じて残す）
        "!task": "task_create",
        "!task_s": "task_start",
        "!task_start": "task_start",
        "!task_p": "task_paused",
        "!task_pause": "task_paused",
        "!task_e": "task_end",
        "!task_end": "task_end",
        "!task_c": "task_end",
        "!task_completed": "task_end",
    }
    return mapping.get(head)


def _strip_head_token(raw: str) -> str:
    """
    先頭トークン（!t 等）を除いた残りを返す。
    先頭トークンのみ or 空の場合は "" を返す。
    """
    if not raw:
        return ""
    parts = raw.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

async def handle_discord_input(message) -> None:
    """
    bot.py から直接呼ばれる唯一のエントリポイント。

    - Discord Message から InputPacket を構築
    - interface_box.handle_request() を呼び出し
    - 戻り値の文字列を Discord に送信する
    """

    # Bot 自身のメッセージは無視
    if getattr(message.author, "bot", False):
        return

    raw_content = message.content or ""
    command_type = _detect_command_type(raw_content)

    # 対象外メッセージは無視（将来 free_chat を解禁する場合はここを拡張）
    if command_type is None:
        return

    channel_id = str(getattr(message.channel, "id", ""))
    author_id = str(getattr(message.author, "id", ""))

    # Discord Thread = task_id = context_key として扱う
    context_key = channel_id
    task_id = context_key

    user_name = getattr(message.author, "display_name", None) or getattr(
        message.author, "name", ""
    )
    user_meta = {"user_id": author_id, "user_name": user_name}

    # Core に渡す InputPacket 構築
    packet = InputPacket(
        raw=raw_content,
        source="discord",
        command=command_type,
        content=_strip_head_token(raw_content),
        author_id=author_id,
        channel_id=channel_id,
        context_key=context_key,
        task_id=task_id,
        user_meta=user_meta,
        meta={
            "discord_channel_id": channel_id,
            "discord_message_id": str(getattr(message, "id", "")),
        },
    )

    # BIS パイプライン実行
    try:
        final_message = await handle_request(packet)
    except Exception as e:  # 例外時も Discord にだけは通知する
        print("[Boundary_Gate] handle_request error:", repr(e))
        final_message = "[Boundary Error] internal failure in BIS pipeline."

    if final_message:
        await message.channel.send(final_message)