# ovv/bis/wbs/wbs_formatter.py
# ============================================================
# MODULE CONTRACT: BIS / ThreadWBS Formatter v1.0
#
# ROLE:
#   - ThreadWBS を「人間が理解できる形」に整形する表示専用モジュール
#
# RESPONSIBILITY:
#   - Stable / Volatile の可視化
#   - 状態を変更しない
#   - 推論しない
#
# CONSTRAINTS:
#   - Builder / Core / PG / Notion に一切触れない
#   - 読み取り専用（Pure Formatter）
# ============================================================

from __future__ import annotations
from typing import Dict, Any, List


def _safe_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def format_wbs_overview(wbs: Dict[str, Any]) -> str:
    """
    ThreadWBS 全体を「現在の思考状態」として表示する。
    """

    task = str(wbs.get("task") or "")
    status = str(wbs.get("status") or "")
    focus = wbs.get("focus_point")

    lines: List[str] = []
    lines.append("=== ThreadWBS Overview ===")
    lines.append(f"task   : {task}")
    lines.append(f"status : {status}")
    lines.append(f"focus  : {focus}")
    lines.append("")

    # --------------------------------------------------------
    # Stable Layer
    # --------------------------------------------------------
    lines.append("[STABLE]")
    items = _safe_list(wbs.get("work_items"))

    if not items:
        lines.append("- (no confirmed work_items)")
    else:
        for i, it in enumerate(items):
            if isinstance(it, dict):
                r = str(it.get("rationale", "") or "")
                st = str(it.get("status", "") or "")
                label = f"- {i}: {r}"
                if st:
                    label += f" [{st}]"
                lines.append(label)
            else:
                lines.append(f"- {i}: {str(it)}")

    lines.append("")

    # --------------------------------------------------------
    # Volatile Layer
    # --------------------------------------------------------
    vol = _safe_dict(wbs.get("volatile"))
    if not vol:
        lines.append("[VOLATILE]")
        lines.append("- (volatile layer not initialized)")
        return "```\n" + "\n".join(lines) + "\n```"

    lines.append("[VOLATILE]")

    # intent
    intent = _safe_dict(vol.get("intent"))
    intent_state = intent.get("state", "unknown")
    intent_summary = intent.get("summary", "")
    lines.append(f"* intent: {intent_state}")
    if intent_summary:
        lines.append(f"  - {intent_summary}")

    # drafts
    drafts = _safe_list(vol.get("drafts"))
    lines.append("* drafts:")
    if not drafts:
        lines.append("  - (none)")
    else:
        for d in drafts:
            if not isinstance(d, dict):
                continue
            did = d.get("draft_id", "?")
            text = d.get("text", "")
            status = d.get("status", "")
            conf = d.get("confidence", "")
            lines.append(f"  - [{status}] {text} (id={did}, conf={conf})")

    # open questions
    qs = _safe_list(vol.get("open_questions"))
    lines.append("* open_questions:")
    if not qs:
        lines.append("  - (none)")
    else:
        for q in qs:
            if not isinstance(q, dict):
                continue
            qid = q.get("q_id", "?")
            text = q.get("text", "")
            status = q.get("status", "")
            lines.append(f"  - [{status}] {text} (id={qid})")

    return "```\n" + "\n".join(lines) + "\n```"