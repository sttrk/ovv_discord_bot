# ovv/bis/memory_kind.py
# Memory Kind Classifier v1.0
#
# [MODULE CONTRACT]
# NAME: memory_kind
# ROLE: IFACE (Domain / Control 分類ヘルパ)
#
# INPUT:
#   - role: str ("user" / "assistant" / "system" など)
#   - content: str
#
# OUTPUT:
#   - kind: str ("domain" / "control" / "system" / "other")
#
# MUST:
#   - LLM 向けのフォーマット指示や一時的な遊びルールなどを "control" として分類する
#   - 通常の会話・設計議論・仕様検討は "domain" として分類する
#
# MUST NOT:
#   - DB I/O を行わない
#   - Discord / Ovv-Core に依存しない
#

from __future__ import annotations

from typing import Literal

Kind = Literal["domain", "control", "system", "other"]


CONTROL_PATTERNS = [
    # 出力形式指定
    "jsonで返", "json で返", "json形式", "json 形式",
    "マークダウン禁止", "markdown 禁止", "markdownで", "マークダウンで",
    # ロール指示
    "として振る舞", "として動作しろ", "あなたは", "you are now",
    # システム/プロンプト指示
    "system prompt", "システムプロンプト", "プロンプトとして扱え",
    "以降必ず", "必ず", "のみで応答",
]

CONTROL_PREFIXES = [
    "[PROMPT]", "[prompt]", "[CONTROL]", "[control]",
]


def _looks_like_control_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return False

    # 明示プレフィクス
    for prefix in CONTROL_PREFIXES:
        if t.startswith(prefix):
            return True

    head = t[:80]
    lowered = head.lower()

    for pat in CONTROL_PATTERNS:
        if pat.lower() in lowered:
            return True

    return False


def classify_memory_kind(role: str, content: str) -> Kind:
    """
    runtime_memory 1件分の kind を判定する。

    設計方針:
      - デフォルトは "domain"
      - 明らかにフォーマット指定・ロール指示・メタ指示なら "control"
      - role == "system" は常に "system"
    """
    if role == "system":
        return "system"

    if _looks_like_control_text(content):
        return "control"

    return "domain"