# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.3 (Debug + PacketCapture Enabled)
#
# ROLE:
#   - Discord on_message → BIS パイプラインへの入口。
#   - Discord メッセージ → InputPacket 変換を一元管理。
#   - パイプライン開始前に InputPacket を capture し、dbg_packet が参照可能にする。
#   - 例外発生時は、Render ログには詳細（traceback）、Discord には境界エラーを返す。
#
# CONSTRAINT:
#   - Core / Persist / Notion には直接触れない。
#   - BIS の interface_box.handle_request() だけを呼び出す。
# ============================================================

from __future__ import annotations

from typing import Optional
import traceback

from .types import InputPacket
from .interface_box import handle_request
from .capture_interface_packet import capture  # ★追加：dbg_packet 用


# ------------------------------------------------------------
# Debug Flag
# ------------------------------------------------------------

DEBUG_BIS = True  # スタックトレースを Render ログに出す


# ------------------------------------------------------------
# Command 判定
# ------------------------------------------------------------

def _detect_command_type(raw: str) -> Optional[str]:
    """
    Discord メッセージの先頭トークンから Ovv のコマンド種別を判定する。
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
    "!t hoge" → "hoge"
    "!ts   memo memo" → "memo memo"
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
    bot.py → ONLY ENTRY.
    Discord message → InputPacket → BIS pipeline.
    """

    # Bot 自身は無視
    if getattr(message.author, "bot", False):
        return

    raw_content = message.content or ""
    command_type = _detect_command_type(raw_content)

    # コマンド以外は現フェーズでは無視
    if command_type is None:
        return

    # Discord context
    channel_id = str(getattr(message.channel, "id", ""))
    author_id = str(getattr(message.author, "id", ""))

    context_key = channel_id        # Discord Thread = task_id = context_key
    task_id = context_key

    user_name = getattr(message.author, "display_name", None) or getattr(
        message.author, "name", ""
    )
    user_meta = {
        "user_id": author_id,
        "user_name": user_name,
    }

    # --------------------------------------------------------
    # InputPacket 構築
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # ★ 重要：dbg_packet 用 Packet Capture
    # --------------------------------------------------------
    capture(packet)

    # --------------------------------------------------------
    # BIS パイプライン（Interface_Box → Core → Stabilizer）
    # --------------------------------------------------------
    try:
        final_message = await handle_request(packet)

    except Exception as e:
        # デバッグ：Render ログに詳細
        if DEBUG_BIS:
            print("==== BIS PIPELINE EXCEPTION ====")
            print("Exception in Boundary_Gate.handle_discord_input:", repr(e))
            print("-- InputPacket --")
            try:
                print(packet)
            except Exception:
                print("<unprintable packet>")
            print("-- Traceback --")
            traceback.print_exc()
            print("================================")
        else:
            print("[Boundary Error] internal failure in BIS pipeline.")

        final_message = "[Boundary Error] internal failure in BIS pipeline."

    # --------------------------------------------------------
    # Discord 返信
    # --------------------------------------------------------
    if final_message:
        await message.channel.send(final_message)