# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.7.2
#   (ThreadWBS Command Routing + Done/Dropped + Hardened + DebugSuite Typed)
#
# ROLE:
#   - Discord on_message → BIS パイプラインへの入口。
#   - Discord メッセージ → InputPacket 変換を一元管理。
#   - パイプライン開始前に InputPacket を capture（dbg_packet 用）
#   - 例外発生時は Render ログに詳細、Discord には境界エラーを返す。
#
# RESPONSIBILITY TAGS:
#   [ENTRY_BG]     Discord message を受けて入口処理
#   [CMD_ROUTE]    コマンド検出と正規化（ルーティングのみ）
#   [PACKETIZE]    InputPacket 構築
#   [CAPTURE]      dbg_packet 用 capture
#   [FAILSAFE]     例外隔離（境界で止める）
#
# CONSTRAINTS (HARD):
#   - Core / Persist / Notion / WBS(PG) には直接触れない。
#   - interface_box.handle_request() のみを呼ぶ。
#
# NOTE:
#   - ThreadWBS の更新は「明示コマンド」を InputPacket.command に載せて下流へ渡す。
#     （Boundary_Gate はルーティングのみ。更新ロジック/永続化は担当しない）
#   - Debug Command Suite は「必ず下流へ通す」(入口で捨てない)。
#   - Debug は command="debug" に潰さず、dbg_* として種別を保持する（情報落ち防止）。
# ============================================================

from __future__ import annotations

from typing import Optional, Tuple, Any
import traceback

from .types import InputPacket
from .interface_box import handle_request
from .capture_interface_packet import capture  # dbg_packet 用


# ------------------------------------------------------------
# Debug Flag
# ------------------------------------------------------------

DEBUG_BIS = True  # Render ログに内部スタックトレースを出す


# ------------------------------------------------------------
# Debug Command Suite (routing only)
#   - Boundary では「種別の正規化」まで（実行は下流）
# ------------------------------------------------------------

_DEBUG_MAP = {
    # packet dump
    "!packet": "dbg_packet",
    "!dbg_packet": "dbg_packet",

    # state dump
    "!state": "dbg_state",
    "!dbg_state": "dbg_state",

    # help
    "!help": "dbg_help",
    "!dbg_help": "dbg_help",
    "!dbg": "dbg_help",
    "!debug": "dbg_help",
}


# ------------------------------------------------------------
# Command 判定
# ------------------------------------------------------------

def _detect_command_type(raw: str) -> Optional[str]:
    """
    Discord の先頭トークンから command_type を決定する。
    Boundary_Gate は「検出と正規化」までが責務。

    HARD:
      - Debug Command Suite は必ず下流へ通す（入口で捨てない）。
      - Debug は dbg_* に正規化して種別を保持する（情報落ち防止）。
    """
    if not raw:
        return None

    head = raw.strip().split()[0].lower()

    # ---- Debug suite: ALWAYS PASS (typed) ----
    dbg = _DEBUG_MAP.get(head)
    if dbg:
        return dbg

    mapping = {
        # Task / thread lifecycle
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",

        # ThreadWBS user-ack commands (candidate -> explicit decision)
        "!wy": "wbs_accept",
        "!wn": "wbs_reject",
        "!we": "wbs_edit",

        # ThreadWBS work_item lifecycle (focus item)
        "!wd": "wbs_done",
        "!wx": "wbs_drop",

        # Debug / inspect
        "!wbs": "wbs_show",
        "!w": "wbs_show",

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
    """
    if not raw:
        return ""
    parts = raw.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------

def _safe_get_channel(message: Any) -> Any:
    return getattr(message, "channel", None)


def _extract_discord_context(message: Any) -> Tuple[str, str, Any]:
    channel = _safe_get_channel(message)
    channel_id = str(getattr(channel, "id", "") or "")
    thread_name = str(getattr(channel, "name", "") or "")
    return channel_id, thread_name, channel


def _extract_author_meta(message: Any) -> Tuple[str, str]:
    author = getattr(message, "author", None)
    author_id = str(getattr(author, "id", "") or "")
    user_name = (
        getattr(author, "display_name", None)
        or getattr(author, "name", None)
        or ""
    )
    return author_id, str(user_name)


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

async def handle_discord_input(message: Any) -> None:
    """
    bot.py → ONLY ENTRY.
    Discord → InputPacket → BIS Pipeline.
    """

    # Bot自身の発言は無視
    if getattr(getattr(message, "author", None), "bot", False):
        return

    raw_content = (getattr(message, "content", "") or "").strip()

    # 空入力は無視（ログ汚染防止）
    if not raw_content:
        return

    command_type = _detect_command_type(raw_content)

    # コマンド以外は現フェーズでは無視
    if command_type is None:
        return

    # Discord context → Ovv context
    channel_id, thread_name, channel = _extract_discord_context(message)

    if not channel_id:
        if DEBUG_BIS:
            print("[Boundary_Gate:WARN] channel_id is empty; drop message.")
        return

    author_id, user_name = _extract_author_meta(message)

    # Discord Thread = task_id = context_key（現行方針）
    context_key = channel_id
    task_id = context_key

    user_meta = {"user_id": author_id, "user_name": user_name}

    # content は downstream でコマンド引数として使われるので、必ず strip して安定化
    content = _strip_head_token(raw_content).strip()

    # 元headを保持（監査/揺れ耐性）
    head = raw_content.split()[0].lower()

    # --------------------------------------------------------
    # InputPacket 構築（FAILSAFE）
    # --------------------------------------------------------
    try:
        packet = InputPacket(
            raw=raw_content,
            source="discord",
            command=command_type,
            content=content,
            author_id=author_id,
            channel_id=channel_id,
            context_key=context_key,
            task_id=task_id,
            user_meta=user_meta,
            meta={
                "discord_channel_id": channel_id,
                "discord_message_id": str(getattr(message, "id", "") or ""),
                "discord_thread_name": thread_name,
                "command_head": head,
            },
        )
    except Exception as e:
        if DEBUG_BIS:
            print("[Boundary_Gate:ERROR] failed to build InputPacket:", repr(e))
            traceback.print_exc()
        return

    # --------------------------------------------------------
    # ★ dbg_packet 用に Packet Capture
    # --------------------------------------------------------
    try:
        capture(packet)
    except Exception as e:
        if DEBUG_BIS:
            print("[Boundary_Gate:WARN] capture(packet) failed:", repr(e))

    if DEBUG_BIS:
        print("[Boundary_Gate] Captured InputPacket:", packet)

    # --------------------------------------------------------
    # BIS Pipeline 実行（Interface_Box → Core → Stabilizer）
    # --------------------------------------------------------
    try:
        final_message = await handle_request(packet)

    except Exception as e:
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
    if final_message and channel is not None:
        try:
            await channel.send(final_message)
        except Exception as e:
            if DEBUG_BIS:
                print("[Boundary_Gate:WARN] failed to send message:", repr(e))
                traceback.print_exc()