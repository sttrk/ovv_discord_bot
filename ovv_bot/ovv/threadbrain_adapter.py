# ovv/threadbrain_adapter.py
# Thread Brain → Ovv 推論用 前処理アダプタ（A-2 + ConstraintFilter 統合版）

from typing import Optional, Dict

from ovv.constraint_filter import filter_constraints


def build_tb_prompt(summary: Optional[Dict]) -> str:
    """
    Thread Brain summary(JSON) を Ovv 推論に使えるテキストへ整形する。
    UI版 Ovv の “Long Context Injection” と同等の役割。

    - constraints については constraint_filter でノイズ除去したうえで挿入する。
    """

    if not summary:
        return ""

    status = summary.get("status", {})
    decisions = summary.get("decisions", [])
    unresolved = summary.get("unresolved", [])
    raw_constraints = summary.get("constraints", [])
    next_actions = summary.get("next_actions", [])
    digest = summary.get("history_digest", "")
    goal = summary.get("high_level_goal", "")
    recent = summary.get("recent_messages", [])

    # ★ 新ロジック: constraints を共通フィルタに通す
    constraints = filter_constraints(raw_constraints)

    out = []

    if goal:
        out.append(f"[High-Level Goal]\n{goal}")

    if constraints:
        out.append(
            "[Constraints]\n" + "\n".join(f"- {c}" for c in constraints)
        )

    if unresolved:
        out.append(
            "[Unresolved Items]\n" + "\n".join(f"- {u}" for u in unresolved)
        )

    if decisions:
        out.append(
            "[Key Decisions]\n" + "\n".join(f"- {d}" for d in decisions)
        )

    if next_actions:
        out.append(
            "[Next Actions]\n" + "\n".join(f"- {a}" for a in next_actions)
        )

    if digest:
        out.append(f"[History Digest]\n{digest}")

    if recent:
        out.append(
            "[Recent Messages]\n" + "\n".join(f"- {m}" for m in recent)
        )

    return "\n\n".join(out)