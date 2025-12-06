# ovv/bis/constraint_filter.py
# ============================================================
# Thread Brain Constraint Filter - BIS Formal Edition
#
# 目的:
#   - Thread Brain summary に含まれる「機械的・内部制御系ノイズ制約」を取り除く。
#   - 人間向けの意味情報・仕様的制約のみを残す。
#   - summary の構造を壊さない（Ovv哲学：意味を捏造しない／構造を壊さない）。
#
# 特徴:
#   - v0 / v1 の両形式の Thread Brain summary に対応。
#   - Interface_Box が呼び出す正式 API:
#         filter_constraints_from_thread_brain(summary)
# ============================================================

from typing import Optional, Dict, List, Any

# ============================================================
# フィルタ対象: 機械／LLM制御系ノイズの特徴語
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
    機械制約（LLM制御ノイズ）判定。
    部分一致で判定する。
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
    constraints 配列をフィルタして「正常な人間向け制約」に限定する。
    dict形式と str形式のどちらも扱う。
    BIS/Ovv哲学に従い、意味捏造や構造破壊は行わない。
    """
    cleaned: List[Any] = []

    for item in items:

        # --- v1 形式（dict内に text フィールド） ---
        if isinstance(item, dict) and "text" in item:
            if not _is_machine_constraint(item["text"]):
                cleaned.append(item)
            continue

        # --- v0 形式（constraint が string の場合） ---
        if isinstance(item, str):
            if not _is_machine_constraint(item):
                cleaned.append(item)
            continue

        # --- 不明形式はそのまま保持（構造破壊禁止） ---
        cleaned.append(item)

    return cleaned


# ============================================================
# Public API — Interface_Box が呼ぶ正式フック
# ============================================================
def filter_constraints_from_thread_brain(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Thread Brain summary を安全化する正式 API。
    - v0/v1 両対応
    - constraints 部分のみフィルタし、他の構造・意味は絶対に壊さない
    - None の場合は None を返す
    """

    if summary is None:
        return None

    # 浅いコピーで構造を維持したまま加工
    result = dict(summary)

    # ============================================================
    # v1 形式: summary["constraints"]
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