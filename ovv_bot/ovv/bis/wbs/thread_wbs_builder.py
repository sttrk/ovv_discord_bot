# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.1 (Minimal)
#
# ROLE:
#   - ThreadWBS の生成・更新を行う唯一のロジック層
#
# RESPONSIBILITY TAGS:
#   [BUILD]     WBS 初期生成
#   [UPDATE]    work_item / focus_point / status 更新
#   [GUARD]     勝手な LLM 改変を防止
#   [LIFECYCLE] work_item の done / dropped を確定
#
# CONSTRAINTS (HARD):
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映はユーザー明示コマンドのみ
#   - WBS 構造の解釈は最小限（focus_point のみ）
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_meta(wbs: Dict[str, Any]) -> None:
    meta = wbs.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        wbs["meta"] = meta
    if "version" not in meta:
        meta["version"] = "minimal-1.1"
    if "created_at" not in meta:
        meta["created_at"] = _now_iso()
    meta["updated_at"] = _now_iso()


def _normalize_work_item(item: Any) -> Dict[str, Any]:
    """
    既存互換:
      - v1.0 の item は {"rationale": "...", "created_at": "..."} のみ
      - v1.1 では status を持つ（active/done/dropped）
    """
    if isinstance(item, dict):
        out = dict(item)
    else:
        out = {"rationale": str(item)}

    if not out.get("created_at"):
        out["created_at"] = _now_iso()
    if not out.get("status"):
        out["status"] = "active"  # default
    return out


def _recompute_focus_point(wbs: Dict[str, Any]) -> Optional[int]:
    """
    focus_point は「最後の active work_item」を指す。
    なければ None。
    """
    items = wbs.get("work_items") or []
    if not isinstance(items, list) or not items:
        return None

    # 後ろから探す
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if isinstance(it, dict) and it.get("status") == "active":
            return i
    return None


def _set_task_status_from_items(wbs: Dict[str, Any]) -> None:
    """
    WBS全体の status は最小の運用整合だけ取る。
      - active: active item がある
      - empty: item がない
      - paused/completed: これは task コマンドでのみ変える（ここでは触らない）
    """
    status = wbs.get("status")
    if status in ("paused", "completed"):
        return

    items = wbs.get("work_items") or []
    if not items:
        wbs["status"] = "empty"
        return

    fp = wbs.get("focus_point")
    if fp is None:
        # active がないなら empty 扱いにしない（過去があるだけの状態）
        wbs["status"] = "active"  # 運用上はスレが続いている前提
        return

    wbs["status"] = "active"


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
        "task": thread_name,
        "status": "empty",            # empty | active | paused | completed
        "work_items": [],             # list[work_item]
        "focus_point": None,          # index of work_items
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
    item = {
        "rationale": (candidate.get("rationale") or "").strip(),
        "created_at": _now_iso(),
        "status": "active",
    }

    items = wbs.get("work_items")
    if not isinstance(items, list):
        items = []
        wbs["work_items"] = items

    items.append(item)

    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"
    _ensure_meta(wbs)
    return wbs


def reject_work_item(wbs: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    !wn による破棄。
    WBS には一切影響しない。
    """
    _ensure_meta(wbs)
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
        "rationale": (new_rationale or "").strip(),
        "created_at": _now_iso(),
        "status": "active",
    }

    items = wbs.get("work_items")
    if not isinstance(items, list):
        items = []
        wbs["work_items"] = items

    items.append(edited)
    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"
    _ensure_meta(wbs)
    return wbs


# ------------------------------------------------------------
# work_item lifecycle (done / dropped)
# ------------------------------------------------------------

def mark_focus_done(wbs: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    !wd: focus_point の work_item を done にする。
    戻り値: (updated_wbs, finalized_item or None)
      - finalized_item は NotionTaskSummary 移送用に上位が使える（Builder は移送しない）
    """
    items = wbs.get("work_items") or []
    if not isinstance(items, list) or not items:
        _ensure_meta(wbs)
        return wbs, None

    fp = wbs.get("focus_point")
    if fp is None or not isinstance(fp, int) or fp < 0 or fp >= len(items):
        _ensure_meta(wbs)
        return wbs, None

    it = _normalize_work_item(items[fp])
    it["status"] = "done"
    it["finalized_at"] = _now_iso()
    items[fp] = it

    # 次の focus を再計算
    wbs["focus_point"] = _recompute_focus_point(wbs)
    _set_task_status_from_items(wbs)
    _ensure_meta(wbs)

    return wbs, dict(it)


def mark_focus_dropped(wbs: Dict[str, Any], reason: str | None = None) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    !wx: focus_point の work_item を dropped にする。
    reason は任意（最小）。
    """
    items = wbs.get("work_items") or []
    if not isinstance(items, list) or not items:
        _ensure_meta(wbs)
        return wbs, None

    fp = wbs.get("focus_point")
    if fp is None or not isinstance(fp, int) or fp < 0 or fp >= len(items):
        _ensure_meta(wbs)
        return wbs, None

    it = _normalize_work_item(items[fp])
    it["status"] = "dropped"
    it["finalized_at"] = _now_iso()
    if reason and str(reason).strip():
        it["drop_reason"] = str(reason).strip()
    items[fp] = it

    wbs["focus_point"] = _recompute_focus_point(wbs)
    _set_task_status_from_items(wbs)
    _ensure_meta(wbs)

    return wbs, dict(it)


# ------------------------------------------------------------
# Task State Handling (thread lifecycle)
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tp
    NOTE: !tp は task status を paused にするだけで、work_item を done にしない。
    """
    wbs["status"] = "paused"
    _ensure_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tc
    """
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    _ensure_meta(wbs)
    return wbs