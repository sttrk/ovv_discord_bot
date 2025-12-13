# ============================================================
# MODULE CONTRACT: CORE / Inference / Snapshot Builder v0.1
#
# ROLE:
#   - PG / ThreadWBS / CoreContext から
#     「推論用 Snapshot」を構築する
#
# RESPONSIBILITY TAGS:
#   [READ_ONLY]   副作用禁止
#   [STRUCTURE]   世界状態の構造化のみ
#
# CONSTRAINTS:
#   - 推論しない
#   - Notion / Discord を触らない
#   - 書き込み禁止
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional
from datetime import datetime

from database.pg import _execute
from database import pg_wbs

from .snapshot_types import (
    InferenceSnapshot,
    SnapshotTask,
    SnapshotWBS,
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.isoformat()


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def build_snapshot(*, context_key: str) -> InferenceSnapshot:
    """
    Inference Snapshot を構築する唯一の入口。

    NOTE:
      - context_key = task_id = thread_id（現行方針）
    """
    snapshot: InferenceSnapshot = {
        "context_key": context_key,
        "meta": {},
    }

    # --------------------------------------------------------
    # Task (PG)
    # --------------------------------------------------------
    task: SnapshotTask = {}

    rows = _execute(
        """
        SELECT task_id, started_at, ended_at, duration_seconds
        FROM task_session
        WHERE task_id = %s
        LIMIT 1
        """,
        (context_key,),
    )

    if rows:
        r = rows[0]
        task.update(
            {
                "task_id": context_key,
                "started_at": _iso(r.get("started_at")),
                "ended_at": _iso(r.get("ended_at")),
                "duration_seconds": r.get("duration_seconds"),
            }
        )

    snapshot["task"] = task

    # --------------------------------------------------------
    # ThreadWBS
    # --------------------------------------------------------
    wbs_raw = None
    try:
        wbs_raw = pg_wbs.load_thread_wbs(context_key)
    except Exception:
        wbs_raw = None

    if isinstance(wbs_raw, dict):
        snapshot["wbs"] = SnapshotWBS(
            task=wbs_raw.get("task"),
            status=wbs_raw.get("status"),
            focus_point=wbs_raw.get("focus_point"),
            work_items=wbs_raw.get("work_items", []),
        )

    return snapshot