# ============================================================
# MODULE CONTRACT: context_splitter v1.1
#
# ROLE:
#   - LLM 向け指示文（≠ドメイン情報）を検出・除去し、
#     domain_clean / llm_control の 2 系統に分離する。
#
# RESPONSIBILITY TAGS:
#   [PARSE]       テキスト分解（行レベル）
#   [DETECT]      LLM 指示文の検出（パターン / 形式 / 行頭語彙）
#   [SPLIT]       domain / llm_control への振り分け
#   [RECONSTRUCT] domain_clean テキストの再構築
#
# CONSTRAINTS:
#   - ThreadBrain / NotionSummary どちらにも適用可能
#   - LLM を呼ばない（純ロジック）
#   - DB / BIS / Core から独立したユーティリティ
#   - semantic を変更しない（除去以外の書き換え禁止）
# ============================================================

from __future__ import annotations
from typing import Tuple, List


# ============================================================
# [DETECT] 指示文パターン定義
# ============================================================

# 行頭トリガ（system / instruction / imperative）
INSTRUCTION_PREFIXES = [
    "you are", "as an ai", "as a model",
    "as ovv", "あなたは", "システムとして",
    "必ず", "以降", "次の形式で", "以下の形式で",
    "jsonで返", "json で返", "json形式", "markdown禁止",
]

# 文中トリガ（出力形式・越境指示）
MID_CONSTRAINT_PATTERNS = [
    "jsonで返", "json形式", "json 形式",
    "markdownを含めない", "マークダウンを含めない",
    "構造化データ", "フォーマット", "のみで返", "only return",
    "system prompt", "ignore the system prompt",
    "出力形式", "書式", "フォーマット指定",
]


# ============================================================
# [DETECT] 行が LLM 指示文かを判定する
# ============================================================

def _is_instruction_line(line: str) -> bool:
    """LLM 向け指示語を含むか判定する。"""
    if not line.strip():
        return False

    lower = line.lower()

    # 行頭チェック
    for p in INSTRUCTION_PREFIXES:
        if lower.startswith(p):
            return True

    # 文中パターン
    for p in MID_CONSTRAINT_PATTERNS:
        if p in lower:
            return True

    return False


# ============================================================
# [PARSE] multi-line 文字列を行単位へ
# ============================================================

def _split_lines(text: str) -> List[str]:
    return text.splitlines()


# ============================================================
# [SPLIT] domain/control 振り分け
# ============================================================

def _split_lines_by_kind(lines: List[str]) -> Tuple[List[str], List[str]]:
    domain: List[str] = []
    control: List[str] = []

    for ln in lines:
        if _is_instruction_line(ln):
            control.append(ln)
        else:
            domain.append(ln)

    return domain, control


# ============================================================
# [RECONSTRUCT] domain_clean を構築
# ============================================================

def _reconstruct_domain(domain_lines: List[str]) -> str:
    cleaned = "\n".join([ln for ln in domain_lines if ln.strip()])
    return cleaned.strip()


# ============================================================
# Public API
# ============================================================

def split_context(text: str) -> Tuple[str, List[str]]:
    """
    text → (domain_clean, llm_control_lines)

    OUTPUT:
        domain_clean: 指示語を除いたユーザードメイン
        llm_control_lines: 検出された LLM 指示文のリスト
    """
    if not text:
        return "", []

    lines = _split_lines(text)
    domain_lines, control_lines = _split_lines_by_kind(lines)

    domain_clean = _reconstruct_domain(domain_lines)
    return domain_clean, control_lines