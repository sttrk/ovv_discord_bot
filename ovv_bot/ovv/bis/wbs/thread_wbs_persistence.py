# ovv/bis/wbs/thread_wbs_persistence.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Persistence v1.1
#   (Minimal + Debugging Subsystem v1.0 compliant / Observation Only)
#
# ROLE:
#   - thread_id ↔ ThreadWBS(JSON text) の永続化を担当する。
#   - 保存（UPSERT）と取得（LOAD）のみを行う。
#
# RESPONSIBILITY TAGS:
#   [PERSIST]   ThreadWBS の DB 永続化
#   [LOAD]      ThreadWBS の取得
#   [MINIMAL]   STEP A 用の最小責務実装
#   [STRICT]    構造解釈・編集・推論を一切行わない
#   [DEBUG]     Debugging Subsystem v1.0 観測ログ（挙動非変更）
#
# CONSTRAINTS (HARD):
#   - ThreadWBS の構造を解釈しない
#   - CDC / Builder / Interface_Box ロジックを含めない
#   - Persist v3.0 の接続管理に完全追従する
#   - 独自 connection / commit / close を行わない
#   - 1 thread_id = 1 row を厳守する
#
# DEBUGGING SUBSYSTEM v1.0 (OBSERVATION ONLY):
#   - trace_id は Boundary / Interface_Box から渡されるのが理想だが、
#     本モジュールでは optional とし、無い場合は "UNKNOWN" を使用。
#   - チェックポイントは CORE_* を流用し、新規定義しない。
#   - except はログを出し、仕様通り None / no-op で返す。
# ============================================================

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import datetime
import traceback

from database.pg import init_db


# ------------------------------------------------------------
# Debugging Subsystem v1.0 (Fixed checkpoints)
# ------------------------------------------------------------

LAYER_CORE = "CORE"

CP_CORE_RECEIVE_PACKET = "CORE_RECEIVE_PACKET"
CP_CORE_EXECUTE = "CORE_EXECUTE"
CP_CORE_RETURN_RESULT = "CORE_RETURN_RESULT"
CP_CORE_EXCEPTION = "CORE_EXCEPTION"


# ------------------------------------------------------------
# Time helper
# ------------------------------------------------------------

def _now_utc() -> datetime.datetime:
    """
    UTC 現在時刻を返す。
    Persist 側の TIMESTAMP と整合させるため naive UTC を使用。
    """
    return datetime.datetime.utcnow()


# ------------------------------------------------------------
# Structured logging (observation only)
# ------------------------------------------------------------

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
        "timestamp": _now_utc().isoformat(),
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


def _tid(trace_id: Optional[str]) -> str:
    return trace_id if isinstance(trace_id, str) and trace_id else "UNKNOWN"


# ============================================================
# Public API
# ============================================================

def load_thread_wbs(thread_id: str, *, trace_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    [LOAD]
    ThreadWBS を取得する。

    Returns:
        - Dict[str, Any]: JSON を復元した WBS
        - None: 未存在 or JSON 破損時
    """
    tid = _tid(trace_id)
    _log_debug(
        trace_id=tid,
        checkpoint=CP_CORE_RECEIVE_PACKET,
        summary=f"load_thread_wbs start (thread_id={thread_id})",
    )

    sql = """
        SELECT wbs_json
        FROM thread_wbs
        WHERE thread_id = %s
        LIMIT 1;
    """

    try:
        conn = init_db()
        with conn.cursor() as cur:
            cur.execute(sql, (thread_id,))
            row = cur.fetchone()

            if not row:
                _log_debug(
                    trace_id=tid,
                    checkpoint=CP_CORE_RETURN_RESULT,
                    summary="load_thread_wbs: not found",
                )
                return None

            raw = row[0]
            try:
                wbs = json.loads(raw)
                _log_debug(
                    trace_id=tid,
                    checkpoint=CP_CORE_RETURN_RESULT,
                    summary="load_thread_wbs: success",
                )
                return wbs
            except Exception as e:
                # JSON 破損時は破綻回避を優先し None
                _log_error(
                    trace_id=tid,
                    checkpoint=CP_CORE_EXCEPTION,
                    summary="load_thread_wbs: json decode failed",
                    exc=e,
                    at="json.loads",
                )
                return None

    except Exception as e:
        _log_error(
            trace_id=tid,
            checkpoint=CP_CORE_EXCEPTION,
            summary="load_thread_wbs: db error",
            exc=e,
            at="db/select",
        )
        traceback.print_exc()
        return None


def save_thread_wbs(
    thread_id: str,
    wbs_json: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> None:
    """
    [PERSIST]
    ThreadWBS を保存する。

    動作:
        - row が存在しない場合: INSERT
        - row が存在する場合: UPDATE（上書き）
    """
    tid = _tid(trace_id)
    _log_debug(
        trace_id=tid,
        checkpoint=CP_CORE_EXECUTE,
        summary=f"save_thread_wbs start (thread_id={thread_id})",
    )

    try:
        raw = json.dumps(wbs_json, ensure_ascii=False)
    except Exception as e:
        _log_error(
            trace_id=tid,
            checkpoint=CP_CORE_EXCEPTION,
            summary="save_thread_wbs: json encode failed",
            exc=e,
            at="json.dumps",
        )
        return

    sql = """
        INSERT INTO thread_wbs (thread_id, wbs_json, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            wbs_json = EXCLUDED.wbs_json,
            updated_at = EXCLUDED.updated_at;
    """

    try:
        conn = init_db()
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    thread_id,
                    raw,
                    _now_utc(),
                ),
            )

        _log_debug(
            trace_id=tid,
            checkpoint=CP_CORE_RETURN_RESULT,
            summary="save_thread_wbs: success",
        )

    except Exception as e:
        _log_error(
            trace_id=tid,
            checkpoint=CP_CORE_EXCEPTION,
            summary="save_thread_wbs: db error",
            exc=e,
            at="db/upsert",
        )
        traceback.print_exc()
        return