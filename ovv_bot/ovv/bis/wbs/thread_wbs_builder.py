# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.1 (Minimal + Finalize)
#
# ROLE:
#   - ThreadWBS の生成・更新を行う唯一のロジック層
#
# RESPONSIBILITY TAGS:
#   [BUILD]     WBS 初期生成
#   [UPDATE]    work_item / focus_point / status 更新
#   [FINALIZE]  work_item の done / dropped 確定
#   [GUARD]     勝手な LLM 改変を防止
#
# CONSTRAINTS:
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映はユーザー明示コマンドのみ
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------
# WBS Factory
# ------------------------------------------------------------

def create_empty_wbs(thread_name: str) -> Dict[str, Any]:
    return {
        "task": thread_name,
        "status": "empty",            # empty | active | paused | completed
        "work_items": [],
        "focus_point": None,
        "meta": {
            "version": "minimal-1.1",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    }


# ------------------------------------------------------------
# CDC Candidate Handling
# ------------------------------------------------------------

def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:

    item = {
        "rationale": candidate["rationale"],
        "created_at": _now_iso(),
    }

    wbs["work_items"].append(item)
    wbs["focus_point"] = len(wbs["work_items"]) - 1
    wbs["status"] = "active"
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
) -> Dict[str, Any]:

    item = {
        "rationale": new_rationale.strip(),
        "created_at": _now_iso(),
    }

    wbs["work_items"].append(item)
    wbs["focus_point"] = len(wbs["work_items"]) - 1
    wbs["status"] = "active"
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs


# ------------------------------------------------------------
# Task State Handling
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any]) -> Dict[str, Any]:
    wbs["status"] = "paused"
    wbs["meta"]["updated_at"] = _now_iso()
    return wbs


def on_task_complete(wbs: Dict[str, Any]) -> Dict[str, Any]:
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    wbs["meta"]["updated_at"] = _now_iso()
    return wbs


# ------------------------------------------------------------
# FINALIZE: work_item done / dropped
# ------------------------------------------------------------

def mark_focus_done(
    wbs: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    focus_point の work_item を完了として確定する。
    """
    idx = wbs.get("focus_point")
    if idx is None:
        return wbs, None

    try:
        item = wbs["work_items"][idx]
    except (IndexError, KeyError, TypeError):
        return wbs, None

    finalized = {
        "rationale": item.get("rationale", ""),
        "status": "done",
        "finalized_at": _now_iso(),
        "index": idx,
    }

    # focus を解除（次の work_item は自動選定しない）
    wbs["focus_point"] = None
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs, finalized


def mark_focus_dropped(
    wbs: Dict[str, Any],
    reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    focus_point の work_item を破棄として確定する。
    """
    idx = wbs.get("focus_point")
    if idx is None:
        return wbs, None

    try:
        item = wbs["work_items"][idx]
    except (IndexError, KeyError, TypeError):
        return wbs, None

    rationale = item.get("rationale", "")
    if reason:
        rationale = f"{rationale} (dropped: {reason})"

    finalized = {
        "rationale": rationale,
        "status": "dropped",
        "finalized_at": _now_iso(),
        "index": idx,
    }

    wbs["focus_point"] = None
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs, finalized