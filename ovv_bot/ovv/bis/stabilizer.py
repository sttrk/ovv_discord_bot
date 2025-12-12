# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer v3.11
#   (Debugging Subsystem v1.0 compliant / Final Output Layer)
#
# ROLE:
#   - BIS の最終統合レイヤ。
#   - Core / Interface_Box 出力をもとに：
#         [1] Persist v3.0 への書き込み
#         [2] NotionOps の拡張（duration / summary）
#         [3] work_item finalized（done / dropped）を
#             NotionTaskSummary に append
#         [4] Notion API の逐次実行
#         [5] Discord へ返す最終メッセージ確定
#
# DEBUGGING SUBSYSTEM v1.0:
#   - trace_id を必ずログに含める
#   - チェックポイントは固定・有限
#   - 例外はログに残し、最終出力は必ず返す（No Silent Death）
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

CP_ST_RECEIVE_RESULT = "ST_RECEIVE_RESULT"      # ST-01
CP_ST_SANITIZE = "ST_SANITIZE"                  # ST-02
CP_ST_FORMAT_OUTPUT = "ST_FORMAT_OUTPUT"        # ST-03
CP_ST_SEND_DISCORD = "ST_SEND_DISCORD"          # ST-04
CP_ST_EXCEPTION = "ST_EXCEPTION"                # ST-FAIL


# ============================================================
# Structured Logging (Observation Only)
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


def _get_trace_id(context_key: Optional[str], core_output: Dict[str, Any]) -> str:
    """
    Stabilizer は trace_id を生成しない。
    受領のみ。

    優先順:
      1. core_output["trace_id"]
      2. core_output["meta"]["trace_id"]
      3. context_key fallback
    """
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
    """
    BIS Final Layer:
        Persist → NotionOps → Discord Response
    """

    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Any],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
        command_type: Optional[str] = None,
        core_output: Optional[Dict[str, Any]] = None,
        thread_state: Optional[Dict[str, Any]] = None,
    ):
        self.message_for_user = message_for_user or ""
        self.notion_ops = self._normalize_ops(notion_ops)

        self.context_key = context_key
        self.user_id = user_id
        self.task_id = str(task_id) if task_id is not None else None
        self.command_type = command_type

        self.core_output = core_output or {}
        self.thread_state = thread_state or {}

        self.trace_id = _get_trace_id(context_key, self.core_output)
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
    # [PERSIST]
    # ========================================================

    def _write_persist(self) -> None:
        if not self.task_id:
            return

        now = datetime.now(timezone.utc)
        event = self.command_type or "unknown"

        insert_task_log(
            task_id=self.task_id,
            event_type=event,
            content=self.message_for_user or "",
            created_at=now,
        )

        if event == "task_start":
            insert_task_session_start(
                task_id=self.task_id,
                user_id=self.user_id,
                started_at=now,
            )

        elif event == "task_end":
            self._last_duration_seconds = insert_task_session_end_and_duration(
                task_id=self.task_id,
                ended_at=now,
            )

    # ========================================================
    # [BUILD_OPS]
    # ========================================================

    def _augment_duration(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if (
            self.command_type == "task_end"
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
        if self.command_type not in ("task_paused", "task_end"):
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
            self.command_type not in ("wbs_done", "wbs_drop")
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

        # 1. Persist
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
                retryable=False,
            )
            traceback.print_exc()

        # 2. Build ops
        _log_debug(
            trace_id=self.trace_id,
            checkpoint=CP_ST_SANITIZE,
            summary="build notion ops",
        )

        ops = list(self.notion_ops)
        ops = self._augment_duration(ops)
        ops = self._augment_summary(ops)
        ops = self._augment_wbs_finalize_append(ops)

        # 3. Execute ops
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

        # 4. Return output
        _log_debug(
            trace_id=self.trace_id,
            checkpoint=CP_ST_SEND_DISCORD,
            summary="return discord output",
        )

        return self.message_for_user