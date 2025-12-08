# ovv/bis/stabilizer.py
"""
[MODULE CONTRACT]
NAME: stabilizer
LAYER: BIS-4 (Stabilizer)
ROLE:
  - Ovv Core からの生テキスト raw_answer から [FINAL] セクションのみを抽出する。

INPUT:
  - raw_answer: str | None

OUTPUT:
  - final_answer: str  # Discord にそのまま送信可能なテキスト

MUST:
  - [FINAL] があればその中身だけを返す
  - [FINAL] が無ければ raw_answer 全体を FINAL とみなす
  - Discord の 2000 文字制限を考慮して ~1900 文字で truncate する

MUST NOT:
  - 要約 / 書き換え / 生成を行わない
  - DB / LLM / Discord に依存しない（print は除く）
"""

from typing import Optional


def extract_final_answer(raw_answer: Optional[str]) -> str:
    """
    Ovv Core からの生テキストから [FINAL] セクションのみを抽出し、
    Discord に送信可能な安定テキストとして返す。
    """
    if not raw_answer:
        print("[BIS-4] Stabilizer: empty raw_answer")
        return ""

    text = str(raw_answer).strip()
    if not text:
        print("[BIS-4] Stabilizer: whitespace raw_answer")
        return ""

    if "[FINAL]" in text:
        parts = text.split("[FINAL]", 1)
        final_block = parts[1].strip()
        if not final_block:
            print("[BIS-4] Stabilizer: [FINAL] present but empty")
            return ""
        out = final_block[:1900]
        print(f"[BIS-4] Stabilizer: [FINAL] extracted (len={len(out)})")
        return out

    out = text[:1900]
    print(f"[BIS-4] Stabilizer: no [FINAL], raw used (len={len(out)})")
    return out