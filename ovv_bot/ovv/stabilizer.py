# ovv/stabilizer.py
# Stabilizer（BIS Layer-3）
#
# 役割:
# - Ovv の生出力から [FINAL] セクションだけを抽出し、Discord へ返すテキストを安定化させる。
# - 生出力が壊れていても、必ず何かしらのテキストを返す。

from typing import Tuple


def extract_final_answer(raw: str) -> str:
    """
    Ovv 生出力から [FINAL] 部分だけを取り出す。
    - [FINAL] がある → その後ろを返す
    - ない       → 全文をそのまま返す
    - 空 / None  → エラーメッセージを返す
    """
    if not raw:
        return "Ovv の応答生成に失敗しました。少し時間をおいてもう一度試してください。"

    text = raw.strip()
    marker = "[FINAL]"

    if marker in text:
        _, tail = text.split(marker, 1)
        tail = tail.strip()
        if tail:
            return tail
        # [FINAL] 以降が空なら、生テキストを返す
        return text

    return text