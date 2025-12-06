# [MODULE CONTRACT]
# NAME: constraint_filter
# ROLE: ThreadBrainConstraintFilter
#
# INPUT:
#   summary: dict | None
#
# OUTPUT:
#   cleaned_summary: dict | None
#
# MUST:
#   - preserve_structure(summary)
#   - remove(machine_constraints)
#   - remove(json_output_forcing_rules)
#   - support(TB_v0)
#   - support(TB_v1)
#
# MUST_NOT:
#   - invent_content
#   - mutate(non_constraint_fields)
#   - allow(JSON_only_constraints)
#   - allow(Markdown_ban_constraints)

from typing import Optional, Dict, List, Any

# ============================================================
# ノイズ制約キーワード（英語 + 日本語）
# ============================================================

_MACHINE_KEYWORDS = [
    # 英語系ノイズ
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
    "###",
    "<!--",
    "-->",

    # 日本語系の危険制約（今回の問題の原因）
    "必ずjson",            # 必ずJSONで返すこと
    "jsonで返す",          # JSONで返す
    "json形式で返す",
    "jsonのみ",
    "マークダウンを含めない",
    "マークダウン禁止",
    "説明文を含めない",
    "構造化データのみを返す",
    "構造化データで返す",
    "オブジェクトのみ",
]


# ============================================================
# 内部判定
# ============================================================

def _is_machine_constraint(text: str) -> bool:
    """
    ThreadBrain constraints のうち、
    LLM の出力を強制的に構造化方向へ歪める“危険制約”を排除する。
    """
    if not text or not isinstance(text, str):
        return False

    lowered = text.lower()
    original = text

    # 英語・日本語ノイズの部分一致で判定
    for kw in _MACHINE_KEYWORDS:
        if kw in lowered or kw in original:
            return True
    return False


def _filter_constraints_list(items: List[Any]) -> List[Any]:
    """
    constraints 配列から危険制約だけ除去し、
    構造と順序は維持する。
    """
    cleaned = []

    for item in items:
        # dict（v1形式）
        if isinstance(item, dict) and "text" in item:
            if not _is_machine_constraint(item["text"]):
                cleaned.append(item)
            continue

        # str（v0形式）
        if isinstance(item, str):
            if not _is_machine_constraint(item):
                cleaned.append(item)
            continue

        # それ以外は構造破壊禁止のためそのまま保持
        cleaned.append(item)

    return cleaned


# ============================================================
# Public API — Interface_Box が呼ぶ正式フィルタ
# ============================================================

def filter_constraints_from_thread_brain(
    summary: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Thread Brain summary に含まれる「危険制約（JSON強制・Markdown禁止など）」を除去する。
    - 構造は壊さない（dictコピーし、constraints 部分のみ差し替える）
    - v0/v1 の両方に対応する
    """

    if summary is None:
        return None

    # 構造破壊を避けるため shallow copy
    result = dict(summary)

    # --- v1 形式: summary["constraints"]
    if "constraints" in result and isinstance(result["constraints"], list):
        result["constraints"] = _filter_constraints_list(result["constraints"])

    # --- v0 形式: summary["thread_brain"]["constraints"]
    tb = result.get("thread_brain")
    if isinstance(tb, dict) and isinstance(tb.get("constraints"), list):
        new_tb = dict(tb)
        new_tb["constraints"] = _filter_constraints_list(tb["constraints"])
        result["thread_brain"] = new_tb

    return result