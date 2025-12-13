# ============================================================
# MODULE CONTRACT: CORE / Inference / Snapshot Types v0.1
#
# ROLE:
#   - Inference Snapshot の構造定義（Typed Dict 相当）
#
# CONSTRAINTS:
#   - 推論しない
#   - Optional / 未確定を許容
# ============================================================

from __future__ import annotations
from typing import TypedDict, Optional, List, Dict, Any


class SnapshotTask(TypedDict, total=False):
    task_id: str
    title: str
    status: str
    started_at: Optional[str]
    ended_at: Optional[str]
    duration_seconds: Optional[int]


class SnapshotWorkItem(TypedDict, total=False):
    rationale: str
    status: str


class SnapshotWBS(TypedDict, total=False):
    task: str
    status: str
    focus_point: Optional[int]
    work_items: List[SnapshotWorkItem]


class InferenceSnapshot(TypedDict, total=False):
    """
    推論・判断のための Read-Only Snapshot
    """
    context_key: str
    task: SnapshotTask
    wbs: SnapshotWBS
    meta: Dict[str, Any]