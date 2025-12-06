# [MODULE CONTRACT]
# NAME: interface_box
# ROLE: Interface_Box
#
# INPUT:
#   user_text: str
#   runtime_memory: list
#   thread_brain: dict | None
#   state_hint: dict | None
#
# OUTPUT:
#   InputPacket: dict
#
# MUST:
#   - apply(constraint_filter)
#   - preserve_structure
#   - generate(tb_prompt)
#   - generate(tb_scoring)
#   - map(state_hint -> state)
#   - prepare_for(OvvCoreCallLayer)
#
# MUST_NOT:
#   - invent_meaning
#   - alter(runtime_memory)
#   - bypass(constraint_filter)
#   - perform_IO

from typing import List, Optional, Dict, Any

# Thread Brain → テキスト化 / スコアリング
from ovv.brain.threadbrain_adapter import build_tb_prompt
from ovv.brain.tb_scoring import build_scoring_prompt

# Thread Brain Constraint Filter（危険制約の除去）
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain


def build_input_packet(
    user_text: str,
    runtime_memory: List[dict],
    thread_brain: Optional[dict],
    state_hint: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Interface_Box（BIS 中間層）
    - 入口: user_text / runtime_memory / thread_brain / state_hint
    - 出口: ovv_call がそのまま扱える InputPacket
    """

    # ---------------------------------------------------------
    # 1. ThreadBrain の危険制約フィルタリング
    # ---------------------------------------------------------
    safe_tb: Optional[dict] = None
    if thread_brain:
        safe_tb = filter_constraints_from_thread_brain(thread_brain)

    # ---------------------------------------------------------
    # 2. TB Prompt（LLMへ渡す要約プロンプト）
    # ---------------------------------------------------------
    if safe_tb:
        tb_prompt = build_tb_prompt(safe_tb) or ""
    else:
        # TB が存在しない場合は最小プロンプトを渡す
        tb_prompt = "[TB]\nNo thread brain available."

    # ---------------------------------------------------------
    # 3. TB Scoring（優先順位付け Hint）
    # ---------------------------------------------------------
    if safe_tb:
        tb_scoring = build_scoring_prompt(safe_tb) or ""
    else:
        tb_scoring = "[TB-Scoring]\nNo summary available. Prioritize clarity."

    # ---------------------------------------------------------
    # 4. InputPacket 構築
    # ---------------------------------------------------------
    packet: Dict[str, Any] = {
        "version": "1.0",
        "user_text": user_text,
        "runtime_memory": runtime_memory,
        "thread_brain": safe_tb,       # ← フィルタ済み TB のみを渡す
        "state": state_hint or {},
        "tb_prompt": tb_prompt,
        "tb_scoring": tb_scoring,
    }

    return packet