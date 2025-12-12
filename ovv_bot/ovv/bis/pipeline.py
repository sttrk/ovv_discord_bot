# ovv/bis/pipeline.py
# ============================================================
# MODULE CONTRACT: BIS / Pipeline v1.1
#   (Debugging Subsystem v1.0 compliant / Thin Adapter)
#
# ROLE:
#   - Interface_Box と Core のあいだの薄いアダプタ。
#   - Core 呼び出し形式を 1 箇所に固定し、将来の差し替えを容易にする。
#
# RESPONSIBILITY TAGS:
#   [ADAPTER]   CoreInput 形式の固定
#   [DISPATCH]  Core 関数呼び出し
#   [DEBUG]     Debugging Subsystem v1.0 観測ログ
#
# CONSTRAINTS (HARD):
#   - Business Logic を持たない
#   - state / notion_ops を加工しない
#   - Core 例外を握りつぶさない（FAILSAFE は Boundary）
#
# DEBUGGING SUBSYSTEM v1.0 COMPLIANCE:
#   - trace_id は Interface_Box / Boundary から受領し、Core にそのまま渡す
#   - チェックポイントは固定・有限
#   - except は必ず構造ログを吐いて raise
# ============================================================

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
import json
import traceback
from datetime import datetime, timezone


# ------------------------------------------------------------
# Debugging Subsystem v1.0 — Checkpoints (Pipeline layer)
# ------------------------------------------------------------

LAYER_CORE = "CORE"

CP_CORE_RECEIVE_PACKET = "CORE_RECEIVE_PACKET"
CP_CORE_EXECUTE = "CORE_EXECUTE"
CP_CORE_RETURN_RESULT = "CORE_RETURN_RESULT"
CP_CORE_EXCEPTION = "CORE_EXCEPTION"


# ------------------------------------------------------------
# Structured logging (observation only)
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(
    *,
    trace_id: str,
    checkpoint: str,
    level: str,
    summary: str,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "trace_id": trace_id or "UNKNOWN",
        "checkpoint": checkpoint,
        "layer": LAYER_CORE,
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
    exc: Exception,
    at: str,
) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        level="ERROR",
        summary=summary,
        error={
            "type": type(exc).__name__,
            "message": str(exc),
            "at": at,
        },
    )


def _get_trace_id(packet: Dict[str, Any]) -> str:
    """
    trace_id 抽出ユーティリティ。
    - packet["trace_id"]
    - packet["meta"]["trace_id"]
    の順で参照し、無ければ UNKNOWN。
    """
    if isinstance(packet, dict):
        tid = packet.get("trace_id")
        if isinstance(tid, str) and tid:
            return tid
        meta = packet.get("meta")
        if isinstance(meta, dict):
            mt = meta.get("trace_id")
            if isinstance(mt, str) and mt:
                return mt
    return "UNKNOWN"


# ============================================================
# Public API
# ============================================================

def build_pipeline(
    core_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    notion_ops: Any,
    state: Optional[Dict[str, Any]],
):
    """
    Interface_Box から呼ばれるビルダ。

    - CoreInput の構造をここで固定する。
    - Debugging Subsystem v1.0 に基づき、
      trace_id / checkpoint を必ず記録する。
    """

    thread_state: Dict[str, Any] = state or {}

    def pipeline(packet: Dict[str, Any]) -> Dict[str, Any]:
        trace_id = _get_trace_id(packet)

        _log_debug(
            trace_id=trace_id,
            checkpoint=CP_CORE_RECEIVE_PACKET,
            summary="pipeline receive packet",
        )

        try:
            _log_debug(
                trace_id=trace_id,
                checkpoint=CP_CORE_EXECUTE,
                summary="call core function",
            )

            core_input: Dict[str, Any] = {
                # ★ Core v2.x / v2.4 が期待する形式に固定
                "command_type": packet.get("command"),
                "raw_text": packet.get("raw"),
                "arg_text": packet.get("content"),
                "task_id": packet.get("task_id"),
                "context_key": packet.get("context_key"),
                "user_id": packet.get("author_id"),
                "thread_wbs": thread_state.get("thread_wbs"),
                # 観測用（Core は無視してよい）
                "trace_id": trace_id,
                "meta": packet.get("meta"),
            }

            result = core_fn(core_input)

            _log_debug(
                trace_id=trace_id,
                checkpoint=CP_CORE_RETURN_RESULT,
                summary="core function returned",
            )

            return result

        except Exception as e:
            _log_error(
                trace_id=trace_id,
                checkpoint=CP_CORE_EXCEPTION,
                summary="exception in pipeline",
                exc=e,
                at="pipeline(core_fn)",
            )
            traceback.print_exc()
            # FAILSAFE は Boundary_Gate が担当
            raise

    return pipeline