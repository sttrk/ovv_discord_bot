# ovv/bis/utils/context_splitter.py
# ============================================================
# MODULE CONTRACT: BIS / context_splitter v1.0
#
# ROLE:
#   - Notion タスクサマリ / ThreadWBS に渡す前に、
#     テキストから「LLM 向け指示文（制御語）」を除去する。
#
# RESPONSIBILITY TAGS:
#   [NORMALIZE] 入力テキストの正規化
#   [DETECT_CTL] 指示文候補行の検出
#   [FILTER] 指示文行の除去
#   [PUBLIC_API] 呼び出し側向けユーティリティ API
#
# CONSTRAINTS:
#   - 意味内容（ドメイン情報）は改変しない。
#   - LLM を呼ばない（純粋なルールベース）。
#   - deterministic（同じ入力には常に同じ出力）。
#   - Discord / Notion / PG など、他レイヤには依存しない。
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
      - 改行はそのまま維持
    """
    if not isinstance(text, str):
        text = str(text)
    # 全体の前後だけ strip。行ごとの trim は別で行う。
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
    "コードブロックで返", "```",  # コードブロック強制類
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

# LLM 制御・強制語（“だけ返せ”“以降必ず～のみ”など）
_CONTROL_PATTERNS = [
    "のみ返す", "だけ返す", "だけを返す", "のみを返す",
    "以降必ず", "絶対に", "のみで応答", "以外は書かない",
    "説明文なしで", "説明文は不要", "説明はいらない",
]


def _is_likely_instruction_line(line: str) -> bool:
    """
    1行が「LLM 向けの指示文」と見なせるかどうかを判定する。
    ドメイン情報を壊さないよう、やや保守的に判定する。
    """
    s = line.strip()
    if not s:
        return False

    lower = s.lower()

    # 明示的なプレフィクス
    if s.startswith("[PROMPT]") or s.startswith("[prompt]"):
        return True
    if s.startswith("[CONTROL]") or s.startswith("[control]"):
        return True

    # 単純に「あなたは〜として動作しろ」系
    for pat in _ROLE_PATTERNS:
        if pat in s:
            return True

    # システム越境系
    for pat in _SYSTEM_PATTERNS:
        if pat.lower() in lower:
            return True

    # 出力形式指定系
    for pat in _FORMAT_PATTERNS:
        if pat.lower() in lower:
            return True

    # 制御系フレーズ
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

    - 改行単位で行を判定
    - 指示文と判定された行は捨てる
    - 残った行のみを '\n' で再結合
    """
    norm = _normalize_text(text)
    if not norm:
        return ""

    lines = norm.splitlines()
    kept: List[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            # 空行はそのまま残しても良いが、ノイズを減らすためにスキップ
            continue

        if _is_likely_instruction_line(line):
            # LLM 向け指示行 → discard
            continue

        kept.append(line)

    return "\n".join(kept).strip()


# ------------------------------------------------------------
# [NORMALIZE] コンテナ入力への対応
# ------------------------------------------------------------

def _flatten_iterable_text(items: Iterable[Any]) -> str:
    """
    list[str] / tuple[str] などをまとめて 1 本のテキストにする。
    """
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
    if not buf:
        return ""
    return "\n".join(buf)


def _extract_text_from_dict(obj: dict) -> str:
    """
    dict からテキストらしきものを抽出する簡易ロジック。
    - よくあるキー: "content", "text", "message", "body"
    - 見つからない場合は、値のうち str のみを連結
    """
    # 優先キー
    for key in ("content", "text", "message", "body"):
        if key in obj and isinstance(obj[key], str):
            return obj[key]

    # 値のうち str だけ拾う
    parts: List[str] = []
    for v in obj.values():
        if isinstance(v, str):
            t = v.strip()
            if t:
                parts.append(t)

    if not parts:
        return ""

    return "\n".join(parts)


# ------------------------------------------------------------
# [PUBLIC_API] 呼び出し側が使うエントリ
# ------------------------------------------------------------

def clean_context_text(value: Any) -> str:
    """
    Notion Task Summary / ThreadWBS work_item / TB などに渡す前の
    「文脈テキスト」をクリーンアップするための統一 API。

    入力:
      - str
      - list/tuple[str or Any]
      - dict（content/text/message/body などを含むもの）
      - それ以外: str(value) でテキスト化

    出力:
      - LLM 指示文を除去した純粋な“意味文脈”テキスト
    """
    # str ならそのまま
    if isinstance(value, str):
        return strip_llm_instructions_from_text(value)

    # list/tuple 等なら flatten → strip
    if isinstance(value, (list, tuple)):
        flat = _flatten_iterable_text(value)
        return strip_llm_instructions_from_text(flat)

    # dict の場合は「テキストらしき部分」を抽出
    if isinstance(value, dict):
        txt = _extract_text_from_dict(value)
        return strip_llm_instructions_from_text(txt)

    # その他は文字列化して処理
    return strip_llm_instructions_from_text(str(value))


__all__ = [
    "clean_context_text",
    "strip_llm_instructions_from_text",
]