# ovv/tb_scoring.py
# Thread Brain → Scoring Layer（A-3 + ConstraintFilter 統合版）

from typing import Optional, Dict, List

from ovv.constraint_filter import filter_constraints


def build_scoring_prompt(summary: Optional[Dict]) -> str:
    """
    Thread Brain summary を解析し、
    「Ovv が次の発話で守るべき優先ルール」を生成する。

    - constraints は constraint_filter 経由でノイズ除去したものだけを使う。
    """

    if not summary:
        return "[TB-Scoring]\nNo summary available. Prioritize clarity and ask user to restate intent."

    status = summary.get("status", {})
    decisions: List[str] = summary.get("decisions", [])
    unresolved: List[str] = summary.get("unresolved", [])
    next_actions: List[str] = summary.get("next_actions", [])
    raw_constraints: List[str] = summary.get("constraints", [])
    goal = summary.get("high_level_goal", "")

    # ★ 新ロジック: constraints を共通フィルタに通す
    constraints = filter_constraints(raw_constraints)

    out = ["[TB-Scoring]"]

    # ======================================================
    # 1. High-level goal
    # ======================================================
    if goal:
        out.append(f"- Maintain alignment with the high-level goal: '{goal}'")

    # ======================================================
    # 2. Constraint Enforcement
    # ======================================================
    if constraints:
        out.append("- Enforce the following constraints strictly:")
        for c in constraints:
            out.append(f"  • {c}")

    # ======================================================
    # 3. Unresolved Items（解消すべき項目）
    # ======================================================
    if unresolved:
        out.append("- Prioritize resolving unresolved items before expanding the topic:")
        for u in unresolved:
            out.append(f"  • {u}")

    # ======================================================
    # 4. Next Actions
    # ======================================================
    if next_actions:
        out.append("- Guide conversation based on next_actions:")
        for a in next_actions:
            out.append(f"  • {a}")

    # ======================================================
    # 5. Decisions（覆さない）
    # ======================================================
    if decisions:
        out.append("- Respect established decisions:")
        for d in decisions:
            out.append(f"  • {d}")

    # ======================================================
    # 6. Risk / Idle detection
    # ======================================================
    phase = status.get("phase")
    if phase == "idle":
        out.append("- Current phase = idle → lean towards proactive clarification.")
    elif phase == "blocked":
        out.append("- Current phase = blocked → propose resolution options.")
    elif phase == "active":
        out.append("- Current phase = active → maintain momentum and avoid digression.")

    return "\n".join(out)