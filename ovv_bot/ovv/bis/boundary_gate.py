# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.4 (Debug + PacketCapture Enabled)
#
# ROLE:
#   - Discord on_message → BIS パイプラインへの入口。
#   - Discord メッセージ → InputPacket 変換を一元管理。
#   - パイプライン開始前に InputPacket を capture（dbg_packet 用）
#   - 例外発生時は Render ログに詳細、Discord には境界エラーを返す。
#
# CONSTRAINT:
#   - Core / Persist / Notion には直接触れない。
#   - interface_box.handle_request() のみを呼ぶ。
# ============================================================

from __future__ import annotations

from typing import Optional
import traceback

from .types import InputPacket
from .interface_box import handle_request
from .capture_interface_packet import capture  # dbg_packet 用


# ------------------------------------------------------------
# Debug Flag
# ------------------------------------------------------------

DEBUG_BIS = True  # Render ログに内部スタックトレースを出す


# ------------------------------------------------------------
# Command 判定
# ------------------------------------------------------------

def _detect_command_type(raw: str) -> Optional[str]:
    if not raw:
        return None

    head = raw.strip().split()[0].lower()

    mapping = {
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",

        # 旧互換コマンド
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
    Discord → InputPacket → BIS Pipeline.
    """

    # Bot自身の発言は無視
    if getattr(message.author, "bot", False):
        return

    raw_content = message.content or ""
    command_type = _detect_command_type(raw_content)

    # コマンド以外は現フェーズでは無視
    if command_type is None:
        return

    # Discord context → Ovv context
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
    # ★ dbg_packet 用に Packet Capture
    # --------------------------------------------------------
    capture(packet)

    if DEBUG_BIS:
        print("[Boundary_Gate] Captured InputPacket:", packet)

    # --------------------------------------------------------
    # BIS Pipeline 実行（Interface_Box → Core → Stabilizer）
    # --------------------------------------------------------
    try:
        final_message = await handle_request(packet)

    except Exception as e:
        # デバッグログ
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
    # Discord に返す
    # --------------------------------------------------------
    if final_message:
        await message.channel.send(final_message)