# ovv/bis/wbs/thread_wbs_builder.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Builder v1.3
#   (Minimal + Finalize + Debugging Subsystem v1.0 observation hooks)
#
# ROLE:
#   - ThreadWBS の生成・更新を行う唯一のロジック層
#
# RESPONSIBILITY TAGS:
#   [BUILD]     WBS 初期生成
#   [UPDATE]    work_item / focus_point / status 更新
#   [FINALIZE]  work_item の done / dropped 確定
#   [GUARD]     勝手な LLM 改変を防止（明示コマンドのみが状態を確定し得る）
#   [DEBUG]     Debugging Subsystem v1.0 の観測（挙動は変えない）
#   [CDC]       初期命名 CDC（制御工程・推測禁止）
#
# CONSTRAINTS:
#   - 永続化は行わない（PG は別責務）
#   - 推論を行わない
#   - CDC 結果の反映は「初期命名(=task/title確定)」と「ユーザー明示 accept/edit」のみ
#   - finalize は focus_point のみを対象とし、勝手に次を選定しない
#   - context_splitter は初期段階では使用しない
#   - thread_id を UI 命名に使用しない（内部キー専用）
#
# DEBUGGING SUBSYSTEM v1.0 (OBSERVATION ONLY):
#   - trace_id は Boundary が生成したものを上位から受領するのが理想。
#   - 本モジュールは互換性維持のため trace_id を optional で受ける。
#   - チェックポイント名は既存の固定セット（CORE_*）を流用し、新規追加しない。
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
import json
import re


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


def _log_error(
    *,
    trace_id: str,
    checkpoint: str,
    summary: str,
    code: str,
    exc: Exception,
    at: str,
    retryable: bool = False,
) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        level="ERROR",
        summary=summary,
        error={
            "code": code,
            "type": type(exc).__name__,
            "message": str(exc),
            "at": at,
            "retryable": retryable,
        },
    )


def _tid(trace_id: Optional[str]) -> str:
    return trace_id if isinstance(trace_id, str) and trace_id else "UNKNOWN"


# ------------------------------------------------------------
# CDC (Initial Naming) - Control Process (No guessing)
# ------------------------------------------------------------

_CDC_PREFIX_LABELS = (
    "相談", "質問", "メモ", "検討", "作業", "task", "todo"
)

_CDC_ABSTRACT_ENDINGS = (
    "検討", "対応", "修正", "整理", "確認", "調査", "作成", "更新", "実装"
)

_CDC_DEICTIC = ("これ", "それ", "あれ")

_CDC_BRACKET_PREFIX = re.compile(r"^\s*(【[^】]*】|\[[^\]]*]|\([^)]*\))\s*")
_CDC_SYMBOL_STRIP = re.compile(r"^[=\-‐—_>＜＜＜＜＜＜<]+|[=\-‐—_>＞＞＞＞＞＞>]+$")

def _cdc_normalize(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""

    # remove leading bracket prefix repeatedly (e.g. "【相談】(メモ) ...")
    for _ in range(3):
        m = _CDC_BRACKET_PREFIX.match(s)
        if not m:
            break
        s = s[m.end():].strip()

    # strip noisy edge symbols
    s = _CDC_SYMBOL_STRIP.sub("", s).strip()

    # remove leading labels (case-insensitive for ascii)
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

    # drop trailing punctuation typical for questions
    s = s.rstrip("?!？！。．. ").strip()

    # normalize spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cdc_is_unconfirmed(title: str) -> bool:
    if not title:
        return True

    # question-like patterns (minimal)
    if "どうする" in title or "どうやる" in title or title.endswith("か"):
        return True

    # deictic-only / too short
    if title in _CDC_DEICTIC:
        return True
    if len(title) <= 3:
        return True

    # abstract-only ending with no concrete subject (heuristic, no guessing)
    # Example: "検討" "修正" etc.
    if title in _CDC_ABSTRACT_ENDINGS:
        return True

    return False


def cdc_title_from_thread_name(raw_thread_name: str) -> Tuple[str, bool, str]:
    """
    CDC (Control) for initial naming.

    Returns:
      (title, is_unconfirmed, note)

    - No guessing: if unconfirmed, title is stabilized with explicit marker.
    - thread_id must not be used as UI name: if raw appears numeric-id-like, mark unconfirmed.
    """
    raw = (raw_thread_name or "").strip()
    norm = _cdc_normalize(raw)

    if not norm:
        return "(untitled task)", True, "empty_thread_name"

    # guard: looks like a numeric id (e.g., Discord thread/channel id)
    # This is not "guessing meaning"; it's a safety constraint to prevent leaking internal key as title.
    if norm.isdigit() and len(norm) >= 12:
        return "(untitled task)", True, "thread_name_looks_like_id"

    unconfirmed = _cdc_is_unconfirmed(norm)
    if unconfirmed:
        # stabilize without guessing
        return f"{norm}（内容未確定）", True, "unconfirmed"

    return norm, False, "confirmed"


def build_initial_work_item_candidate(
    raw_thread_name: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ThreadWBS 初期生成時の CDC 候補を生成する（自動 accept はしない）。

    - ユーザーが !wy / !we で accept/edit するための候補。
    - candidate は accept_work_item / edit_and_accept_work_item に投入可能な形にする。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs build_initial_work_item_candidate")

    title, unconfirmed, note = cdc_title_from_thread_name(raw_thread_name)

    # Minimal candidate schema:
    # - rationale: accept_work_item が使用
    # - title: Notion/表示側が必要なら使用
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
    """
    !t 実行時に呼ばれる。
    CDC により task(title) を確定し、空 WBS を生成する。

    IMPORTANT:
      - thread_id を渡して命名してはならない（thread_id は別レイヤの内部キー）。
      - raw_thread_name が不適切な場合、CDC は unconfirmed title に収束させる。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs create_empty_wbs (with CDC title)")

    title, unconfirmed, note = cdc_title_from_thread_name(raw_thread_name)

    return {
        # task は Notion Task 名にも流用され得るため、人間可読 title を保持する
        "task": title,
        "status": "empty",            # empty | active | paused | completed
        "work_items": [],
        "focus_point": None,
        "meta": {
            "version": "minimal-1.3",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            # CDC audit footprint (control, not inference)
            "cdc": {
                "raw_thread_name": str(raw_thread_name or ""),
                "title": title,
                "is_unconfirmed": bool(unconfirmed),
                "note": note,
            },
        },
    }


# ------------------------------------------------------------
# CDC Candidate Handling (explicit accept only)
# ------------------------------------------------------------

def accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    !wy による明示承認。

    NOTE:
      - candidate は build_initial_work_item_candidate() など CDC 由来を想定するが、
        ユーザー明示コマンドであれば外部生成 candidate も受理する（推論はしない）。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs accept_work_item")

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

    _log_debug(
        trace_id=tid,
        checkpoint=CP_CORE_RETURN_RESULT,
        summary=f"wbs accepted item; focus_point={wbs.get('focus_point')}",
    )
    return wbs


def edit_and_accept_work_item(
    wbs: Dict[str, Any],
    candidate: Dict[str, Any],
    new_rationale: str,
    *,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    !we による編集後採用。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs edit_and_accept_work_item")

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

    _log_debug(
        trace_id=tid,
        checkpoint=CP_CORE_RETURN_RESULT,
        summary=f"wbs edited+accepted; focus_point={wbs.get('focus_point')}",
    )
    return wbs


# ------------------------------------------------------------
# Task State Handling (explicit)
# ------------------------------------------------------------

def on_task_pause(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    """
    !tp
    NOTE:
      - paused は「作業途中の中断」。完了(done)とは無関係。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs on_task_pause")

    wbs["status"] = "paused"
    _touch_meta(wbs)
    return wbs


def on_task_complete(wbs: Dict[str, Any], *, trace_id: Optional[str] = None) -> Dict[str, Any]:
    """
    !tc
    NOTE:
      - completed は「タスク(スレッド)の終了」。
      - work_item の done/dropped とは別概念。
    """
    tid = _tid(trace_id)
    _log_debug(trace_id=tid, checkpoint=CP_CORE_EXECUTE, summary="wbs on_task_complete")

    wbs["status"] = "completed"
    wbs["focus_point"] = None
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
    """
    !wd:
      - focus_point の work_item を done として確定する。
      - 次の focus 自動選定はしない（ガード）。
      - finalized_item を返す（Stabilizer が NotionTaskSummary へ移送するため）。
    """
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
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs done finalized index={idx}")
    return wbs, finalized


def mark_focus_dropped(
    wbs: Dict[str, Any],
    reason: Optional[str] = None,
    *,
    trace_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    !wx:
      - focus_point の work_item を dropped として確定する。
      - reason は UI 表示の補助（Interface_Box 側が packet.content 等から渡す想定）。
      - 次の focus 自動選定はしない（ガード）。
      - finalized_item を返す。
    """
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
    _touch_meta(wbs)

    _log_debug(trace_id=tid, checkpoint=CP_CORE_RETURN_RESULT, summary=f"wbs dropped finalized index={idx}")
    return wbs, finalized