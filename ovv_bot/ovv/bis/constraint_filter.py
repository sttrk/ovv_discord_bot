# ovv/bis/constraint_filter.py
# ============================================================
# Thread Brain Constraint Filter - BIS Formal Edition
#
# 目的:
#   - Thread Brain summary に含まれる「機械的・LLM内部制御用」の制約文を取り除き、
#     Ovv コアが扱うべき“人間向けの制約（仕様・ルール・注意点）”のみを残す。
#   - summary の構造は絶対に壊さない。意味内容を追加・捏造しない。
#
# 特徴:
#   - v0 / v1 の両方の Thread Brain summary に対応。
#   - constraints が「直下」または「thread_brain 内」にある両方に対応。
#
# BIS 原則:
#   - Boundary-Gate → Interface_Box → Ovv Core → Stabilizer
#   - Interface_Box は “Thread Brain の安全化（サニタイズ）” を要求するため、
#     本フィルタはその前段でノイズ制約を削除する役割を担う。
# ============================================================

from typing import Optional, Dict, List, Any


# ============================================================
# ノイズ制約として除外すべきキーワード群（機械的・LLM制御用）
# ============================================================
_MACHINE_KEYWORDS = [
    "system",
    "assistant",
    "llm",
    "policy",
    "do not reply",
    "forbidden",
    "internal",
    "developer",
    "instruction",
    "ignore",
    "override",
    "jailbreak",
    "rp jailbreak",
    "###",
    "<!--",
    "-->",
]


def _is_machine_constraint(text: str) -> bool:
    """
    機械向けの制約文（LLM 制御系ノイズ）かどうか判定する。
    完全一致ではなく「部分一致」によるフィルタ。
    """
    if not text or not isinstance(text, str):
        return False

    lowered = text.lower()
    for kw in _MACHINE_KEYWORDS:
        if kw in lowered:
            return True
    return False


def _filter_constraints_list(items: List[Any]) -> List[Any]:
    """
    constraint list を走査して、「人間向けの制約」だけを残す。
    Ovv哲学に従い、意味内容を捏造しない・書き換えない。
    """
    cleaned = []
    for item in items:
        # item が dict なら "text" フィールドを見る（TB v1 フォーマットに対応）
        if isinstance(item, dict) and "text" in item:
            if not _is_machine_constraint(item["text"]):
                cleaned.append(item)
        # item が str の場合はそのまま判定（TB v0 フォーマットに対応）
        elif isinstance(item, str):
            if not _is_machine_constraint(item):
                cleaned.append(item)
        # それ以外は “構造として” そのまま残す（意味は変えない）
        else:
            cleaned.append(item)

    return cleaned


# ============================================================
# Public API（Interface_Box が呼び出す正式メソッド）
# ============================================================
def filter_constraints_from_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Thread Brain summary 内の constraint 部分のみを安全化する。
    - summary 全体の意味構造を壊さない。
    - constraint の「削除」以外の変形・追加は禁止。
    - v0 / v1 形式の両方を扱う。

    戻り値:
      - フィルタ済み summary（dict）
      - None（summary が None の場合）
    """
    if summary is None:
        return None

    # 浅いコピー（summary の構造を壊さずに差し替える）
    result = dict(summary)

    # ============================================================
    # v1 形式: summary["constraints"] が list の場合
    # ============================================================
    if "constraints" in result and isinstance(result["constraints"], list):
        result["constraints"] = _filter_constraints_list(result["constraints"])

    # ============================================================
    # v0 形式: summary["thread_brain"]["constraints"]
    # ============================================================
    tb = result.get("thread_brain")
    if isinstance(tb, dict) and isinstance(tb.get("constraints"), list):
        new_tb = dict(tb)
        new_tb["constraints"] = _filter_constraints_list(tb["constraints"])
        result["thread_brain"] = new_tb

    return result