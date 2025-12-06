# ovv/constraint_filter.py
# Thread Brain Constraint Filter - BIS Edition
#
# 目的:
# - Thread Brain に紛れ込んだ「機械向け・LLM制御向けの制約文」をフィルタリングし、
#   Ovv 推論に不要なノイズを除去する。
# - 人間向けの制約（ドメインルール・仕様・注意点）はできる限り残す。
#
# 責務:
# - あくまで「文字列の選別」のみ行う。
# - 新しい制約を付け足したり、意味内容を書き換えたりしない。

from typing import Optional, Dict, List


_MACHINE_KEYWORDS = [
    # 明らかに LLM 制御っぽい制約
    "必ず JSON",
    "JSON オブジェクトのみ",
    "JSONオブジェクトのみ",
    "マークダウン",
    "Markdown",
    "markdown",
    "コードブロック",
    "```",
    "返答はJSON",
    "回答はJSON",
    "回答は JSON",
    "出力は JSON",
    # 実験用・デバッグ用の指示っぽいもの
    "デバッグ用",
    "実験用",
]


def _is_machine_constraint(text: str) -> bool:
    """機械向けの制約かどうかを雑に判定する。"""
    t = (text or "").strip()
    if not t:
        return False

    # 長さが極端に短いものはそのまま残す
    if len(t) < 5:
        return False

    # 明らかに制御系のキーワードを含むものは除外対象
    for kw in _MACHINE_KEYWORDS:
        if kw in t:
            return True

    return False


def _filter_constraints_list(constraints: List[str]) -> List[str]:
    """constraints 配列から機械向け制約だけを取り除く。"""
    kept: List[str] = []
    for c in constraints:
        if isinstance(c, str):
            if not _is_machine_constraint(c):
                kept.append(c)
        else:
            # dict など他の型はそのまま残す（勝手に解釈しない）
            kept.append(c)
    return kept


def filter_constraints_in_tb(summary: Optional[Dict]) -> Optional[Dict]:
    """
    Thread Brain summary(JSON) 内の constraints をフィルタリングする。
    - 入力が None の場合は None を返す。
    - constraints 以外のフィールドは一切変更しない（コピーして返す）。
    """
    if summary is None:
        return None

    # 浅いコピーで十分（入れ子の中身は変更しない）
    result = dict(summary)

    # 形式が v1（constraints が直下）/ v0（thread_brain 内など）の両方を許容する
    if "constraints" in result and isinstance(result["constraints"], list):
        result["constraints"] = _filter_constraints_list(result["constraints"])

    # v0 系フォーマットの可能性（thread_brain 内に含まれている）
    tb = result.get("thread_brain")
    if isinstance(tb, dict) and isinstance(tb.get("constraints"), list):
        new_tb = dict(tb)
        new_tb["constraints"] = _filter_constraints_list(tb["constraints"])
        result["thread_brain"] = new_tb

    return result