# ovv/constraint_filter.py
# Constraint Filter - BIS Interface Layer
#
# 目的:
#   - Thread Brain が生成した constraints から
#     「内部システム用ノイズ（例: JSONのみで返す など）」を除外し、
#     Ovv 推論に渡すべきユーザー本位の制約だけを抽出する。
#
# 責務:
#   - constraints のフィルタリングロジックを 1 箇所に集約し、
#     threadbrain_adapter / tb_scoring から共通利用する。
#
# 注意:
#   - ここで除外されるのは「Ovv 内部の実装都合」系の制約のみ。
#   - ユーザーが将来、本当に「JSONだけ返して」と要求した場合は、
#     Thread Brain 側の構造やタグ分けで明示的に区別する想定。

from typing import Any, List, Optional


# 「内部仕様っぽい」制約を検出するための簡易パターン
# （全角・半角スペースは normalize 時に除去）
_INTERNAL_CONSTRAINT_PATTERNS = [
    "jsonオブジェクトのみで返す",
    "jsonのみで返す",
    "jsonだけ返す",
    "jsonのみを返す",
    "マークダウンや説明文を含めない",
    "マークダウンや説明文を含めてはならない",
    "markdownを含めない",
    "markdown禁止",
]


def _normalize(text: str) -> str:
    """比較用に全角・半角スペースを除去して小文字化。"""
    if text is None:
        return ""
    t = str(text)
    for ch in [" ", "　", "\n", "\t"]:
        t = t.replace(ch, "")
    return t.lower()


def _is_internal_constraint(text: str) -> bool:
    """
    Thread Brain 内部で使うだけの制約（例: JSON-only, Markdown禁止）かどうかを判定。
    将来、必要になればここにパターンを追加する。
    """
    norm = _normalize(text)
    if not norm:
        return False
    for pat in _INTERNAL_CONSTRAINT_PATTERNS:
        if pat in norm:
            return True
    return False


def _extract_text_from_constraint(c: Any) -> Optional[str]:
    """
    constraint 要素が str / dict / その他 いずれの場合でも、
    Ovv が解釈しやすいテキストへ変換する。
    """
    if c is None:
        return None

    # もっとも多いケース: すでに str
    if isinstance(c, str):
        return c.strip() or None

    # dict の場合はよくあるフィールド名を見る
    if isinstance(c, dict):
        for key in ("text", "content", "value", "message"):
            v = c.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

    # その他は素直に str 化
    s = str(c).strip()
    return s or None


def filter_constraints(raw_constraints: Any) -> List[str]:
    """
    Thread Brain summary 内の constraints から、
    Ovv 推論に渡すべき制約のみを抽出する。

    Parameters
    ----------
    raw_constraints : Any
        summary.get("constraints") で取得した値（list / None / その他）

    Returns
    -------
    List[str]
        フィルタ済みの constraint テキスト一覧
    """
    if raw_constraints is None:
        return []

    # list 以外が来た場合にも保守的に対応
    if not isinstance(raw_constraints, list):
        raw_constraints = [raw_constraints]

    result: List[str] = []

    for c in raw_constraints:
        text = _extract_text_from_constraint(c)
        if not text:
            continue

        # 内部用ノイズは除外
        if _is_internal_constraint(text):
            continue

        result.append(text)

    return result