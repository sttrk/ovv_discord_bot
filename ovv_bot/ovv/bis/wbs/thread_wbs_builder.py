# ovv/bis/wbs/thread_wbs_builder.py
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
#   [GUARD]     勝手な LLM 改変を防止（明示コマンドのみが状態を確定し得る）
#
# CONSTRAINTS:
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映はユーザー明示コマンドのみ（= accept/edit のみ）
#   - finalize は focus_point のみを対象とし、勝手に次を選定しない
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_items(wbs: Dict[str, Any]) -> list:
    items = wbs.get("work_items")
    return items if isinstance(items, list) else []


def _safe_focus_index(wbs: Dict[str, Any]) -> Optional[int]:
    idx = wbs.get("focus_point")
    if isinstance(idx, bool):  # bool is subclass of int
        return None
    if isinstance(idx, int):
        return idx
    return None


def _touch_meta(wbs: Dict[str, Any]) -> None:
    meta = wbs.get("meta")
    if not isinstance(meta, dict):
        wbs["meta"] = {}
        meta = wbs["meta"]
    meta["updated_at"] = _now_iso()


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
        "work_items": [],
        "focus_point": None,
        "meta": {
            "version": "minimal-1.1",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    }


# ------------------------------------------------------------
# CDC Candidate Handling (explicit accept only)
# ------------------------------------------------------------

def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    !wy による明示承認。
    """
    rationale = ""
    if isinstance(candidate, dict):
        rationale = str(candidate.get("rationale", "") or "").strip()

    item = {
        "rationale": rationale,
        "created_at": _now_iso(),
    }

    items = _safe_items(wbs)
    items.append(item)
    wbs["work_items"] = items

    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"
    _touch_meta(wbs)
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
) -> Dict[str, Any]:
    """
    !we による編集後採用。
    """
    rationale = str(new_rationale or "").strip()

    item = {
        "rationale": rationale,
        "created_at": _now_iso(),
    }

    items = _safe_items(wbs)
    items.append(item)
    wbs["work_items"] = items

    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# Task State Handling (explicit)
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tp
    NOTE:
      - paused は「作業途中の中断」。完了(done)とは無関係。
    """
    wbs["status"] = "paused"
    _touch_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    !tc
    NOTE:
      - completed は「タスク(スレッド)の終了」。
      - work_item の done/dropped とは別概念。
    """
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# FINALIZE: work_item done / dropped (explicit)
# ------------------------------------------------------------

def mark_focus_done(
    wbs: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    !wd:
      - focus_point の work_item を done として確定する。
      - 次の focus 自動選定はしない（ガード）。
      - finalized_item を返す（Stabilizer が NotionTaskSummary へ移送するため）。
    """
    idx = _safe_focus_index(wbs)
    if idx is None:
        return wbs, None

    items = _safe_items(wbs)
    if idx < 0 or idx >= len(items):
        return wbs, None

    item = items[idx] if isinstance(items[idx], dict) else {"rationale": str(items[idx])}

    # 状態を item に刻む（最小解釈）
    item["status"] = "done"
    item["finalized_at"] = _now_iso()
    items[idx] = item
    wbs["work_items"] = items

    finalized = {
        "rationale": str(item.get("rationale", "") or ""),
        "status": "done",
        "finalized_at": str(item.get("finalized_at") or _now_iso()),
        "index": idx,
    }

    # focus を解除（次の work_item は自動選定しない）
    wbs["focus_point"] = None
    _touch_meta(wbs)

    # active/paused/completed は勝手に変更しない（taskの状態遷移は別コマンド）
    return wbs, finalized


def mark_focus_dropped(
    wbs: Dict[str, Any],
    reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    !wx:
      - focus_point の work_item を dropped として確定する。
      - reason は UI 表示の補助（Interface_Box 側が packet.content 等から渡す想定）。
      - 次の focus 自動選定はしない（ガード）。
      - finalized_item を返す。
    """
    idx = _safe_focus_index(wbs)
    if idx is None:
        return wbs, None

    items = _safe_items(wbs)
    if idx < 0 or idx >= len(items):
        return wbs, None

    item = items[idx] if isinstance(items[idx], dict) else {"rationale": str(items[idx])}

    base_rationale = str(item.get("rationale", "") or "")
    if reason:
        base_rationale = f"{base_rationale} (dropped: {str(reason).strip()})"

    item["rationale"] = base_rationale
    item["status"] = "dropped"
    item["finalized_at"] = _now_iso()
    items[idx] = item
    wbs["work_items"] = items

    finalized = {
        "rationale": str(item.get("rationale", "") or ""),
        "status": "dropped",
        "finalized_at": str(item.get("finalized_at") or _now_iso()),
        "index": idx,
    }

    wbs["focus_point"] = None
    _touch_meta(wbs)

    return wbs, finalized