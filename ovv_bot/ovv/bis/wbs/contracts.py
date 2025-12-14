# ovv/inference/contracts.py
# ============================================================
# MODULE CONTRACT: Inference / Contracts v0.1 (Soft-Lock)
#
# ROLE:
#   - Inference Box の入出力を「固定のデータ契約」として定義する。
#   - ここは実装詳細を持たない（型と契約のみ）。
#
# HARD:
#   - Inference は stable(WBS core fields) を変更しない。
#   - Inference が返せる更新は volatile のみ（draft_ops）。
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


ISO8601 = str

# -----------------------------
# Volatile Schema (read-only snapshot)
# -----------------------------

VolatileIntentState = Literal["unconfirmed", "candidate", "confirmed"]

DraftKind = Literal[
    "work_item_candidate",
    "note",
    "decision_candidate",
    "question",
]

DraftConfidence = Literal["low", "mid", "high"]
DraftStatus = Literal["open", "promoted", "discarded"]

QuestionStatus = Literal["open", "answered", "expired"]


class VolatileIntent(TypedDict, total=False):
    state: VolatileIntentState
    summary: str
    updated_at: ISO8601


class VolatileDraft(TypedDict, total=False):
    draft_id: str
    kind: DraftKind
    text: str
    confidence: DraftConfidence
    status: DraftStatus
    source: str  # "user_message" | "inference"
    created_at: ISO8601
    updated_at: ISO8601
    promoted_to_index: Optional[int]


class VolatileQuestion(TypedDict, total=False):
    q_id: str
    text: str
    status: QuestionStatus
    created_at: ISO8601
    updated_at: ISO8601


class VolatileLayer(TypedDict, total=False):
    schema: str  # "volatile-0.1"
    intent: VolatileIntent
    drafts: List[VolatileDraft]
    open_questions: List[VolatileQuestion]


# -----------------------------
# Minimal WBS snapshot (read-only)
# -----------------------------

class WbsSnapshot(TypedDict, total=False):
    task: str
    status: str
    work_items: List[Any]
    focus_point: Optional[int]
    meta: Dict[str, Any]
    volatile: VolatileLayer


# -----------------------------
# Inference Input
# -----------------------------

@dataclass(frozen=True)
class InferenceInput:
    """
    Inference Box input contract.

    - packet: user message / normalized content (future: full conversation window)
    - wbs: snapshot (read-only)
    - context_key/task_id: routing key
    """
    context_key: str
    task_id: str
    user_id: str
    message_text: str

    # read-only snapshots
    wbs: WbsSnapshot = field(default_factory=dict)
    volatile: VolatileLayer = field(default_factory=dict)

    # telemetry
    trace_id: str = "UNKNOWN"


# -----------------------------
# Inference Output (advice + volatile ops only)
# -----------------------------

AdviceType = Literal["summary", "options", "question"]

@dataclass(frozen=True)
class Advice:
    kind: AdviceType
    text: str


DraftOpType = Literal[
    "append_draft",
    "discard_draft",
    "set_intent",
    "append_question",
    "mark_question_answered",
]

class DraftOp(TypedDict, total=False):
    op: DraftOpType

    # common routing
    task_id: str

    # append_draft
    draft: VolatileDraft

    # discard_draft
    draft_id: str

    # set_intent
    intent: VolatileIntent

    # append_question / mark_question_answered
    question: VolatileQuestion
    q_id: str


@dataclass(frozen=True)
class InferenceOutput:
    """
    Output is purely:
      - advice for user (Discord response generator will format)
      - draft_ops to update volatile layer (optional)
    """
    advice: List[Advice] = field(default_factory=list)
    draft_ops: List[DraftOp] = field(default_factory=list)

    # debug
    trace_id: str = "UNKNOWN"


# -----------------------------
# Safety Gate (recommended utility)
# -----------------------------

def assert_ops_are_volatile_only(ops: List[DraftOp]) -> None:
    """
    Runtime assertion helper:
    - ensures ops are only volatile mutations, not stable WBS mutations.
    """
    allowed = {
        "append_draft",
        "discard_draft",
        "set_intent",
        "append_question",
        "mark_question_answered",
    }
    for op in ops:
        t = op.get("op")
        if t not in allowed:
            raise ValueError(f"Invalid draft op: {t}")