# ovv/core/ovv_core.py
# ============================================================
# MODULE CONTRACT: Ovv Core v2.5
#   (Debugging Subsystem v1.0 compliant / Task + CDC Candidate + WBS Mode Normalize)
#
# ROLE:
#   - BIS / Interface_Box から受け取った core_input(dict) を解釈し、
#     Task 系コマンド / WBS 系コマンド / free_chat を振り分ける。
#   - task_create 時にのみ CDC 候補を 1 件生成して返す（固定キー: cdc_candidate）。
#   - WBS コマンドについては「mode の正規化」と「最小のユーザー応答」のみを行う。
#
# DEBUGGING SUBSYSTEM v1.0 COMPLIANCE:
#   - trace_id は Boundary_Gate 生成のものを受領するのみ（Single Trace Rule）
#   - 固定チェックポイントのみを使用（Checkpoint Determinism）
#   - except は必ず構造ログを吐き、握りつぶさず raise
#
# CONSTRAINTS (HARD):
#   - 外部 I/O（DB / Notion / Discord）は一切行わない（純ロジック層）。
#   - WBS の更新 / 永続化 / 候補確定 / Finalize を行わない。
#   - FAILSAFE を持たない（Boundary_Gate に集約）。
# ============================================================

from __future__ import annotations

from typing import Any, Dict
from datetime import datetime, timezone
import json
import traceback


# ============================================================
# Debugging Subsystem v1.0 — Checkpoints (FIXED)
# ============================================================

LAYER_CORE = "CORE"

CP_CORE_RECEIVE_INPUT = "CORE_RECEIVE_INPUT"      # CORE-01
CP_CORE_PARSE_COMMAND = "CORE_PARSE_COMMAND"      # CORE-02
CP_CORE_DISPATCH = "CORE_DISPATCH"                # CORE-03
CP_CORE_BUILD_RESULT = "CORE_BUILD_RESULT"        # CORE-04
CP_CORE_EXCEPTION = "CORE_EXCEPTION"              # CORE-FAIL


# ============================================================
# Structured Logging (Observation Only)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    code: str = "E_CORE",
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


# ============================================================
# trace_id helper
# ============================================================

def _get_trace_id(core_input: Dict[str, Any]) -> str:
    """
    trace_id は Boundary_Gate が唯一生成する。
    Core は受領してログに付与するのみ。
    """
    tid = core_input.get("trace_id")
    if isinstance(tid, str) and tid:
        return tid
    meta = core_input.get("meta") or {}
    mt = meta.get("trace_id")
    if isinstance(mt, str) and mt:
        return mt
    return "UNKNOWN"


# ============================================================
# Public Entry
# ============================================================

def run_core(core_input: Dict[str, Any]) -> Dict[str, Any]:
    trace_id = _get_trace_id(core_input)
    checkpoint = CP_CORE_RECEIVE_INPUT
    _log_debug(trace_id=trace_id, checkpoint=checkpoint, summary="core input received")

    try:
        checkpoint = CP_CORE_PARSE_COMMAND
        _log_debug(trace_id=trace_id, checkpoint=checkpoint, summary="parse core_input")

        command_type = core_input.get("command_type", "free_chat")
        raw_text = core_input.get("raw_text", "") or ""
        arg_text = core_input.get("arg_text", "") or ""
        task_id = core_input.get("task_id")
        context_key = core_input.get("context_key")
        user_id = core_input.get("user_id")

        if task_id is None and context_key is not None:
            task_id = str(context_key)

        checkpoint = CP_CORE_DISPATCH
        _log_debug(
            trace_id=trace_id,
            checkpoint=checkpoint,
            summary=f"dispatch command_type={command_type}",
        )

        # -------------------------
        # Task Commands
        # -------------------------
        if command_type == "task_create":
            return _handle_task_create(trace_id, task_id, arg_text, user_id)

        if command_type == "task_start":
            return _handle_task_start(trace_id, task_id, arg_text)

        if command_type == "task_paused":
            return _handle_task_paused(trace_id, task_id)

        if command_type == "task_end":
            return _handle_task_end(trace_id, task_id)

        # -------------------------
        # WBS Commands (mode normalize only)
        # -------------------------
        if command_type in (
            "wbs_show",
            "wbs_accept",
            "wbs_reject",
            "wbs_edit",
            "wbs_done",
            "wbs_drop",
        ):
            return _handle_wbs_command(trace_id, command_type, task_id)

        # -------------------------
        # Fallback
        # -------------------------
        return _handle_free_chat(trace_id, raw_text, user_id, context_key)

    except Exception as e:
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_CORE_EXCEPTION,
            summary="exception in core",
            exc=e,
            at=checkpoint,
        )
        traceback.print_exc()
        raise  # FAILSAFE は Boundary_Gate が担当


# ============================================================
# Task Handlers
# ============================================================

def _handle_task_create(
    trace_id: str,
    task_id: str | None,
    arg_text: str,
    user_id: str | None,
) -> Dict[str, Any]:
    if task_id is None:
        return {
            "message_for_user": "[task_create] このコマンドはスレッド内でのみ有効です。",
            "mode": "free_chat",
        }

    title = arg_text.strip() or f"Task {task_id}"
    user_label = user_id or "unknown"

    msg = (
        "[task_create] 新しいタスクを登録しました。\n"
        f"- task_id   : {task_id}\n"
        f"- name      : {title}\n"
        f"- created_by: {user_label}\n\n"
        "[CDC] 作業候補を生成しました。承認: !wy / 破棄: !wn / 編集: !we"
    )

    _log_debug(
        trace_id=trace_id,
        checkpoint=CP_CORE_BUILD_RESULT,
        summary="task_create result built",
    )

    return {
        "message_for_user": msg,
        "mode": "task_create",
        "task_name": title,
        "task_id": task_id,
        "cdc_candidate": {
            "rationale": f"{title} を進めるための最初の作業項目を定義する",
        },
    }


def _handle_task_start(trace_id: str, task_id: str | None, arg_text: str) -> Dict[str, Any]:
    if task_id is None:
        return {"message_for_user": "[task_start] スレッド内で実行してください。", "mode": "free_chat"}

    memo = arg_text.strip()
    memo_line = f"- memo   : {memo}\n" if memo else ""

    msg = (
        "[task_start] 学習セッションを開始しました。\n"
        f"- task_id: {task_id}\n"
        f"{memo_line}"
        "※ task_end までの時間が duration に記録されます。"
    )

    return {"message_for_user": msg, "mode": "task_start", "task_id": task_id, "memo": memo}


def _handle_task_paused(trace_id: str, task_id: str | None) -> Dict[str, Any]:
    if task_id is None:
        return {"message_for_user": "[task_paused] スレッド内で実行してください。", "mode": "free_chat"}

    msg = f"[task_paused] 学習を一時停止しました。\n- task_id: {task_id}"
    return {"message_for_user": msg, "mode": "task_paused", "task_id": task_id, "task_summary": msg}


def _handle_task_end(trace_id: str, task_id: str | None) -> Dict[str, Any]:
    if task_id is None:
        return {"message_for_user": "[task_end] スレッド内で実行してください。", "mode": "free_chat"}

    msg = f"[task_end] 学習セッションを終了しました。\n- task_id: {task_id}"
    return {"message_for_user": msg, "mode": "task_end", "task_id": task_id, "task_summary": msg}


# ============================================================
# WBS Handler (mode normalize only)
# ============================================================

def _handle_wbs_command(trace_id: str, command_type: str, task_id: str | None) -> Dict[str, Any]:
    return {
        "message_for_user": f"[{command_type}]",
        "mode": command_type,
        "task_id": task_id,
    }


# ============================================================
# Free chat fallback
# ============================================================

def _handle_free_chat(
    trace_id: str,
    raw_text: str,
    user_id: str | None,
    context_key: str | None,
) -> Dict[str, Any]:
    base = raw_text.strip() or "(empty)"

    msg = (
        "[free_chat] タスク管理モード（Persist / Notion 連携）を優先しています。\n"
        f"- user_id    : {user_id or 'unknown'}\n"
        f"- context_key: {context_key or 'none'}\n\n"
        "---- Echo ----\n"
        f"{base}"
    )

    return {"message_for_user": msg, "mode": "free_chat"}