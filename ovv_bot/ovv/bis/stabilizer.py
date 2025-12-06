# ovv/stabilizer.py
"""
[MODULE CONTRACT]
NAME: stabilizer
ROLE: Stabilizer

INPUT:
  - raw_answer: str  # Ovv Core から返ってきた生テキスト（[PROPOSAL]/[AUDIT]/[FINAL] を含みうる）

OUTPUT:
  - final_answer: str  # Discord にそのまま送信可能な最終テキスト（最大 1900 文字程度）

MUST:
  - raw_answer から [FINAL] セクションのみを抽出し、余計な内部ログを取り除く。
  - [FINAL] が存在しない場合は、生テキスト全体を FINAL とみなして返却する。
  - Discord の文字数制限（2000）を考慮し、おおよそ 1900 文字程度に truncate する。

MUST NOT:
  - 文意の rewrite / 要約 / 追加生成を行わない。
  - エラー時に独自の長文メッセージを生成しない（簡易フォールバックのみ許可）。
  - Ovv Core の挙動や仕様を前提にした推論ロジックを追加しない。

BOUNDARY:
  - Stabilizer は BIS の「S」層であり、Ovv Core と Discord の間にのみ存在する。
  - Storage（PG / Notion）や Interface_Box / Boundary_Gate には依存しない（逆方向参照禁止）。
"""

from typing import Optional


def extract_final_answer(raw_answer: Optional[str]) -> str:
    """
    Ovv Core からの生テキストから [FINAL] セクションのみを抽出し、
    Discord に送信可能な安定テキストとして返す。
    """

    if not raw_answer:
        return ""

    text = str(raw_answer).strip()
    if not text:
        return ""

    # 明示的な [FINAL] セクションがある場合
    if "[FINAL]" in text:
        # 最初の [FINAL] 以降を取り出す（複数あっても先頭を優先）
        parts = text.split("[FINAL]", 1)
        final_block = parts[1].strip()
        if not final_block:
            return ""
        # Discord の上限を軽く意識して truncate
        return final_block[:1900]

    # [FINAL] が無い場合 → 生テキストをそのまま FINAL とみなす
    return text[:1900]