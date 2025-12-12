# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.5 (ThreadWBS Commands Routed)
#
# ROLE:
#   - Discord on_message → BIS パイプラインへの入口。
#   - Discord メッセージ → InputPacket 変換を一元管理。
#   - パイプライン開始前に InputPacket を capture（dbg_packet 用）
#   - 例外発生時は Render ログに詳細、Discord には境界エラーを返す。
#
# CONSTRAINT:
#   - Core / Persist / Notion / WBS(PG) には直接触れない。
#   - interface_box.handle_request() のみを呼ぶ。
#
# NOTE:
#   - ThreadWBS の更新は「明示コマンド」を InputPacket.command に載せて下流へ渡す。
#     （Boundary_Gate はルーティングのみ。更新ロジック/永続化は担当しない）
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
    """
    Discord の先頭トークンから command_type を決定する。
    Boundary_Gate は「検出と正規化」までが責務。
    """
    if not raw:
        return None

    head = raw.strip().split()[0].lower()

    mapping = {
        # Task / thread lifecycle
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",

        # ThreadWBS user-ack commands (draft -> minimal routing)
        "!wy": "wbs_accept",   # CDC候補を採用
        "!wn": "wbs_reject",   # CDC候補を破棄
        "!we": "wbs_edit",     # CDC候補を編集採用（後続テキスト必須想定）

        # Debug / inspect
        "!wbs": "wbs_show",    # 現在のWBSを表示（参照のみ）

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
    """
    先頭トークン（コマンド）を除いた残りを content として返す。
    例: "!we 修正文" -> "修正文"
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
    Discord → InputPacket → BIS Pipeline.
    """

    # Bot自身の発言は無視
    if getattr(message.author, "bot", False):
        return

    raw_content = (message.content or "").strip()
    command_type = _detect_command_type(raw_content)

    # コマンド以外は現フェーズでは無視
    if command_type is None:
        return

    # Discord context → Ovv context
    # Discord Thread = task_id = context_key（現行方針）
    channel = getattr(message, "channel", None)
    channel_id = str(getattr(channel, "id", ""))
    author_id = str(getattr(getattr(message, "author", None), "id", ""))

    context_key = channel_id
    task_id = context_key

    # Thread名（存在すれば）を付与（下流で !t 初期WBS生成に使う）
    thread_name = getattr(channel, "name", None) or ""

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
            "discord_thread_name": thread_name,
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