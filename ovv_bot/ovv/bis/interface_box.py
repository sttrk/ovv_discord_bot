# ============================================================
# [MODULE CONTRACT]
# NAME: interface_box
# LAYER: BIS-2 (Interface Box)
#
# ROLE:
#   - BoundaryPacket (物理レイヤ) を Ovv Core 入力用の InterfacePacket に変換する。
#   - ThreadBrain / runtime_memory / state_hint を統合し、LLM に渡す直前の構造を組み立てる。
#
# INPUT:
#   - boundary_packet: dict
#   - runtime_memory: list[dict]
#   - thread_brain: dict | None
#   - state_hint: dict | None
#
# OUTPUT:
#   - iface_packet: dict
#
# MUST:
#   - constraint_filter を通した ThreadBrain のみを Core に渡す
#   - runtime_memory を改変せずそのまま渡す
#   - state_hint を "state" に正規化して渡す
#   - TB 用の tb_prompt / tb_scoring を生成する
#
# MUST NOT:
#   - DB / Notion / Discord に触れない
#   - LLM を直接呼ばない
#   - 物理 I/O（print を除く）を行わない
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
    Discord 物理レイヤ（Boundary）→ Ovv 論理レイヤ（Core 入力）
    """

    context_key = boundary_packet.get("context_key")
    session_id = boundary_packet.get("session_id")

    # -----------------------------------------
    # 1) ThreadBrain の制約フィルタリング
    # -----------------------------------------
    safe_tb = filter_constraints_from_thread_brain(thread_brain) if thread_brain else None

    # -----------------------------------------
    # 2) TB Prompt
    # -----------------------------------------
    if safe_tb:
        tb_prompt = build_tb_prompt(safe_tb) or ""
    else:
        tb_prompt = "[TB]\nNo thread brain available."

    # -----------------------------------------
    # 3) TB-Scoring
    # -----------------------------------------
    if safe_tb:
        tb_scoring = build_scoring_prompt(safe_tb) or ""
    else:
        tb_scoring = "[TB-Scoring]\nNo summary available. Prioritize clarity."

    # -----------------------------------------
    # 4) InterfacePacket（Core へ渡す論理データ）
    # -----------------------------------------
    iface_packet: Dict[str, Any] = {
        "input": boundary_packet["text"],              # user_text
        "memory": runtime_memory,                      # runtime_memory
        "thread_brain": safe_tb,                       # filtered TB
        "state": state_hint or {},                     # 決定状態
        "tb_prompt": tb_prompt,                        # ThreadBrain Prompt
        "tb_scoring": tb_scoring,                      # TB scoring
        "context_key": context_key,
        "session_id": session_id,
        "is_task": boundary_packet.get("is_task_channel", False),
    }

    print(f"[BIS-2] InterfaceBox: iface_packet built (ctx={context_key}, session={session_id})")

    return iface_packet