# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.0 (Minimal)
#
# ROLE:
#   - ThreadWBS の生成・更新を行う唯一のロジック層
#
# RESPONSIBILITY TAGS:
#   [BUILD]     WBS 初期生成
#   [UPDATE]    work_item / focus_point / status 更新
#   [GUARD]     勝手な LLM 改変を防止
#
# CONSTRAINTS:
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映はユーザー明示コマンドのみ
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List
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
    """
    !t 実行時に呼ばれる。
    CDC 前の空 WBS を生成する。
    """
    return {
        "task": thread_name,
        "status": "empty",            # empty | active | paused | completed
        "work_items": [],             # list[work_item]
        "focus_point": None,          # index of work_items
        "meta": {
            "version": "minimal-1.0",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    }


# ------------------------------------------------------------
# CDC Candidate Handling
# ------------------------------------------------------------

def propose_work_item(wbs: Dict[str, Any], rationale: str) -> Dict[str, Any]:
    """
    CDC により生成された候補を保持する。
    ※この時点では WBS には確定反映しない。
    """
    return {
        "rationale": rationale.strip(),
        "proposed_at": _now_iso(),
    }


def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    !wy による明示承認。
    """
    item = {
        "rationale": candidate["rationale"],
        "created_at": _now_iso(),
    }

    wbs["work_items"].append(item)

    # focus_point は常に「最新の未完了 work_item」
    wbs["focus_point"] = len(wbs["work_items"]) - 1
    wbs["status"] = "active"
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs


def reject_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    !wn による破棄。
    WBS には一切影響しない。
    """
    wbs["meta"]["updated_at"] = _now_iso()
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
) -> Dict[str, Any]:
    """
    !we による編集後採用。
    """
    edited = {
        "rationale": new_rationale.strip(),
        "created_at": _now_iso(),
    }

    wbs["work_items"].append(edited)
    wbs["focus_point"] = len(wbs["work_items"]) - 1
    wbs["status"] = "active"
    wbs["meta"]["updated_at"] = _now_iso()

    return wbs


# ------------------------------------------------------------
# Task State Handling
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tp
    """
    wbs["status"] = "paused"
    wbs["meta"]["updated_at"] = _now_iso()
    return wbs


def on_task_complete(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tc
    """
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    wbs["meta"]["updated_at"] = _now_iso()
    return wbs