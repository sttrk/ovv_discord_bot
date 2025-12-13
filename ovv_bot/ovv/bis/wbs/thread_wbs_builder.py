# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.4
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
import json
import re
import uuid


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tid(trace_id: Optional[str]) -> str:
    return trace_id if isinstance(trace_id, str) and trace_id else "UNKNOWN"


def _safe_items(wbs: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = wbs.get("work_items")
    return items if isinstance(items, list) else []


def _touch_meta(wbs: Dict[str, Any]) -> None:
    meta = wbs.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        wbs["meta"] = meta
    meta["updated_at"] = _now_iso()


# ------------------------------------------------------------
# work_item factory (NO INFERENCE)
# ------------------------------------------------------------

def _new_work_item(*, rationale: str, source: str) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "status": "candidate",
        "source": source,
        "rationale": rationale,
        "context": {
            "what": None,
            "how": None,
            "result": None,
        },
        "created_at": now,
        "updated_at": now,
        "finalized_at": None,
    }


# ------------------------------------------------------------
# CDC (Initial Naming)
# ------------------------------------------------------------

_CDC_PREFIX_LABELS = ("相談", "質問", "メモ", "検討", "作業", "task", "todo")
_CDC_ABSTRACT_ENDINGS = ("検討", "対応", "修正", "整理", "確認", "調査", "作成", "更新", "実装")
_CDC_DEICTIC = ("これ", "それ", "あれ")

_CDC_BRACKET_PREFIX = re.compile(r"^\s*(【[^】]*】|\[[^\]]*]|\([^)]*\))\s*")
_CDC_SYMBOL_STRIP = re.compile(r"^[=\-‐—_>＜<]+|[=\-‐—_>＞>]+$")


def _cdc_normalize(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""

    for _ in range(3):
        m = _CDC_BRACKET_PREFIX.match(s)
        if not m:
            break
        s = s[m.end():].strip()

    s = _CDC_SYMBOL_STRIP.sub("", s).strip()

    lowered = s.lower()
    for lab in _CDC_PREFIX_LABELS:
        if s.startswith(lab):
            s = s[len(lab):].lstrip(" :：-—_")
            lowered = s.lower()

    s = s.rstrip("?!？！。．. ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _cdc_is_unconfirmed(title: str) -> bool:
    if not title:
        return True
    if title in _CDC_DEICTIC or len(title) <= 3:
        return True
    if title.endswith("か"):
        return True
    if title in _CDC_ABSTRACT_ENDINGS:
        return True
    return False


def cdc_title_from_thread_name(raw_thread_name: str) -> Tuple[str, bool, str]:
    raw = (raw_thread_name or "").strip()
    norm = _cdc_normalize(raw)

    if not norm:
        return "(untitled task)", True, "empty"

    if norm.isdigit() and len(norm) >= 12:
        return "(untitled task)", True, "looks_like_id"

    if _cdc_is_unconfirmed(norm):
        return f"{norm}（内容未確定）", True, "unconfirmed"

    return norm, False, "confirmed"


# ------------------------------------------------------------
# WBS Factory
# ------------------------------------------------------------

def create_empty_wbs(raw_thread_name: str, *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    title, unconfirmed, note = cdc_title_from_thread_name(raw_thread_name)

    return {
        "task": title,
        "status": "empty",          # empty | active | paused | completed
        "work_items": [],
        "focus_point": None,        # work_item.id
        "meta": {
            "version": "minimal-1.4",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "cdc": {
                "raw_thread_name": raw_thread_name or "",
                "title": title,
                "is_unconfirmed": unconfirmed,
                "note": note,
            },
        },
    }


# ------------------------------------------------------------
# CDC Candidate Handling (explicit only)
# ------------------------------------------------------------

def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    rationale = str(candidate.get("rationale", "") or "").strip()
    if not rationale:
        return wbs

    item = _new_work_item(rationale=rationale, source="user_accept")
    item["status"] = "active"

    items = _safe_items(wbs)
    items.append(item)

    wbs["work_items"] = items
    wbs["focus_point"] = item["id"]
    wbs["status"] = "active"
    _touch_meta(wbs)
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    rationale = str(new_rationale or "").strip()
    if not rationale:
        return wbs

    item = _new_work_item(rationale=rationale, source="user_edit")
    item["status"] = "active"

    items = _safe_items(wbs)
    items.append(item)

    wbs["work_items"] = items
    wbs["focus_point"] = item["id"]
    wbs["status"] = "active"
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# Task State
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    wbs["status"] = "paused"
    _touch_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    wbs["status"] = "completed"
    wbs["focus_point"] = None
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# FINALIZE (explicit)
# ------------------------------------------------------------

def _find_focus_item(wbs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fid = wbs.get("focus_point")
    if not fid:
        return None
    for it in _safe_items(wbs):
        if it.get("id") == fid:
            return it
    return None


def mark_focus_done(
    wbs: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    item = _find_focus_item(wbs)
    if not item:
        return wbs, None

    item["status"] = "done"
    item["finalized_at"] = _now_iso()
    item["updated_at"] = item["finalized_at"]

    finalized = {
        "rationale": item.get("rationale", ""),
        "status": "done",
        "finalized_at": item["finalized_at"],
        "id": item["id"],
    }

    wbs["focus_point"] = None
    _touch_meta(wbs)
    return wbs, finalized


def mark_focus_dropped(
    wbs: Dict[str, Any],
    reason: Optional[str] = None,
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    item = _find_focus_item(wbs)
    if not item:
        return wbs, None

    if reason:
        item["rationale"] = f"{item.get('rationale','')} (dropped: {reason})"

    item["status"] = "dropped"
    item["finalized_at"] = _now_iso()
    item["updated_at"] = item["finalized_at"]

    finalized = {
        "rationale": item.get("rationale", ""),
        "status": "dropped",
        "finalized_at": item["finalized_at"],
        "id": item["id"],
    }

    wbs["focus_point"] = None
    _touch_meta(wbs)
    return wbs, finalized