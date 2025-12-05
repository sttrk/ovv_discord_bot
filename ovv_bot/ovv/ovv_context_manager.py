# ovv/ovv_context_manager.py
from typing import List, Dict, Optional


def _shorten(text: str, limit: int = 200) -> str:
    if text is None:
        return ""
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + " ...[truncated]"


def format_thread_brain(summary: Optional[Dict]) -> str:
    """
    thread_brain(JSON) を、GPT に渡しやすい自然文ブロックに変換する。
    Bモード（自然文整形）。
    """
    if not summary:
        return (
            "【スレッド状態（thread_brain）】\n"
            "現在、このスレッドには保存済みの thread_brain 要約がありません。\n"
            "新規のコンテキストとして扱ってください。"
        )

    meta = summary.get("meta", {}) or {}
    status = summary.get("status", {}) or {}

    decisions = summary.get("decisions", []) or []
    unresolved = summary.get("unresolved", []) or []
    constraints = summary.get("constraints", []) or []
    next_actions = summary.get("next_actions", []) or []

    lines: List[str] = []
    lines.append("【スレッド状態（thread_brain）】")

    phase = status.get("phase") or "idle"
    lines.append(f"- フェーズ: {phase}")

    last_event = status.get("last_major_event") or ""
    if last_event:
        lines.append(f"- 直近の重要イベント: {last_event}")

    hl_goal = summary.get("high_level_goal") or ""
    if hl_goal:
        lines.append(f"- 高レベル目標: {hl_goal}")

    digest = summary.get("history_digest") or ""
    if digest:
        lines.append(f"- 履歴ダイジェスト: {_shorten(digest, 240)}")

    if constraints:
        lines.append("- 主な制約条件:")
        for c in constraints[:5]:
            lines.append(f"  - {c}")

    if decisions:
        lines.append("- これまでの主な決定:")
        for d in decisions[:5]:
            lines.append(f"  - {d}")

    if unresolved:
        lines.append("- 未解決の論点・保留事項:")
        for u in unresolved[:5]:
            lines.append(f"  - {u}")

    if next_actions:
        lines.append("- 候補となる次アクション:")
        for a in next_actions[:5]:
            lines.append(f"  - {a}")

    return "\n".join(lines)


def format_recent_memory(recent_mem: List[Dict], limit: int = 20) -> str:
    """
    runtime_memory を、人間が読めるダイジェスト形式に整形。
    """
    if not recent_mem:
        return "【直近の対話ログ】\n直近ログはありません。"

    lines: List[str] = []
    lines.append("【直近の対話ログ（抜粋）】")

    for m in recent_mem[-limit:]:
        role = m.get("role", "user")
        if role == "user":
            prefix = "USER"
        elif role == "assistant":
            prefix = "OVV"
        else:
            prefix = role.upper()

        content = _shorten(m.get("content", ""), 220)
        ts = m.get("ts")
        if ts:
            lines.append(f"{prefix} [{ts}]: {content}")
        else:
            lines.append(f"{prefix}: {content}")

    return "\n".join(lines)


def build_ovv_context_block(
    context_key: int,
    thread_brain_summary: Optional[Dict],
    recent_mem: List[Dict],
) -> str:
    """
    Ovv が推論に使う「コンテキストブロック」全文を組み立てる。
    - thread_brain の要約
    - 直近ログのダイジェスト
    を 1 つの自然文ブロックとして返す。
    """
    parts: List[str] = []

    parts.append("【Ovv コンテキスト概要】")
    parts.append(f"- context_key: {context_key}")
    parts.append("")

    # thread_brain 情報
    parts.append(format_thread_brain(thread_brain_summary))
    parts.append("")

    # recent memory 情報
    parts.append(format_recent_memory(recent_mem))

    parts.append("")
    parts.append(
        "※上記は文脈の説明用ブロックです。この情報を踏まえてユーザー発話に応答してください。"
    )

    return "\n".join(parts)
