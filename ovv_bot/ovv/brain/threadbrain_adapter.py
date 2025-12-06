# ovv/threadbrain_adapter.py
# Thread Brain → Ovv 推論用 前処理アダプタ（BIS 正式版）
#
# 目的:
# - Thread Brain summary(JSON) を「Ovv が system/context として読みやすいテキスト」に整形する。
# - v0 / v1 など複数の TB フォーマット差異を吸収し、単一のテキスト表現に揃える。
#
# 責務:
# - 既存フィールドの「並べ替え・ラベル付け」のみに限定。
# - 新しい要約を作らない。新しい意味内容を勝手に生まない。

from typing import Optional, Dict, Any, List


def _extract_v1_style(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    v1 スタイル:
      {
        "meta": {...},
        "status": {...},
        "decisions": [...],
        "unresolved": [...],
        "constraints": [...],
        "next_actions": [...],
        "history_digest": "...",
        "high_level_goal": "...",
        "recent_messages": [...],
        "current_position": "..."
      }
    """
    return {
        "meta": summary.get("meta") or {},
        "status": summary.get("status") or {},
        "decisions": summary.get("decisions") or [],
        "unresolved": summary.get("unresolved") or [],
        "constraints": summary.get("constraints") or [],
        "next_actions": summary.get("next_actions") or [],
        "history_digest": summary.get("history_digest") or "",
        "high_level_goal": summary.get("high_level_goal") or "",
        "recent_messages": summary.get("recent_messages") or [],
        "current_position": summary.get("current_position") or "",
    }


def _extract_v0_style(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    v0 スタイル:
      {
        "meta": {...},
        "thread_brain": {
          "summary": "...",
          "recent_logs": [...]
        }
      }
    など旧形式をざっくり吸収する。
    """
    tb = summary.get("thread_brain") or {}
    history_digest = tb.get("summary") or ""
    recent_logs = tb.get("recent_logs") or []

    # recent_logs の形式を recent_messages に寄せる
    recent_messages: List[str] = []
    for item in recent_logs:
        if not isinstance(item, dict):
            continue
        speaker = item.get("speaker") or item.get("role") or ""
        content = item.get("content") or ""
        if not content:
            continue
        if speaker:
            recent_messages.append(f"{speaker}: {content}")
        else:
            recent_messages.append(content)

    return {
        "meta": summary.get("meta") or {},
        "status": {},
        "decisions": [],
        "unresolved": [],
        "constraints": [],
        "next_actions": [],
        "history_digest": history_digest,
        "high_level_goal": "",
        "recent_messages": recent_messages,
        "current_position": "",
    }


def _normalize_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    v0 / v1 などフォーマット差異を吸収して、単一の dict 形式に揃える。
    """
    if "thread_brain" in summary:
        return _extract_v0_style(summary)
    return _extract_v1_style(summary)


def build_tb_prompt(summary: Optional[Dict[str, Any]]) -> str:
    """
    Thread Brain summary(JSON) を Ovv 推論用テキストへ整形する。
    - None の場合は空文字を返す（TB 未利用）。
    - ありもののフィールドだけを組み立てて返す。
    """
    if not summary:
        return ""

    norm = _normalize_summary(summary)

    status = norm.get("status") or {}
    decisions = norm.get("decisions") or []
    unresolved = norm.get("unresolved") or []
    constraints = norm.get("constraints") or []
    next_actions = norm.get("next_actions") or []
    digest = norm.get("history_digest") or ""
    goal = norm.get("high_level_goal") or ""
    recent = norm.get("recent_messages") or []
    current_position = norm.get("current_position") or ""

    out: List[str] = []

    if goal:
        out.append("[High-Level Goal]")
        out.append(goal)

    if constraints:
        out.append("[Constraints]")
        out.extend(f"- {c}" for c in constraints)

    if unresolved:
        out.append("[Unresolved Items]")
        out.extend(f"- {u}" for u in unresolved)

    if decisions:
        out.append("[Key Decisions]")
        out.extend(f"- {d}" for d in decisions)

    if next_actions:
        out.append("[Next Actions]")
        out.extend(f"- {a}" for a in next_actions)

    if digest:
        out.append("[History Digest]")
        out.append(digest)

    if recent:
        out.append("[Recent Messages]")
        for m in recent:
            out.append(f"- {m}")

    if current_position:
        phase = status.get("phase") or ""
        out.append("[Current Position]")
        if phase:
            out.append(f"phase={phase}, position={current_position}")
        else:
            out.append(current_position)

    return "\n".join(out)