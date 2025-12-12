# ovv/bis/utils/context_splitter.py
# ============================================================
# MODULE CONTRACT: BIS / context_splitter v1.1
#   (Debugging Subsystem v1.0 aware / Deterministic Normalizer)
#
# ROLE:
#   - Notion タスクサマリ / ThreadWBS / ThreadBrain に渡す前に、
#     テキストから「LLM 向け指示文（制御語）」を除去する。
#
# RESPONSIBILITY TAGS:
#   [NORMALIZE]    入力テキストの正規化
#   [DETECT_CTL]   指示文候補行の検出
#   [FILTER]       指示文行の除去
#   [PUBLIC_API]   呼び出し側向けユーティリティ API
#   [DEBUG_SAFE]   Debugging Subsystem と非干渉（観測のみ・副作用なし）
#
# CONSTRAINTS:
#   - 意味内容（ドメイン情報）は改変しない。
#   - LLM を呼ばない（純ルールベース）。
#   - deterministic（同じ入力には常に同じ出力）。
#   - Discord / Notion / PG / Trace 管理など、他レイヤに依存しない。
#
# NOTE:
#   - Debugging Subsystem v1.0 の trace_id / checkpoint とは直接連携しない。
#     （本モジュールは「内容正規化」のみを責務とする観測非依存ユーティリティ）
# ============================================================

from __future__ import annotations

from typing import Any, Iterable, List


# ------------------------------------------------------------
# [NORMALIZE] テキスト正規化
# ------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """
    軽量な正規化のみ行う。
      - 前後の空白除去
      - 改行構造は維持
    """
    if not isinstance(text, str):
        text = str(text)
    return text.strip()


# ------------------------------------------------------------
# [DETECT_CTL] 指示文候補判定（1行単位）
# ------------------------------------------------------------

# 出力形式 / フォーマット指示
_FORMAT_PATTERNS = [
    "jsonで返", "json で返", "json形式", "json 形式",
    "yamlで返", "yaml で返", "xmlで返", "xml で返",
    "マークダウン禁止", "markdown 禁止",
    "markdownで", "マークダウンで",
    "表形式で返", "テーブル形式で返", "箇条書きで返",
    "コードブロックで返", "```",
]

# ロール / 人格指示
_ROLE_PATTERNS = [
    "として動作しろ", "として動作せよ",
    "として振る舞え", "として振る舞う",
    "あなたは", "you are now", "act as",
]

# システム越境
_SYSTEM_PATTERNS = [
    "system prompt", "システムプロンプト",
    "プロンプトを無視", "prompt を無視",
    "ignore the system prompt", "override the system prompt",
    "jailbreak",
]

# LLM 制御・強制語
_CONTROL_PATTERNS = [
    "のみ返す", "だけ返す", "だけを返す", "のみを返す",
    "以降必ず", "絶対に", "のみで応答", "以外は書かない",
    "説明文なしで", "説明文は不要", "説明はいらない",
]


def _is_likely_instruction_line(line: str) -> bool:
    """
    1行が「LLM 向けの指示文」と見なせるかを判定する。
    ドメイン情報を破壊しないよう、保守的に判定。
    """
    s = line.strip()
    if not s:
        return False

    lower = s.lower()

    # 明示プレフィクス
    if s.startswith(("[PROMPT]", "[prompt]", "[CONTROL]", "[control]")):
        return True

    for pat in _ROLE_PATTERNS:
        if pat in s:
            return True

    for pat in _SYSTEM_PATTERNS:
        if pat.lower() in lower:
            return True

    for pat in _FORMAT_PATTERNS:
        if pat.lower() in lower:
            return True

    for pat in _CONTROL_PATTERNS:
        if pat in s:
            return True

    return False


# ------------------------------------------------------------
# [FILTER] 指示文行の除去ロジック
# ------------------------------------------------------------

def strip_llm_instructions_from_text(text: str) -> str:
    """
    単一テキストから「指示文っぽい行」を除去し、残りを結合して返す。
    """
    norm = _normalize_text(text)
    if not norm:
        return ""

    lines = norm.splitlines()
    kept: List[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if _is_likely_instruction_line(line):
            continue
        kept.append(line)

    return "\n".join(kept).strip()


# ------------------------------------------------------------
# [NORMALIZE] コンテナ入力対応
# ------------------------------------------------------------

def _flatten_iterable_text(items: Iterable[Any]) -> str:
    buf: List[str] = []
    for v in items:
        if v is None:
            continue
        if isinstance(v, str):
            t = v.strip()
            if t:
                buf.append(t)
        else:
            t = str(v).strip()
            if t:
                buf.append(t)
    return "\n".join(buf) if buf else ""


def _extract_text_from_dict(obj: dict) -> str:
    for key in ("content", "text", "message", "body"):
        if key in obj and isinstance(obj[key], str):
            return obj[key]

    parts: List[str] = []
    for v in obj.values():
        if isinstance(v, str):
            t = v.strip()
            if t:
                parts.append(t)

    return "\n".join(parts) if parts else ""


# ------------------------------------------------------------
# [PUBLIC_API]
# ------------------------------------------------------------

def clean_context_text(value: Any) -> str:
    """
    Notion Task Summary / ThreadWBS / ThreadBrain に渡す前の
    文脈テキスト正規化 API。
    """
    if isinstance(value, str):
        return strip_llm_instructions_from_text(value)

    if isinstance(value, (list, tuple)):
        flat = _flatten_iterable_text(value)
        return strip_llm_instructions_from_text(flat)

    if isinstance(value, dict):
        txt = _extract_text_from_dict(value)
        return strip_llm_instructions_from_text(txt)

    return strip_llm_instructions_from_text(str(value))


__all__ = [
    "clean_context_text",
    "strip_llm_instructions_from_text",
]