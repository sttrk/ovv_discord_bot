# ovv/brain/constraint_classifier.py
# Constraint Classifier Utility v1.0
#
# ROLE:
#   - ThreadBrain 用テキストを "soft" / "hard" / "unknown" に分類する軽量ユーティリティ。
#
# USE:
#   - ThreadBrainAdapter v3.2（semantic-cleaning）
#   - 将来 constraint_filter 等からも再利用可能な形で切り出しておく。
#
# MUST:
#   - determinisic (同じ入力には常に同じクラスを返す)
#   - 出力形式関連の「hard 制約」を確実に検出する
#
# MUST NOT:
#   - LLM を呼ばない
#   - 外部 I/O を行わない

from typing import Literal

ConstraintClass = Literal["soft", "hard", "unknown"]


def classify_constraint_text(text: str) -> ConstraintClass:
    """
    制約テキストを soft / hard / unknown に分類する。

    - hard:
        出力形式・構造・システム越境に関する指示（例: JSON で返せ、マークダウン禁止 等）
    - soft:
        会話スタイル・言語・トーンなどの「嗜好・運用ルール」
    - unknown:
        判定不能（上位ロジックで soft 側に寄せる）
    """
    if not text:
        return "unknown"

    t = str(text).strip()
    if not t:
        return "unknown"

    lower = t.lower()

    # -------------------------
    # 1) 明確な "hard" パターン
    # -------------------------

    # JSON / YAML / XML などフォーマット強制
    if "json" in lower or "yaml" in lower or "xml" in lower:
        # 「jsonで返す」「json形式」「jsonオブジェクト」など
        if "返" in t or "形式" in t or "オブジェクト" in t or "only" in lower:
            return "hard"

    # マークダウン禁止・説明文禁止など
    if "マークダウン" in t and ("含めない" in t or "禁止" in t):
        return "hard"
    if "説明文" in t and "含めない" in t:
        return "hard"

    # 構造化データ強制
    if "構造化データ" in t and "返" in t:
        return "hard"
    if "オブジェクト" in t and "のみ" in t and "返" in t:
        return "hard"

    # システム・プロンプト越境系（簡易英語）
    if "ignore the system prompt" in lower or "override the system prompt" in lower:
        return "hard"
    if "jailbreak" in lower:
        return "hard"

    # -------------------------
    # 2) 明確な "soft" パターン
    # -------------------------

    # 敬語/タメ口など会話スタイル
    if "敬語" in t:
        return "soft"
    if "タメ口" in t or "ため口" in t:
        return "soft"

    # 長さ・簡潔さ
    if "短く" in t or "簡潔" in t:
        return "soft"

    # 言語指定（日本語/英語で話す等）※形式ではなく会話ルールとみなす
    if "日本語" in t and ("話す" in t or "答える" in t):
        return "soft"
    if "英語" in t and ("話す" in t or "答える" in t):
        return "soft"

    # スレッド用途の指定（用途ルール）
    if "このスレ" in t or "このスレッド" in t:
        return "soft"

    # -------------------------
    # 3) 判定不能 → unknown
    # -------------------------
    return "unknown"