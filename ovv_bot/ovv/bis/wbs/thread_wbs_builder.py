# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.1 (Minimal Integrated)
#
# ROLE:
#   - ThreadWBS の生成・更新を行う唯一のロジック層
#   - work_item の state を厳格に管理し、!tp ≠ done を保証する
#
# RESPONSIBILITY TAGS:
#   [BUILD]     WBS 初期生成
#   [UPDATE]    work_item / focus_point / status 更新
#   [STATE]     work_item.state の更新（active/paused/done/dropped）
#   [GUARD]     勝手な LLM 改変を防止（明示コマンドのみ反映）
#
# CONSTRAINTS (HARD):
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映はユーザー明示コマンドのみ
#   - !tp は work_item を done にしない（paused のみ）
#   - done/dropped の確定は「別コマンド層」（後続仕様）でのみ行う
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone


# ------------------------------------------------------------
# constants (minimal)
# ------------------------------------------------------------

WBS_STATUS_EMPTY = "empty"
WBS_STATUS_ACTIVE = "active"
WBS_STATUS_PAUSED = "paused"
WBS_STATUS_COMPLETED = "completed"

ITEM_STATE_ACTIVE = "active"
ITEM_STATE_PAUSED = "paused"
ITEM_STATE_DONE = "done"
ITEM_STATE_DROPPED = "dropped"


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _safe_meta(wbs: Dict[str, Any]) -> Dict[str, Any]:
    meta = wbs.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        wbs["meta"] = meta
    return meta


def _touch(wbs: Dict[str, Any]) -> None:
    meta = _safe_meta(wbs)
    meta["updated_at"] = _now_iso()


def _get_focus_index(wbs: Dict[str, Any]) -> Optional[int]:
    fp = wbs.get("focus_point", None)
    if isinstance(fp, int) and fp >= 0:
        return fp
    return None


def _get_focus_item(wbs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = _safe_list(wbs.get("work_items"))
    fp = _get_focus_index(wbs)
    if fp is None or fp >= len(items):
        return None
    it = items[fp]
    return it if isinstance(it, dict) else None


def _set_status_active(wbs: Dict[str, Any]) -> None:
    # 最小: work_items が存在し focus があるなら active を基本とする
    wbs["status"] = WBS_STATUS_ACTIVE


# ------------------------------------------------------------
# WBS Factory
# ------------------------------------------------------------

def create_empty_wbs(thread_name: str) -> Dict[str, Any]:
    """
    !t 実行時に呼ばれる。
    CDC 前の空 WBS を生成する。
    """
    now = _now_iso()
    return {
        "task": (thread_name or "").strip(),
        "status": WBS_STATUS_EMPTY,     # empty | active | paused | completed
        "work_items": [],              # list[work_item]
        "focus_point": None,           # index of work_items
        "meta": {
            "version": "minimal-1.1",
            "created_at": now,
            "updated_at": now,
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
        "rationale": (rationale or "").strip(),
        "proposed_at": _now_iso(),
    }


def accept_work_item(wbs: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    !wy による明示承認。
    """
    rationale = ""
    if isinstance(candidate, dict):
        rationale = (candidate.get("rationale") or "").strip()

    item = {
        "rationale": rationale,
        "state": ITEM_STATE_ACTIVE,    # v1.1: state を必須化
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    items = _safe_list(wbs.get("work_items"))
    items.append(item)
    wbs["work_items"] = items

    # focus_point は常に「最新の active work_item」
    wbs["focus_point"] = len(items) - 1
    _set_status_active(wbs)
    _touch(wbs)
    return wbs


def reject_work_item(wbs: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    !wn による破棄。
    WBS には一切影響しない（仕様）。
    """
    _touch(wbs)
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
) -> Dict[str, Any]:
    """
    !we による編集後採用。
    """
    rationale = (new_rationale or "").strip()

    edited = {
        "rationale": rationale,
        "state": ITEM_STATE_ACTIVE,    # v1.1
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    items = _safe_list(wbs.get("work_items"))
    items.append(edited)
    wbs["work_items"] = items

    wbs["focus_point"] = len(items) - 1
    _set_status_active(wbs)
    _touch(wbs)
    return wbs


# ------------------------------------------------------------
# Task State Handling
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tp
    v1.1:
      - WBS.status を paused にする
      - focus_item が存在すれば state を paused にする
      - done/dropped には絶対にしない（!tp ≠ done）
    """
    wbs["status"] = WBS_STATUS_PAUSED

    focus = _get_focus_item(wbs)
    if focus is not None:
        # state が未設定でもここで矯正
        focus["state"] = ITEM_STATE_PAUSED
        focus["updated_at"] = _now_iso()

    _touch(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tc
    v1.1（最小）:
      - WBS 全体として completed にする
      - focus_point を None
    NOTE:
      - work_item を done/dropped に確定する責務は v1.1 では持たない
        （移送・確定は後続レイヤ/コマンドで実装）
    """
    wbs["status"] = WBS_STATUS_COMPLETED
    wbs["focus_point"] = None
    _touch(wbs)
    return wbs


# ------------------------------------------------------------
# Optional: work_item state transitions (reserved for next step)
# ------------------------------------------------------------

def mark_focus_done(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    予約: focus work_item を done にする（将来コマンド用）。
    現行の Interface_Box / Boundary_Gate からは呼ばない想定。
    """
    focus = _get_focus_item(wbs)
    if focus is None:
        _touch(wbs)
        return wbs

    focus["state"] = ITEM_STATE_DONE
    focus["updated_at"] = _now_iso()
    _touch(wbs)
    return wbs


def mark_focus_dropped(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    予約: focus work_item を dropped にする（将来コマンド用）。
    現行の Interface_Box / Boundary_Gate からは呼ばない想定。
    """
    focus = _get_focus_item(wbs)
    if focus is None:
        _touch(wbs)
        return wbs

    focus["state"] = ITEM_STATE_DROPPED
    focus["updated_at"] = _now_iso()
    _touch(wbs)
    return wbs