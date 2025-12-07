# ============================================================
# [MODULE CONTRACT]
# NAME: interface_box
# ROLE: IFACE (Layer 2 – Interface Box)
#
# INPUT:
#   boundary_packet: BoundaryPacket (dict or dataclass)
#   runtime_memory: list
#   thread_brain: dict | None
#   state_hint: dict | None
#
# OUTPUT:
#   InterfacePacket (dict)
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
# ============================================================

from typing import List, Optional, Dict, Any

from ovv.brain.threadbrain_adapter import build_tb_prompt
from ovv.brain.tb_scoring import build_scoring_prompt
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

# ============================================================
# [IFACE] Interface Packet Builder
# ============================================================
def build_interface_packet(
    boundary_packet: Dict[str, Any],
    runtime_memory: List[dict],
    thread_brain: Optional[dict],
    state_hint: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Interface Box（Layer 2）
    Discord物理レイヤ（Boundary）→ Ovv論理レイヤ（Core入力）
    """

    # -----------------------------------------
    # 1) ThreadBrain の制約フィルタリング
    # -----------------------------------------
    safe_tb = filter_constraints_from_thread_brain(thread_brain) if thread_brain else None

    # -----------------------------------------
    # 2) TB Prompt
    # -----------------------------------------
    tb_prompt = build_tb_prompt(safe_tb) if safe_tb else "[TB]\nNo thread brain available."

    # -----------------------------------------
    # 3) TB-Scoring
    # -----------------------------------------
    tb_scoring = (
        build_scoring_prompt(safe_tb)
        if safe_tb else
        "[TB-Scoring]\nNo summary available. Prioritize clarity."
    )

    # -----------------------------------------
    # 4) InterfacePacket（Core へ渡す論理データ）
    # -----------------------------------------
    iface_packet = {
        "input": boundary_packet["text"],          # user_text
        "memory": runtime_memory,                  # runtime_memory
        "thread_brain": safe_tb,                   # TB v3 filtered
        "state": state_hint or {},                 # 決定状態
        "tb_prompt": tb_prompt,                    # ThreadBrain Prompt
        "tb_scoring": tb_scoring,                  # scoring
        "context_key": boundary_packet["context_key"],
        "session_id": boundary_packet["session_id"],
        "is_task": boundary_packet["is_task_channel"],
    }

    return iface_packet