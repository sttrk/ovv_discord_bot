# ovv/interface_box.py
# Interface_Box - BIS Edition
#
# 目的:
# - Boundary_Gate（bot.py）から渡された情報を、Ovv が扱いやすい InputPacket にまとめる。
# - Thread Brain / State / Runtime Memory を「構造化」して渡すだけに徹する。
#
# 責務:
# - 要約・推論・補完は禁止（構造化のみ）。
# - 既存の情報をフィルタ・整形して 1 つの dict にパックする。

from typing import List, Dict, Any, Optional

from .threadbrain_adapter import build_tb_prompt
from .tb_scoring import build_scoring_prompt
from .constraint_filter import filter_constraints_in_tb


def build_input_packet(
    user_text: str,
    runtime_memory: List[Dict[str, Any]],
    thread_brain: Optional[Dict[str, Any]],
    state_hint: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Ovv 呼び出し用の InputPacket を構築する。

    Input:
      - user_text: 今回ユーザーが送ったテキスト
      - runtime_memory: PG に蓄積されている短期メモリ（user/assistant）
      - thread_brain: thread_brain summary(JSON) or None
      - state_hint: decide_state が返した軽量ステート or None

    Output (InputPacket):
      {
        "version": "1.1",
        "user_text": str,
        "runtime_memory": [...],
        "thread_brain": {...} or None,
        "tb_prompt": str,
        "tb_scoring": str,
        "state": {...} or {}
      }
    """

    # runtime_memory は念のため最大 40 件程度に絞る（安全サイド）
    if runtime_memory is None:
        runtime_memory = []
    clipped_mem = runtime_memory[-40:]

    # Thread Brain の constraints から機械向け制約を除去したバージョン
    filtered_tb = filter_constraints_in_tb(thread_brain) if thread_brain else None

    # TB → テキスト化（Adapter）
    tb_prompt = build_tb_prompt(filtered_tb) if filtered_tb else ""

    # TB → Scoring（優先ルールヒント）
    tb_scoring = build_scoring_prompt(filtered_tb) if filtered_tb else "[TB-Scoring]\nNo summary available."

    packet: Dict[str, Any] = {
        "version": "1.1",
        "user_text": user_text or "",
        "runtime_memory": clipped_mem,
        "thread_brain": filtered_tb,
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
        "state": state_hint or {},
    }

    return packet