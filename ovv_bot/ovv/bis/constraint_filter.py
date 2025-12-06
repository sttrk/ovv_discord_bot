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
#   - remove(markdown_ban_rules)
#   - support(TB_v0)
#   - support(TB_v1)
#
# MUST_NOT:
#   - invent_content
#   - mutate(non_constraint_fields)
#   - remove(human_conversation_rules)

from typing import Optional, Dict, List, Any

# ============================================================
# 英語系ノイズキーワード（機械制約・LLM制御系）
# ============================================================

_MACHINE_KEYWORDS_EN = [
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


# ============================================================
# 日本語系の危険制約パターン
#   - JSON強制
#   - 構造化データ強制
#   - マークダウン・説明文の禁止
# ============================================================

def _is_japanese_machine_constraint(text: str) -> bool:
    """
    日本語で書かれた「出力形式を不自然に縛る制約」を検出する。
    例:
      - 必ずJSONオブジェクトのみを返すこと
      - JSON形式で返す
      - マークダウンや説明文を含めないこと
    """

    if not text or not isinstance(text, str):
        return False

    # JSON 強制系
    # 例: "必ずJSONオブジェクトのみを返すこと", "JSON形式で返す", "JSONで返す"
    if "json" in text.lower():
        if ("返す" in text) or ("のみ" in text) or ("オブジェクト" in text) or ("形式" in text):
            return True

    # 構造化データ強制っぽい表現
    # 例: "構造化データのみを返す", "オブジェクトのみを返す"
    if "構造化データ" in text and "返す" in text:
        return True
    if "オブジェクト" in text and "のみ" in text and "返す" in text:
        return True

    # マークダウン・説明文の禁止
    # 例: "マークダウンや説明文を含めないこと", "マークダウンを含めない", "説明文を含めない"
    if "マークダウン" in text and ("含めない" in text or "禁止" in text):
        return True
    if "説明文" in text and "含めない" in text:
        return True

    # ここでは「敬語禁止」などの会話スタイル制約は機械制約として扱わない。
    return False


# ============================================================
# 総合判定
# ============================================================

def _is_machine_constraint(text: str) -> bool:
    """
    ThreadBrain constraints のうち、
    LLM の出力を歪める「機械制約 / 形式強制」を排除する。
    """
    if not text or not isinstance(text, str):
        return False

    lowered = text.lower()

    # 1) 英語系キーワード（部分一致）
    for kw in _MACHINE_KEYWORDS_EN:
        if kw in lowered:
            return True

    # 2) 日本語系パターン
    if _is_japanese_machine_constraint(text):
        return True

    return False


def _filter_constraints_list(items: List[Any]) -> List[Any]:
    """
    constraints 配列から危険制約だけ除去し、
    構造と順序は維持する。
    """
    cleaned: List[Any] = []

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