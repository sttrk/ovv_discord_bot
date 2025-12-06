# ovv/tb_scoring.py
# Thread Brain → Scoring Layer（BIS Edition）
#
# 目的:
# - Thread Brain summary を解析し、
#   「Ovv が次の発話で優先的に守るべきルール・フォーカス」をテキスト化する。
#
# 責務:
# - 既存の decisions / unresolved / constraints / next_actions を並べ替えるだけ。
# - 新しい制約や方針を勝手に捏造しない。

from typing import Optional, Dict, List, Any


def build_scoring_prompt(summary: Optional[Dict[str, Any]]) -> str:
    """
    Thread Brain summary を解析し、
    「Ovv が次の発話で守るべき優先ルール」を生成する。
    """
    if not summary:
        return "[TB-Scoring]\nNo summary available. Prioritize clarity and ask user to restate intent."

    status = summary.get("status", {}) or {}
    decisions: List[Any] = summary.get("decisions", []) or []
    unresolved: List[Any] = summary.get("unresolved", []) or []
    next_actions: List[Any] = summary.get("next_actions", []) or []
    constraints: List[Any] = summary.get("constraints", []) or []
    goal = summary.get("high_level_goal", "") or ""

    out: List[str] = ["[TB-Scoring]"]

    # 1. High-level goal
    if goal:
        out.append(f"- Maintain alignment with the high-level goal: '{goal}'")

    # 2. Constraint Enforcement
    if constraints:
        out.append("- Enforce the following constraints strictly:")
        for c in constraints:
            out.append(f"  • {c}")

    # 3. Unresolved Items（優先的に解消する項目）
    if unresolved:
        out.append("- Prioritize resolving unresolved items before expanding the topic:")
        for u in unresolved:
            out.append(f"  • {u}")

    # 4. Next Actions
    if next_actions:
        out.append("- Guide conversation based on next_actions:")
        for a in next_actions:
            out.append(f"  • {a}")

    # 5. Decisions（覆さない）
    if decisions:
        out.append("- Respect established decisions:")
        for d in decisions:
            out.append(f"  • {d}")

    # 6. Risk / Phase detection
    phase = status.get("phase")
    if phase == "idle":
        out.append("- Current phase = idle → lean towards proactive clarification.")
    elif phase == "blocked":
        out.append("- Current phase = blocked → propose resolution options.")
    elif phase == "active":
        out.append("- Current phase = active → maintain momentum and avoid digression.")

    return "\n".join(out)