# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.4 (Volatile integrated)
#
# CHANGE:
#   - wbs["volatile"] を正式導入（揮発層: drafts / intent / open_questions）
#   - stable(work_items等)には触れず、揮発層の操作関数のみ追加
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

CP_CORE_RECEIVE_PACKET = "CORE_RECEIVE_PACKET"      # CORE-01
CP_CORE_PARSE_INTENT = "CORE_PARSE_INTENT"          # CORE-02
CP_CORE_EXECUTE = "CORE_EXECUTE"                    # CORE-03
CP_CORE_RETURN_RESULT = "CORE_RETURN_RESULT"        # CORE-04
CP_CORE_EXCEPTION = "CORE_EXCEPTION"                # CORE-FAIL


# ------------------------------------------------------------
# helpers (time / safe access)
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_items(wbs: Dict[str, Any]) -> List[Any]:
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
        "timestamp": _now_iso(),
    }
    if error is not None:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False))


def _log_debug(*, trace_id: str, checkpoint: str, summary: str) -> None:
    _log_event(trace_id=trace_id, checkpoint=checkpoint, level="DEBUG", summary=summary)


def _tid(trace_id: Optional[str]) -> str:
    return trace_id if isinstance(trace_id, str) and trace_id else "UNKNOWN"


# ------------------------------------------------------------
# Volatile layer (NEW) - guaranteed but optional
# ------------------------------------------------------------

_VOL_SCHEMA = "volatile-0.1"

def _ensure_volatile(wbs: Dict[str, Any]) -> Dict[str, Any]:
    """
    volatile 層は「存在してもしなくても良い」が、
    builder が触る場合は必ず正規化してから扱う。
    """
    vol = wbs.get("volatile")
    if not isinstance(vol, dict):
        vol = {}
        wbs["volatile"] = vol

    if vol.get("schema") != _VOL_SCHEMA:
        vol["schema"] = _VOL_SCHEMA

    intent = vol.get("intent")
    if not isinstance(intent, dict):
        intent = {"state": "unconfirmed", "summary": "", "updated_at": _now_iso()}
        vol["intent"] = intent
    else:
        intent.setdefault("state", "unconfirmed")
        intent.setdefault("summary", "")
        intent.setdefault("updated_at", _now_iso())

    drafts = vol.get("drafts")
    if not isinstance(drafts, list):
        vol["drafts"] = []

    oq = vol.get("open_questions")
    if not isinstance(oq, list):
        vol["open_questions"] = []

    return wbs


def volatile_append_draft(
    wbs: Dict[str, Any],
    text: str,
    *,
    kind: str = "work_item_candidate",
    confidence: str = "low",
    source: str = "inference",
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    揮発 draft を追加する。
    HARD: stable には触れない。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="volatile append_draft")

    wbs = _ensure_volatile(wbs)
    vol = wbs["volatile"]

    draft = {
        "draft_id": str(uuid.uuid4()),
        "kind": str(kind),
        "text": str(text or "").strip(),
        "confidence": str(confidence),
        "status": "open",
        "source": str(source),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "promoted_to_index": None,
    }

    if draft["text"]:
        vol["drafts"].append(draft)

    _touch_meta(wbs)
    return wbs


def volatile_discard_draft(
    wbs: Dict[str, Any],
    draft_id: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    draft を discarded にする（削除ではなく履歴保持）。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="volatile discard_draft")

    wbs = _ensure_volatile(wbs)
    vol = wbs["volatile"]
    drafts = vol.get("drafts", [])

    for d in drafts:
        if isinstance(d, dict) and d.get("draft_id") == draft_id:
            d["status"] = "discarded"
            d["updated_at"] = _now_iso()

    _touch_meta(wbs)
    return wbs


def volatile_set_intent(
    wbs: Dict[str, Any],
    *,
    state: str,
    summary: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    intent は「スレの現在の方向性」1つだけを保持（揮発）。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="volatile set_intent")

    wbs = _ensure_volatile(wbs)
    vol = wbs["volatile"]
    intent = vol["intent"]
    intent["state"] = str(state)
    intent["summary"] = str(summary or "").strip()
    intent["updated_at"] = _now_iso()

    _touch_meta(wbs)
    return wbs


def volatile_append_question(
    wbs: Dict[str, Any],
    text: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    open_questions に追加。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="volatile append_question")

    wbs = _ensure_volatile(wbs)
    vol = wbs["volatile"]

    q = {
        "q_id": str(uuid.uuid4()),
        "text": str(text or "").strip(),
        "status": "open",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if q["text"]:
        vol["open_questions"].append(q)

    _touch_meta(wbs)
    return wbs


def volatile_mark_question_answered(
    wbs: Dict[str, Any],
    q_id: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="volatile mark_question_answered")

    wbs = _ensure_volatile(wbs)
    vol = wbs["volatile"]
    qs = vol.get("open_questions", [])

    for q in qs:
        if isinstance(q, dict) and q.get("q_id") == q_id:
            q["status"] = "answered"
            q["updated_at"] = _now_iso()

    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# CDC (Initial Naming) - Control Process (No guessing)
# ------------------------------------------------------------

_CDC_PREFIX_LABELS = ("相談", "質問", "メモ", "検討", "作業", "task", "todo")
_CDC_ABSTRACT_ENDINGS = ("検討", "対応", "修正", "整理", "確認", "調査", "作成", "更新", "実装")
_CDC_DEICTIC = ("これ", "それ", "あれ")

_CDC_BRACKET_PREFIX = re.compile(r"^\s*(【[^】]*】|\[[^\]]*]|\([^)]*\))\s*")
_CDC_SYMBOL_STRIP = re.compile(r"^[=\-‐—_>＜＜＜＜＜＜<]+|[=\-‐—_>＞＞＞＞＞＞>]+$")

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
        if lab.isascii():
            if lowered.startswith(lab):
                s = s[len(lab):].lstrip(" :：-—_")
                lowered = s.lower()
        else:
            if s.startswith(lab):
                s = s[len(lab):].lstrip(" :：-—_")
                lowered = s.lower()

    s = s.rstrip("?!？！。．. ").strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cdc_is_unconfirmed(title: str) -> bool:
    if not title:
        return True
    if "どうする" in title or "どうやる" in title or title.endswith("か"):
        return True
    if title in _CDC_DEICTIC:
        return True
    if len(title) <= 3:
        return True
    if title in _CDC_ABSTRACT_ENDINGS:
        return True
    return False


def cdc_title_from_thread_name(raw_thread_name: str) -> Tuple[str, bool, str]:
    raw = (raw_thread_name or "").strip()
    norm = _cdc_normalize(raw)

    if not norm:
        return "(untitled task)", True, "empty_thread_name"

    if norm.isdigit() and len(norm) >= 12:
        return "(untitled task)", True, "thread_name_looks_like_id"

    unconfirmed = _cdc_is_unconfirmed(norm)
    if unconfirmed:
        return f"{norm}（内容未確定）", True, "unconfirmed"

    return norm, False, "confirmed"


def build_initial_work_item_candidate(
    raw_thread_name: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs build_initial_work_item_candidate")

    title, unconfirmed, note = cdc_title_from_thread_name(raw_thread_name)

    return {
        "title": title,
        "rationale": title,
        "is_unconfirmed": bool(unconfirmed),
        "source": "thread_name",
        "note": note,
        "created_at": _now_iso(),
    }


# ------------------------------------------------------------
# WBS Factory
# ------------------------------------------------------------

def create_empty_wbs(raw_thread_name: str, *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs create_empty_wbs (with CDC title)")

    title, unconfirmed, note = cdc_title_from_thread_name(raw_thread_name)

    wbs = {
        "task": title,
        "status": "empty",            # empty | active | paused | completed
        "work_items": [],
        "focus_point": None,
        "meta": {
            "version": "minimal-1.4",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "cdc": {
                "raw_thread_name": str(raw_thread_name or ""),
                "title": title,
                "is_unconfirmed": bool(unconfirmed),
                "note": note,
            },
        },
        # NEW: volatile layer (safe default)
        "volatile": {
            "schema": _VOL_SCHEMA,
            "intent": {"state": "unconfirmed", "summary": "", "updated_at": _now_iso()},
            "drafts": [],
            "open_questions": [],
        },
    }
    return wbs


# ------------------------------------------------------------
# CDC Candidate Handling (explicit accept only)
# ------------------------------------------------------------

def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs accept_work_item")

    rationale = ""
    if isinstance(candidate, dict):
        rationale = str(candidate.get("rationale", "") or "").strip()

    item = {"rationale": rationale, "created_at": _now_iso()}

    items = _safe_items(wbs)
    items.append(item)
    wbs["work_items"] = items

    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"

    # if candidate was from drafts, you can mark promoted later (Core responsibility)
    _ensure_volatile(wbs)
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs accepted item; focus_point={wbs.get('focus_point')}")
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs edit_and_accept_work_item")

    rationale = str(new_rationale or "").strip()
    item = {"rationale": rationale, "created_at": _now_iso()}

    items = _safe_items(wbs)
    items.append(item)
    wbs["work_items"] = items

    wbs["focus_point"] = len(items) - 1
    wbs["status"] = "active"

    _ensure_volatile(wbs)
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs edited+accepted; focus_point={wbs.get('focus_point')}")
    return wbs


# ------------------------------------------------------------
# Task State Handling (explicit)
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs on_task_pause")

    wbs["status"] = "paused"
    _ensure_volatile(wbs)
    _touch_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs on_task_complete")

    wbs["status"] = "completed"
    wbs["focus_point"] = None
    _ensure_volatile(wbs)
    _touch_meta(wbs)
    return wbs


# ------------------------------------------------------------
# FINALIZE: work_item done / dropped (explicit)
# ------------------------------------------------------------

def mark_focus_done(
    wbs: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs mark_focus_done")

    idx = _safe_focus_index(wbs)
    if idx is None:
        _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary="wbs done skipped (no focus_point)")
        return wbs, None

    items = _safe_items(wbs)
    if idx < 0 or idx >= len(items):
        _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary="wbs done skipped (focus out of range)")
        return wbs, None

    item = items[idx] if isinstance(items[idx], dict) else {"rationale": str(items[idx])}
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

    wbs["focus_point"] = None
    _ensure_volatile(wbs)
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs done finalized index={idx}")
    return wbs, finalized


def mark_focus_dropped(
    wbs: Dict[str, Any],
    reason: Optional[str] = None,
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs mark_focus_dropped")

    idx = _safe_focus_index(wbs)
    if idx is None:
        _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary="wbs drop skipped (no focus_point)")
        return wbs, None

    items = _safe_items(wbs)
    if idx < 0 or idx >= len(items):
        _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary="wbs drop skipped (focus out of range)")
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
    _ensure_volatile(wbs)
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs dropped finalized index={idx}")
    return wbs, finalized