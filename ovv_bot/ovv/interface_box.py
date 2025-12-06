# ovv/interface_box.py
# Interface_Box - BIS: Boundary_Gate → Ovv の中間整形レイヤ
#
# 役割:
#   - Boundary_Gate（bot.py）から渡される情報を「InputPacket」として整理する
#   - ThreadBrain の生データを、Ovv が扱いやすいテキスト/スコアリングに変換する
#   - state_hint（軽量ステート）を Ovv に渡すための共通フォーマットを持つ
#
# InputPacket の構造（dict）:
# {
#   "user_text": str,
#   "runtime_memory": List[dict],
#   "thread_brain": Optional[dict],
#   "tb_prompt": str,      # thread_brain の要約（人間/LLM向け）
#   "tb_scoring": str,     # TB-Scoring（優先ルール）
#   "state_hint": Optional[dict],
# }

from typing import List, Optional, Dict, Any

from .threadbrain_adapter import build_tb_prompt
from .tb_scoring import build_scoring_prompt


def build_input_packet(
    user_text: str,
    runtime_memory: List[dict],
    thread_brain: Optional[Dict[str, Any]],
    state_hint: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Boundary_Gate（bot.py）から渡された情報を「InputPacket」としてまとめる。
    ここでは I/O の形式変換に専念し、LLM 呼び出しは絶対に行わない。
    """

    # None 防御
    runtime_memory = runtime_memory or []
    thread_brain = thread_brain or None
    state_hint = state_hint or None

    # ThreadBrain 系テキスト
    tb_prompt = build_tb_prompt(thread_brain) if thread_brain else ""
    tb_scoring = build_scoring_prompt(thread_brain) if thread_brain else ""

    packet: Dict[str, Any] = {
        "user_text": user_text,
        "runtime_memory": runtime_memory,
        "thread_brain": thread_brain,
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
        "state_hint": state_hint,
    }

    return packet