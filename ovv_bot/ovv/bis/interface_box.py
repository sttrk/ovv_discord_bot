# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v1.4 (FINAL)
#
# ROLE:
#   - Boundary_Gate から受け取った InputPacket を Core に委譲
#   - CoreResult を Stabilizer に橋渡しし、Discord 返却文を確定して返す
#
# RESPONSIBILITY TAGS:
#   [INTERFACE]   InputPacket 最小ガード
#   [DELEGATE]    Core.handle_packet への完全委譲
#   [BRIDGE]      CoreResult → Stabilizer 変換（無加工）
#   [DEBUG]       Debugging Subsystem v1.0（観測のみ）
#
# CONSTRAINTS:
#   - 推論しない
#   - 状態を持たない
#   - Core の意味構造を改変しない
# ============================================================

from __future__ import annotations

from typing import Any
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


def _safe_str(v: Any) -> str:
    return str(v) if v is not None else ""


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

async def handle_request(packet: InputPacket) -> str:
    """
    Boundary_Gate → await される唯一の入口。

    Flow:
      1) guard
      2) Core.handle_packet
      3) Stabilizer.finalize
      4) Discord 返却文(str)
    """

    # --- Guard ---
    if not isinstance(packet, InputPacket):
        return "Invalid input packet."

    _log_dispatch(packet)

    # --- Core ---
    try:
        core_result: CoreResult = handle_packet(packet)
    except Exception:
        return "Core execution failed."

    # --- Stabilizer bridge (NO interpretation) ---
    st = Stabilizer(
        message_for_user=core_result.discord_output,
        notion_ops=core_result.notion_ops,
        context_key=_safe_str(packet.context_key),
        user_id=_safe_str(packet.user_meta.get("user_id") if isinstance(packet.user_meta, dict) else ""),
        task_id=_safe_str(packet.task_id),
        command_type=_safe_str(packet.command),
        core_output=core_result.core_output or {},
        thread_state=core_result.wbs or {},
    )

    # --- Finalize ---
    try:
        return await st.finalize()
    except Exception:
        return core_result.discord_output or "Stabilizer finalize failed."