# ovv/tb_scoring.py
# Thread Brain → Scoring Layer（A-5 強化版）
#
# 目的:
#   - Thread Brain summary から
#       「Ovv が次の発話で守るべき優先ルール」
#     を短く・優先度付きで抽出する。
#   - BIS / Soft-Core に沿って、ブレない行動指針だけを渡す。
#
# インターフェース（既存と完全互換）:
#   build_scoring_prompt(summary: Optional[Dict]) -> str
#
# 出力フォーマット:
#   "[TB-Scoring]\n..."
#
# 特徴:
#   - 未定義フィールドに強く、どのような JSON でも安全に動作。
#   - 「未解決項目 → Next Actions → 制約 → 決定事項 → ゴール → フェーズ/リスク」
#     の順で優先度を明示。
#   - 文字数を抑えつつも、Ovv コアが誤読しないレベルまで情報を圧縮。

from typing import Optional, Dict, List, Any


def _normalize_list(value: Any) -> List[Any]:
    """None / 単一要素 / リストの違いを吸収して常に list を返す。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_text(item: Any, fallback_prefix: str = "") -> str:
    """
    unresolved / next_actions / constraints / decisions などの要素を
    安全にテキストへ変換する。
    """
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        # よくありそうなキーを優先的に拾う
        for key in ("title", "content", "text", "name", "desc", "description"):
            if key in item and isinstance(item[key], str) and item[key].strip():
                return item[key].strip()

        # next_actions 用の簡易フォーマット
        if "action" in item or "step" in item:
            action = str(item.get("action") or item.get("step") or "").strip()
            detail = str(item.get("detail") or item.get("content") or "").strip()
            if action and detail:
                return f"{action}: {detail}"
            if action:
                return action
            if detail:
                return detail

        # それでも拾えなければ短く JSON 化
        try:
            import json

            s = json.dumps(item, ensure_ascii=False)
            if len(s) > 80:
                s = s[:77] + "..."
            return s
        except Exception:
            return fallback_prefix + str(item)[:80]

    # その他（数字など）
    return fallback_prefix + str(item)[:80]


def build_scoring_prompt(summary: Optional[Dict]) -> str:
    """
    Thread Brain summary を解析し、
    「Ovv が次の発話で守るべき優先ルール」を生成する。
    返り値はテキスト 1 本（[TB-Scoring] から始まる）。
    """

    # サマリが無い場合は「とにかく明確に聞き返す」モード
    if not summary:
        return (
            "[TB-Scoring]\n"
            "- No thread_brain summary available.\n"
            "- Prioritize clarity over speculation.\n"
            "- Briefly ask the user to restate their current goal or question."
        )

    status = summary.get("status", {}) or {}
    decisions_raw = _normalize_list(summary.get("decisions"))
    unresolved_raw = _normalize_list(summary.get("unresolved"))
    next_actions_raw = _normalize_list(summary.get("next_actions"))
    constraints_raw = _normalize_list(summary.get("constraints"))
    goal = summary.get("high_level_goal", "") or ""
    risk_raw = _normalize_list(status.get("risk"))
    phase = (status.get("phase") or "").strip()
    last_event = (status.get("last_major_event") or "").strip()

    lines: List[str] = ["[TB-Scoring]"]

    # ======================================================
    # 0. 最新ユーザー発言優先の原則（安全ネット）
    # ======================================================
    lines.append(
        "- ALWAYS prioritize the latest user message over older plans or summaries if they conflict."
    )

    # ======================================================
    # 1. 未解決項目（最優先で解消するべきもの）
    # ======================================================
    unresolved = [_to_text(u, "unresolved: ") for u in unresolved_raw if _to_text(u)]
    if unresolved:
        lines.append(
            "- Highest priority: address unresolved items before starting new topics."
        )
        for u in unresolved[:5]:
            lines.append(f"  • {u}")

    # ======================================================
    # 2. Next Actions（次にやるべきこと）
    # ======================================================
    next_actions = [_to_text(a, "next: ") for a in next_actions_raw if _to_text(a)]
    if next_actions:
        lines.append("- Guide the conversation based on the following next_actions:")
        for a in next_actions[:5]:
            lines.append(f"  • {a}")

    # ======================================================
    # 3. Constraints（守るべき制約）
    # ======================================================
    constraints = [_to_text(c, "constraint: ") for c in constraints_raw if _to_text(c)]
    if constraints:
        lines.append("- Enforce these constraints strictly (do NOT violate them):")
        for c in constraints[:5]:
            lines.append(f"  • {c}")

    # ======================================================
    # 4. Decisions（既に合意したことは安易に覆さない）
    # ======================================================
    decisions = [_to_text(d, "decision: ") for d in decisions_raw if _to_text(d)]
    if decisions:
        lines.append("- Respect the following established decisions unless user changes them:")
        for d in decisions[:5]:
            lines.append(f"  • {d}")

    # ======================================================
    # 5. High-Level Goal（大きな方向性）
    # ======================================================
    if goal:
        lines.append("- Keep alignment with the high-level goal:")
        lines.append(f"  • {goal}")

    # ======================================================
    # 6. Risk / Phase に応じた方針
    # ======================================================
    risks = [_to_text(r, "risk: ") for r in risk_raw if _to_text(r)]
    if phase:
        if phase == "idle":
            lines.append(
                "- Current phase = idle → proactively propose concrete next steps or ask what to focus on."
            )
        elif phase == "active":
            lines.append(
                "- Current phase = active → maintain momentum and avoid unnecessary digressions."
            )
        elif phase == "blocked":
            lines.append(
                "- Current phase = blocked → propose 1–3 clear options to unblock the situation."
            )
        elif phase == "done":
            lines.append(
                "- Current phase = done → help with reflection, summary, or next larger goal."
            )
        else:
            lines.append(f"- Phase hint: {phase}")

    if risks:
        lines.append("- Be careful about the following risks and avoid triggering them:")
        for r in risks[:5]:
            lines.append(f"  • {r}")

    if last_event:
        lines.append(f"- Last major event to keep in mind: {last_event}")

    # ======================================================
    # 7. 冗長カット
    # ======================================================
    text = "\n".join(lines)
    if len(text) > 1200:
        text = text[:1197] + "..."

    return text