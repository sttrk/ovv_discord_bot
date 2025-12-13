# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v1.5
#
# ROLE:
#   - Boundary_Gate から受け取った InputPacket を Core に委譲
#   - CoreResult を Stabilizer に橋渡しして最終 Discord 出力(str)を返す
#
# RESPONSIBILITY TAGS:
#   [INTERFACE]   InputPacket 最小ガード
#   [DELEGATE]    Core.handle_packet への完全委譲
#   [BRIDGE]      CoreResult → Stabilizer 変換（無加工）
#   [DEBUG]       Debugging Subsystem v1.0（観測のみ）
#   [NO_SILENT]   例外は必ずログ化し、Boundary_Gate FAILSAFE へ集約
#
# CONSTRAINTS:
#   - 推論しない
#   - 状態を持たない
#   - Core の意味構造(core_output/wbs)を改変しない
# ============================================================

from __future__ import annotations

from typing import Any, Dict
import json
import traceback
from datetime import datetime, timezone

from ovv.bis.types import InputPacket
from ovv.core.ovv_core import handle_packet, CoreResult
from ovv.bis.stabilizer import Stabilizer


# ============================================================
# Debugging Subsystem v1.0 — Checkpoints (FIXED)
# ============================================================

LAYER_IF = "IF"

CP_IF_ENTRY = "IF_ENTRY"
CP_IF_DISPATCH_CORE = "IF_DISPATCH_CORE"
CP_IF_CORE_OK = "IF_CORE_OK"
CP_IF_STABILIZE = "IF_STABILIZE"
CP_IF_EXCEPTION = "IF_EXCEPTION"


# ============================================================
# Structured logging (observation only)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _log_event(
    *,
    trace_id: str,
    checkpoint: str,
    level: str,
    summary: str,
    error: Dict[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "trace_id": trace_id,
        "checkpoint": checkpoint,
        "layer": LAYER_IF,
        "level": level,
        "summary": summary,
        "timestamp": _now_iso(),
    }
    if error is not None:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False))


def _log_debug(*, trace_id: str, checkpoint: str, summary: str) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        level="DEBUG",
        summary=summary,
    )


def _log_error(
    *,
    trace_id: str,
    checkpoint: str,
    summary: str,
    code: str,
    exc: Exception,
    at: str,
    retryable: bool = False,
) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        level="ERROR",
        summary=summary,
        error={
            "code": code,
            "type": type(exc).__name__,
            "message": str(exc),
            "at": at,
            "retryable": retryable,
        },
    )


def _safe_str(v: Any) -> str:
    return str(v) if v is not None else ""


def _safe_user_id(packet: InputPacket) -> str:
    user_meta = getattr(packet, "user_meta", None)
    if isinstance(user_meta, dict):
        uid = user_meta.get("user_id")
        if uid is not None:
            return str(uid)
    return ""


# ============================================================
# Public entry
# ============================================================

async def handle_request(packet: InputPacket) -> str:
    """
    Boundary_Gate → await される唯一の入口。

    Flow:
      1) guard
      2) Core.handle_packet(packet)
      3) Stabilizer.finalize()
      4) Discord 返却文(str)
    """

    trace_id = _trace_id_from_packet(packet)
    _log_debug(trace_id=trace_id, checkpoint=CP_IF_ENTRY, summary="interface entry")

    # --- Guard ---
    if not isinstance(packet, InputPacket):
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_IF_EXCEPTION,
            summary="invalid input packet type",
            code="E_IF_GUARD",
            exc=TypeError(f"expected InputPacket, got {type(packet)}"),
            at="IF_GUARD",
            retryable=False,
        )
        # ここは Boundary_Gate まで上げても意味が薄いので固定文言
        return "Invalid input packet."

    # --- Core ---
    _log_debug(trace_id=trace_id, checkpoint=CP_IF_DISPATCH_CORE, summary="dispatch core.handle_packet")
    try:
        core_result: CoreResult = handle_packet(packet)
        _log_debug(trace_id=trace_id, checkpoint=CP_IF_CORE_OK, summary="core returned CoreResult")
    except Exception as e:
        # 重要：ここで握りつぶさない。必ずログ→再送出し、BG_FAILSAFE に集約。
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_IF_EXCEPTION,
            summary="core raised exception (will re-raise to Boundary_Gate failsafe)",
            code="E_IF_CORE",
            exc=e,
            at="CORE",
            retryable=False,
        )
        traceback.print_exc()
        raise  # Boundary_Gate が FAILSAFE で返す（Single Failure Exit）

    # --- Stabilizer bridge (NO interpretation) ---
    _log_debug(trace_id=trace_id, checkpoint=CP_IF_STABILIZE, summary="bridge to stabilizer.finalize")

    st = Stabilizer(
        message_for_user=core_result.discord_output,
        notion_ops=core_result.notion_ops,
        context_key=_safe_str(packet.context_key),
        user_id=_safe_user_id(packet),
        task_id=_safe_str(packet.task_id),
        command_type=_safe_str(packet.command),
        core_output=core_result.core_output or {},   # ★ Core の構造をそのまま
        thread_state=core_result.wbs or {},          # ★ Core の wbs をそのまま（finalized_item 将来対応）
    )

    try:
        return await st.finalize()
    except Exception as e:
        # Stabilizer 自身も No Silent Death だが、IF でも観測を残す。
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_IF_EXCEPTION,
            summary="stabilizer.finalize raised exception (return best-effort)",
            code="E_IF_ST",
            exc=e,
            at="STABILIZER",
            retryable=True,
        )
        traceback.print_exc()
        # 最終的にはユーザー文言を返す（IF 層としてのフォールバック）
        return core_result.discord_output or "Stabilizer finalize failed."