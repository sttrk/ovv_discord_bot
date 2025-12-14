# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.11 (FIXED / SANITIZED)
#
# ROLE:
#   - CoreResult を受け取り、最終的な副作用（Persist / Notion）を制御
#   - Discord へ返す文字列を唯一保証する「最終安定化層」
#
# CONSTRAINTS:
#   - 推論しない
#   - Core / WBS の意味構造を改変しない
#   - 副作用は mode に基づき明示的に制御する
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, List
from datetime import datetime, timezone
import json
import traceback

from ovv.external_services.notion.ops.executor import execute_notion_ops
from database.pg import (
    insert_task_session_start,
    insert_task_session_end_and_duration,
    insert_task_log,
)

# ============================================================
# Debugging Subsystem v1.0 — Checkpoints (FIXED)
# ============================================================

LAYER_ST = "ST"

CP_ST_RECEIVE_RESULT = "ST_RECEIVE_RESULT"
CP_ST_SANITIZE = "ST_SANITIZE"
CP_ST_SEND_DISCORD = "ST_SEND_DISCORD"
CP_ST_EXCEPTION = "ST_EXCEPTION"


# ============================================================
# Logging
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(
    *,
    trace_id: str,
    checkpoint: str,
    layer: str,
    level: str,
    summary: str,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "trace_id": trace_id,
        "checkpoint": checkpoint,
        "layer": layer,
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
        layer=LAYER_ST,
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
        layer=LAYER_ST,
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


def _resolve_trace_id(context_key: Optional[str], core_output: Dict[str, Any]) -> str:
    tid = core_output.get("trace_id")
    if isinstance(tid, str) and tid:
        return tid
    meta = core_output.get("meta")
    if isinstance(meta, dict):
        mt = meta.get("trace_id")
        if isinstance(mt, str) and mt:
            return mt
    return str(context_key or "UNKNOWN")


# ============================================================
# Stabilizer
# ============================================================

class Stabilizer:
    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Any],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
        command_type: Optional[str] = None,  # 互換のため残す（使用しない）
        core_output: Optional[Dict[str, Any]] = None,
        thread_state: Optional[Dict[str, Any]] = None,
    ):
        self.message_for_user = str(message_for_user or "")
        self.notion_ops = self._normalize_ops(notion_ops)

        self.context_key = context_key
        self.user_id = user_id
        self.task_id = str(task_id) if task_id is not None else None

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        self.mode: str = str(self.core_output.get("mode") or "unknown")
        self.trace_id = _resolve_trace_id(context_key, self.core_output)

        self._last_duration_seconds: Optional[int] = None

    # ========================================================
    # Utils
    # ========================================================

    @staticmethod
    def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            return [raw]
        return []

    # ========================================================
    # [SANITIZE]
    # ========================================================

    def _sanitize(self) -> None:
        """
        Stabilizer 入力の最小正規化。
        - 副作用条件を壊さない
        - 文字列 / list / dict のみ保証
        """
        self.message_for_user = str(self.message_for_user or "").strip()
        if not isinstance(self.core_output, dict):
            self.core_output = {}
        if not isinstance(self.thread_state, dict):
            self.thread_state = {}

    # ========================================================
    # [PERSIST]
    # ========================================================

    def _write_persist(self) -> None:
        if not self.task_id:
            return

        now = datetime.now(timezone.utc)

        insert_task_log(
            task_id=self.task_id,
            event_type=self.mode,
            content=self.message_for_user or "",
            created_at=now,
        )

        if self.mode == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif self.mode == "task_end":
            self._last_duration_seconds = insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

    # ========================================================
    # [OPS AUGMENT]
    # ========================================================

    def _augment_duration(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if (
            self.mode == "task_end"
            and self._last_duration_seconds is not None
            and self.task_id
        ):
            ops.append(
                {
                    "op": "update_task_duration",
                    "task_id": self.task_id,
                    "duration_seconds": self._last_duration_seconds,
                }
            )
        return ops

    def _build_summary_text(self) -> str:
        for key in ("task_summary", "summary_text", "task_summary_text"):
            v = self.core_output.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return self.message_for_user.strip()

    def _augment_summary(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self.mode not in ("task_paused", "task_end"):
            return ops
        if not self.task_id:
            return ops

        summary = self._build_summary_text()
        if summary:
            ops.append(
                {
                    "op": "update_task_summary",
                    "task_id": self.task_id,
                    "summary_text": summary,
                }
            )
        return ops

    def _augment_wbs_finalize_append(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        finalized = self.thread_state.get("finalized_item")
        if (
            self.mode not in ("wbs_done", "wbs_drop")
            or not self.task_id
            or not isinstance(finalized, dict)
        ):
            return ops

        status = finalized.get("status", "done")
        rationale = finalized.get("rationale") or "<no rationale>"
        ops.append(
            {
                "op": "append_task_summary",
                "task_id": self.task_id,
                "append_text": f"[WBS:{status}] {rationale}",
            }
        )
        return ops

    # ========================================================
    # [FINAL]
    # ========================================================

    async def finalize(self) -> str:
        _log_debug(
            trace_id=self.trace_id,
            checkpoint=CP_ST_RECEIVE_RESULT,
            summary="stabilizer receive result",
        )

        _log_debug(
            trace_id=self.trace_id,
            checkpoint=CP_ST_SANITIZE,
            summary="sanitize stabilizer input",
        )
        self._sanitize()

        try:
            self._write_persist()
        except Exception as e:
            _log_error(
                trace_id=self.trace_id,
                checkpoint=CP_ST_EXCEPTION,
                summary="persist failure",
                code="E_ST_PERSIST",
                exc=e,
                at="PERSIST",
            )
            traceback.print_exc()

        ops = list(self.notion_ops)
        ops = self._augment_duration(ops)
        ops = self._augment_summary(ops)
        ops = self._augment_wbs_finalize_append(ops)

        if ops:
            try:
                await execute_notion_ops(
                    ops,
                    context_key=self.context_key,
                    user_id=self.user_id,
                )
            except Exception as e:
                _log_error(
                    trace_id=self.trace_id,
                    checkpoint=CP_ST_EXCEPTION,
                    summary="notion ops execution failed",
                    code="E_ST_NOTION",
                    exc=e,
                    at="EXEC_OPS",
                    retryable=True,
                )
                traceback.print_exc()

        _log_debug(
            trace_id=self.trace_id,
            checkpoint=CP_ST_SEND_DISCORD,
            summary="return discord output",
        )

        return self.message_for_user