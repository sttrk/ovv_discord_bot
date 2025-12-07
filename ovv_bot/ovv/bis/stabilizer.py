# ============================================================
# [MODULE CONTRACT]
# NAME: stabilizer
# ROLE: STAB (Layer 4 — Stabilizer)
#
# INPUT:
#   raw_answer: str | object
#       - Ovv-Core から返る生出力。
#       - ChatCompletionMessage / dict / None の可能性も許容し、
#         Stabilizer が「Discord に送れる文字列」へ正規化する。
#
# OUTPUT:
#   final_answer: str
#       - Discord に送信可能な自然文（最大 1900 文字）
#
# MUST:
#   - [FINAL] セクションのみを抽出する
#   - 見つからない場合は raw_answer を自然文扱いで返す
#   - rewrite / 要約 / 追加生成を行わない
#   - Discord の文字数制限を考慮し truncate
#
# MUST_NOT:
#   - Ovv-Core の推論内容を変形しない
#   - PG / Notion / Interface_Box に依存しない
#   - 辞書構造のまま返さない
#
# ============================================================

from typing import Optional


# ============================================================
# [STAB] extract_final_answer — Core 出力の安定化
# ============================================================
def extract_final_answer(raw_answer: Optional[str]) -> str:
    """
    Stabilizer (Layer 4)
    Core の生出力から Discord に送信可能な最終回答を抽出する。
    """

    # -----------------------------------------
    # [STAB] None / 空文字の安全吸収
    # -----------------------------------------
    if raw_answer is None:
        return ""

    # -----------------------------------------
    # [STAB] Core 出力が「辞書型」「Message型」などの場合に備えて正規化
    # ChatCompletionMessage が subscriptable でない問題をここで吸収する
    # -----------------------------------------
    try:
        # ChatCompletionMessage の場合 → .content を取り出す
        if hasattr(raw_answer, "content"):
            raw_answer = raw_answer.content
    except Exception:
        pass

    # dict や list が来た場合は “構造を壊さず str 化” のみ許可（rewrite はしない）
    if not isinstance(raw_answer, str):
        raw_answer = str(raw_answer)

    text = raw_answer.strip()
    if not text:
        return ""

    # -----------------------------------------
    # [STAB] 最重要: [FINAL] セクション抽出
    # -----------------------------------------
    if "[FINAL]" in text:
        _, final_block = text.split("[FINAL]", 1)
        final_block = final_block.strip()
        return final_block[:1900] if final_block else ""

    # -----------------------------------------
    # [STAB] [FINAL] が無い場合、Core が "FINAL-only モード" で返したとみなし、
    #         生テキストをそのまま Discord に渡す。
    # -----------------------------------------
    return text[:1900]