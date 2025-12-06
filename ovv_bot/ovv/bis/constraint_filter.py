# ovv/bis/constraint_filter.py
# Constraint_Filter v2.0 (BIS Layer-1 Final)
#
# 目的:
#   - Core に「曖昧・未定義・情報不足」を流入させない。
#   - Boundary_Gate→Constraint_Filter→Interface_Box の安全ルートを確保。
#
# 出力:
#   {
#     "status": "ok" | "clarify" | "reject",
#     "reason": "...",
#     "message": "...",       # ユーザーへ返す文
#     "clean_text": "..."     # Core に送る安全なテキスト
#   }
#
# 曖昧カテゴリ:
#   A: input_insufficient
#   B: ambiguous_modifier
#   C: missing_required_field
#   D: undefined_action
#   E: referent_not_found
#
# 注意:
#   - 本フィルタは「答えを生成しない」。
#   - Core の安全性を最大化する入口フィルタ。

from typing import Dict, List, Optional


# ============================================================
# ユーティリティ
# ============================================================

AMBIGUOUS_MODIFIERS = [
    "適当に", "いい感じに", "雑に", "ほどほどに", "まあまあ",
    "何となく", "なんとなく", "いつもの感じで", "例のやつで"
]

UNDEFINED_ACTIONS = [
    "対応して", "処理して", "なんとかして", "頼む", "お願いします"
]


def _make(status: str, reason: str, message: str = "", clean: str = "") -> Dict:
    return {
        "status": status,
        "reason": reason,
        "message": message,
        "clean_text": clean
    }


# ============================================================
# メインフィルタ
# ============================================================

def apply_constraint_filter(
    user_text: str,
    runtime_memory: List[Dict],
    thread_brain: Optional[Dict]
) -> Dict:
    """
    Constraint_Filter v2.0（決定版）
    """

    text = user_text.strip()

    # -----------------------------------------
    # A. 内容が空 / 成立しない入力
    # -----------------------------------------
    if len(text) == 0:
        return _make(
            "reject",
            "input_insufficient",
            "ごめん、もう少し詳しく教えてほしい。"
        )

    # 極端に短い曖昧 ("どう？", "これどう", etc.)
    if text in ["どう？", "どう", "これどう", "どう思う？", "どう思う"]:
        return _make(
            "clarify",
            "input_insufficient",
            "もう少し具体的に質問内容を教えてほしい。"
        )

    # -----------------------------------------
    # B. 曖昧修飾語（意味が広すぎて Core が解釈不能）
    # -----------------------------------------
    for w in AMBIGUOUS_MODIFIERS:
        if w in text:
            return _make(
                "clarify",
                "ambiguous_modifier",
                f"「{w}」は曖昧なので、具体的にどうしたいか教えてほしい。"
            )

    # -----------------------------------------
    # C. 必須情報不足（最低限の指定なし）
    # 例: 「コード書いて」→種類不明
    # -----------------------------------------
    if text.startswith("コード書いて") or text == "コード書いて":
        return _make(
            "clarify",
            "missing_required_field",
            "どの言語で？また、作りたい処理の内容を教えてほしい。"
        )

    # -----------------------------------------
    # D. 多義的な動詞（「対応して」「処理して」）
    # -----------------------------------------
    for w in UNDEFINED_ACTIONS:
        if w in text:
            # 文脈が無いなら曖昧
            if len(runtime_memory) < 1:
                return _make(
                    "clarify",
                    "undefined_action",
                    f"「{w}」だけだと意図が絞れない。具体的に何をしたい？"
                )

    # -----------------------------------------
    # E. 参照が曖昧 ("それ", "前のやつ", etc.)
    # -----------------------------------------
    if text in ["それ", "それで", "前のやつ", "前の"]:
        # runtime_memory に該当対象がない場合は曖昧
        if len(runtime_memory) < 2:
            return _make(
                "clarify",
                "referent_not_found",
                "どのメッセージを指している？もう少し詳しく説明してほしい。"
            )

    # -----------------------------------------
    # OK → 安全なテキストを返却
    # -----------------------------------------
    clean_text = text  # 現段階ではそのまま。今後の v3 で正規化ルール追加可能。

    return _make("ok", "pass", clean=clean_text)
