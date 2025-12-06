# ovv/tb_scoring.py
# Thread Brain → Scoring Layer（A-5 / BIS 対応版）

from typing import Optional, Dict, List, Any


def build_scoring_prompt(summary: Optional[Dict[str, Any]]) -> str:
    """
    Thread Brain summary(JSON) から
    「Ovv が次の発話で守るべき優先ルール」を生成する。

    ・特定のゲーム/ドメインに依存しない汎用設計
    ・存在しないフィールドがあっても安全に動くように防御的に実装
    """

    if not summary:
        return (
            "[TB-Scoring]\n"
            "- No summary available.\n"
            "- Prioritize clarity.\n"
            "- Ask the user to restate or clarify their current goal."
        )

    status: Dict[str, Any] = summary.get("status", {}) or {}
    decisions: List[Any] = summary.get("decisions", []) or []
    unresolved: List[Any] = summary.get("unresolved", []) or []
    next_actions: List[Any] = summary.get("next_actions", []) or []
    constraints: List[Any] = summary.get("constraints", []) or []
    goal: str = summary.get("high_level_goal", "") or ""

    out: List[str] = ["[TB-Scoring]"]

    # ======================================================
    # 1. High-level goal
    # ======================================================
    if goal:
        out.append(f"- Maintain alignment with the high-level goal: '{goal}'")
    else:
        out.append("- No explicit high-level goal → focus on clarifying user intent.")

    # ======================================================
    # 2. Constraint Enforcement
    # ======================================================
    if constraints:
        out.append("- Enforce the following constraints strictly:")
        for c in constraints:
            text = c if isinstance(c, str) else str(c)
            out.append(f"  • {text}")

    # ======================================================
    # 3. Unresolved Items（解消すべき項目）
    #    unresolved があれば最優先
    # ======================================================
    if unresolved:
        out.append("- Prioritize resolving unresolved items before expanding the topic:")
        for u in unresolved:
            text = u if isinstance(u, str) else str(u)
            out.append(f"  • {text}")

    # ======================================================
    # 4. Next Actions
    # ======================================================
    if next_actions:
        out.append("- Guide conversation based on next_actions:")
        for a in next_actions:
            text = a if isinstance(a, str) else str(a)
            out.append(f"  • {text}")

    # ======================================================
    # 5. Decisions（覆さない）
    # ======================================================
    if decisions:
        out.append("- Respect established decisions (do not overturn them lightly):")
        for d in decisions:
            text = d if isinstance(d, str) else str(d)
            out.append(f"  • {text}")

    # ======================================================
    # 6. Risk / Phase detection
    # ======================================================
    phase = status.get("phase")
    if phase == "idle":
        out.append("- Current phase = idle → lean towards proactive clarification and light next steps.")
    elif phase == "blocked":
        out.append("- Current phase = blocked → propose concrete resolution options and unblock the flow.")
    elif phase == "active":
        out.append("- Current phase = active → maintain momentum and avoid unnecessary digression.")

    # その他 status に特記事項があれば軽く反映（任意）
    last_event = status.get("last_major_event")
    if last_event:
        out.append(f"- Last major event: {last_event}")

    return "\n".join(out)