# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.5 (Volatile + Promotion integrated)
#
# CHANGE:
#   - volatile draft → stable work_item 昇格 API を正式導入
#   - 昇格理由・操作者・時刻を volatile に保持
#   - 推論・自動昇格は行わない
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
import json
import re
import uuid

# ------------------------------------------------------------
# Debugging Subsystem v1.0 (Fixed checkpoints - DO NOT EXTEND HERE)
# ------------------------------------------------------------

LAYER_CORE = "CORE"

CP_CORE_RECEIVE_PACKET = "CORE_RECEIVE_PACKET"
CP_CORE_PARSE_INTENT = "CORE_PARSE_INTENT"
CP_CORE_EXECUTE = "CORE_EXECUTE"
CP_CORE_RETURN_RESULT = "CORE_RETURN_RESULT"
CP_CORE_EXCEPTION = "CORE_EXCEPTION"

# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_items(wbs: Dict[str, Any]) -> List[Any]:
    items = wbs.get("work_items")
    return items if isinstance(items, list) else []


def _safe_focus_index(wbs: Dict[str, Any]) -> Optional[int]:
    idx = wbs.get("focus_point")
    if isinstance(idx, bool):
        return None
    return idx if isinstance(idx, int) else None


def _touch_meta(wbs: Dict[str, Any]) -> None:
    meta = wbs.setdefault("meta", {})
    meta["updated_at"] = _now_iso()


def _tid(trace_id: Optional[str]) -> str:
    return trace_id if isinstance(trace_id, str) and trace_id else "UNKNOWN"


# ------------------------------------------------------------
# Volatile layer
# ------------------------------------------------------------

_VOL_SCHEMA = "volatile-0.2"

def _ensure_volatile(wbs: Dict[str, Any]) -> Dict[str, Any]:
    vol = wbs.setdefault("volatile", {})
    vol.setdefault("schema", _VOL_SCHEMA)

    vol.setdefault(
        "intent",
        {"state": "unconfirmed", "summary": "", "updated_at": _now_iso()},
    )
    vol.setdefault("drafts", [])
    vol.setdefault("open_questions", [])

    return wbs


# ------------------------------------------------------------
# Volatile draft APIs（既存 + 微拡張）
# ------------------------------------------------------------

def volatile_append_draft(
    wbs: Dict[str, Any],
    text: str,
    *,
    kind: str = "work_item_candidate",
    confidence: str = "low",
    source: str = "inference",
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    wbs = _ensure_volatile(wbs)

    draft = {
        "draft_id": str(uuid.uuid4()),
        "kind": kind,
        "text": str(text or "").strip(),
        "confidence": confidence,
        "status": "open",
        "source": source,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "promotion": None,   # ← 統合ポイント
    }

    if draft["text"]:
        wbs["volatile"]["drafts"].append(draft)

    _touch_meta(wbs)
    return wbs


def volatile_discard_draft(
    wbs: Dict[str, Any],
    draft_id: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    wbs = _ensure_volatile(wbs)

    for d in wbs["volatile"]["drafts"]:
        if d.get("draft_id") == draft_id:
            d["status"] = "discarded"
            d["updated_at"] = _now_iso()

    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# NEW: Promotion API（核心）
# ------------------------------------------------------------

def promote_draft_to_work_item(
    wbs: Dict[str, Any],
    *,
    draft_id: str,
    rationale: Optional[str] = None,
    promoted_by: str = "user",
    reason: str = "",
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    明示操作による draft → work_item 昇格。

    HARD:
      - 自動昇格禁止
      - 推論禁止
      - 昇格理由を必ず残す
    """
    wbs = _ensure_volatile(wbs)
    drafts = wbs["volatile"]["drafts"]

    target = None
    for d in drafts:
        if d.get("draft_id") == draft_id and d.get("status") == "open":
            target = d
            break

    if not target:
        return wbs

    item = {
        "rationale": rationale or target.get("text", ""),
        "created_at": _now_iso(),
    }

    items = _safe_items(wbs)
    items.append(item)
    wbs["work_items"] = items
    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"

    target["status"] = "promoted"
    target["promotion"] = {
        "to_index": wbs["focus_point"],
        "by": promoted_by,
        "reason": reason,
        "at": _now_iso(),
    }
    target["updated_at"] = _now_iso()

    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# Task state APIs（既存）
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    wbs["status"] = "paused"
    _ensure_volatile(wbs)
    _touch_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    _ensure_volatile(wbs)
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# FINALIZE APIs（既存）
# ------------------------------------------------------------

def mark_focus_done(
    wbs: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    idx = _safe_focus_index(wbs)
    if idx is None:
        return wbs, None

    items = _safe_items(wbs)
    if idx < 0 or idx >= len(items):
        return wbs, None

    item = items[idx]
    item["status"] = "done"
    item["finalized_at"] = _now_iso()

    finalized = {
        "index": idx,
        "rationale": item.get("rationale", ""),
        "status": "done",
        "finalized_at": item["finalized_at"],
    }

    wbs["focus_point"] = None
    _ensure_volatile(wbs)
    _touch_meta(wbs)
    return wbs, finalized