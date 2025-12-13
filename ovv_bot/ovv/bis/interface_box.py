# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v1.4
#
# ROLE:
#   - Boundary_Gate から受け取った InputPacket を正規化し、
#     Core に安全に受け渡すための「薄いインターフェース層」。
#   - CoreResult を Stabilizer に橋渡しし、Discord 返却文を確定して返す。
#
# RESPONSIBILITY TAGS:
#   [INTERFACE]   InputPacket 最小正規化
#   [DELEGATE]    Core.handle_packet への完全委譲
#   [BRIDGE]      CoreResult → Stabilizer 変換
#   [GUARD]       不正・不足フィールドの最小ガード
#   [DEBUG]       Debugging Subsystem v1.0（観測のみ）
#
# CONSTRAINTS:
#   - 推論しない
#   - 状態を持たない
#   - 命名・CDC・業務判断は行わない
#   - context_splitter は使用しない
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional
import json

from ovv.bis.types import InputPacket
from ovv.core.ovv_core import handle_packet, CoreResult
from ovv.bis.stabilizer import Stabilizer


# ------------------------------------------------------------
# Debug logging (observation only)
# ------------------------------------------------------------

LAYER_BIS = "BIS"
CP_IFACE_DISPATCH = "IFACE_DISPATCH"


def _trace_id_from_packet(packet: Any) -> str:
    tid = getattr(packet, "trace_id", None)
    if isinstance(tid, str) and tid:
        return tid
    meta = getattr(packet, "meta", None)
    if isinstance(meta, dict):
        mt = meta.get("trace_id")
        if isinstance(mt, str) and mt:
            return mt
    return "UNKNOWN"


def _log_dispatch(packet: InputPacket) -> None:
    payload = {
        "trace_id": _trace_id_from_packet(packet),
        "checkpoint": CP_IFACE_DISPATCH,
        "layer": LAYER_BIS,
        "level": "DEBUG",
        "summary": "interface dispatch to core",
    }
    print(json.dumps(payload, ensure_ascii=False))


def _safe_user_id(packet: InputPacket) -> str:
    user_meta = getattr(packet, "user_meta", None)
    if isinstance(user_meta, dict):
        uid = user_meta.get("user_id")
        if uid is not None:
            return str(uid)
    # Executor 側は user_id を要求するため空文字でフォールバック
    return ""


def _safe_context_key(packet: InputPacket) -> str:
    v = getattr(packet, "context_key", None)
    return str(v) if v is not None else ""


def _safe_task_id(packet: InputPacket) -> str:
    v = getattr(packet, "task_id", None)
    return str(v) if v is not None else ""


def _safe_command_type(packet: InputPacket) -> str:
    v = getattr(packet, "command", None)
    return str(v) if isinstance(v, str) else "unknown"


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

async def handle_request(packet: InputPacket) -> str:
    """
    Interface_Box の単一エントリ（Boundary_Gate → await）。

    Flow:
      1) guard
      2) Core.handle_packet(packet)
      3) Stabilizer(...).finalize()
      4) Discord へ返す最終メッセージ(str) を返却
    """

    # Guard（Boundary_Gate が保証する前提だが、最小で落とす）
    if not isinstance(packet, InputPacket):
        return "Invalid input packet."

    # 観測のみ
    _log_dispatch(packet)

    # 1) Core へ完全委譲（同期）
    try:
        core_result: CoreResult = handle_packet(packet)
    except Exception:
        # Interface 層で握り潰さず、Discord 側へ安全な固定文言を返す
        # （詳細は Boundary_Gate / Stabilizer の構造ログに委譲）
        return "Core execution failed."

    # 2) Stabilizer へ橋渡し（最小マッピング）
    message_for_user = getattr(core_result, "discord_output", "") or ""
    notion_ops = getattr(core_result, "notion_ops", None)

    context_key = _safe_context_key(packet)
    user_id = _safe_user_id(packet)
    task_id = _safe_task_id(packet)
    command_type = _safe_command_type(packet)

    # Stabilizer は trace_id を core_output/meta から抽出するため、最低限の受け渡しを用意
    core_output: Dict[str, Any] = {
        "trace_id": _trace_id_from_packet(packet),
        "mode": command_type,
    }

    # thread_state は最小で wbs を渡す（finalized_item 連携は Core 側実装が必要）
    thread_state: Dict[str, Any] = {}
    wbs = getattr(core_result, "wbs", None)
    if isinstance(wbs, dict):
        thread_state["wbs"] = wbs

    st = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=context_key,
        user_id=user_id,
        task_id=task_id,
        command_type=command_type,
        core_output=core_output,
        thread_state=thread_state,
    )

    # 3) 最終確定（非同期）
    try:
        return await st.finalize()
    except Exception:
        # Stabilizer は内部で例外をログ化する設計だが、
        # Interface_Box 側も “返す” を最優先し固定文言でフォールバックする
        return message_for_user or "Stabilizer finalize failed."