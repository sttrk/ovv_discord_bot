# ovv/bis/constraint_filter.py
# ThreadBrainConstraintFilter v3.2 – TB v3.2 正規化ラッパ
#
# [MODULE CONTRACT]
# NAME: constraint_filter
# ROLE: ThreadBrainConstraintFilter_v3
#
# INPUT:
#   summary: dict | None
#
# OUTPUT:
#   cleaned_summary: dict | None
#
# MUST:
#   - normalize_to_TB_v3_2
#   - keep(constraints_soft_only)
#   - drop(format_hard_constraints_indirectly)
#   - be_deterministic
#
# MUST_NOT:
#   - call_LLM
#   - store_constraints_hard
#   - alter_core_meaning
#   - control_output_format

from typing import Optional, Dict, Any

# TB v3.2 正規化ロジックに委譲
from ovv.brain.threadbrain_adapter import normalize_thread_brain


def filter_constraints_from_thread_brain(
    summary: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Thread Brain summary を「TB v3.2 正規形」にそろえるための薄いラッパ。

    役割:
      - v1/v2 形式が来た場合:
          → normalize_thread_brain() によって v3.2 にアップグレードされ、
             constraints_soft のみを残した構造へ正規化される。
      - すでに v3 以降の場合:
          → semantic-cleaning を含む v3.2 仕様で再正規化される。

    ポリシー:
      - hard constraints（JSON強制・フォーマット指定など）は、
        threadbrain_adapter 側で分類・破棄されるため、
        この関数では直接扱わない。
      - ここでは「決定論的な構造正規化レイヤ」としてのみ振る舞う。
    """
    return normalize_thread_brain(summary)