# ============================================================
# [MODULE CONTRACT]
# NAME: domain_control_splitter
# ROLE: TB Domain/Control Separator (TB v3.1)
#
# INPUT:
#   tb: dict | None    - ThreadBrain (filtered)
#
# OUTPUT:
#   domain_tb: dict | None
#   control_tb: dict | None
#
# PURPOSE:
#   - TB から「LLM 向けの指示（制御語）」を分離し、
#     TB を純粋なドメイン状態だけにする。
#
# MUST:
#   - TB の構造を破壊しない
#   - domain_tb に control 用語を混入させない
#   - control_tb をオプションとして保持し、Core は利用しない（拡張ポイント）
#
# MUST_NOT:
#   - TB を改変し意味を変えてはならない
#   - Control 情報を domain_tb に戻してはならない
# ============================================================

from typing import Dict, Any, Tuple, Optional


CONTROL_KEYS = {
    "format",            # 例: "json", "markdown 禁止"
    "output_style",      # 例: "short", "long"
    "role_instruction",  # 例: "あなたはOvvとして動作せよ"
    "constraints_soft",  # LLM向けの形式制約
    "llm_rules",         # 将来の追加ルール
}


def split_thread_brain(tb: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    TB を Domain 情報と Control 情報に分離する。

    Domain:
        - decisions
        - unresolved
        - next_actions
        - history_digest
        - high_level_goal
        - recent_messages
        - meta のうち domain に関係するもの

    Control:
        - constraints_soft
        - format
        - output_style
        - role_instruction
        - その他 LLM向けの操作命令
    """

    if tb is None:
        return None, None

    domain: Dict[str, Any] = {}
    control: Dict[str, Any] = {}

    for key, value in tb.items():
        # Control ワードまたはキーなら control_tb に吸収
        if key in CONTROL_KEYS:
            control[key] = value
            continue

        # 値が LLM向け指示語を含む場合も control 側へ送る
        if isinstance(value, str):
            v = value.lower()
            if any(x in v for x in ["jsonで返", "markdown", "禁止", "フォーマット"]):
                control[key] = value
                continue

        # Domain 側として保持
        domain[key] = value

    # 完全空の場合は None にする
    if not domain:
        domain = None
    if not control:
        control = None

    return domain, control