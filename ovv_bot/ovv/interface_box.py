# ovv/interface_box.py
# Interface_Box (BIS Layer-2)
# Boundary_Gate → Interface_Box → Ovv → Stabilizer の中間整形層

from typing import Dict, List, Optional

from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt


def build_input_packet(
    user_text: str,
    runtime_memory: List[Dict],
    thread_brain: Optional[Dict]
) -> Dict:
    """
    Boundary_Gate から渡された入力を Ovv 推論用 InputPacket に正規化する。

    Output Example:
    {
        "user_text": "...",
        "runtime_memory_text": "...",
        "thread_brain_prompt": "...",
        "tb_scoring": "..."
    }
    """

    # ============================================================
    # 1. Runtime Memory → テキスト化（最大20件）
    # ============================================================
    rm_lines = []
    for m in runtime_memory[-20:]:
        role = m.get("role", "user")
        txt = m.get("content", "").replace("\n", " ")
        rm_lines.append(f"{role.upper()}: {txt}")

    runtime_text = "\n".join(rm_lines) if rm_lines else "(no recent memory)"

    # ============================================================
    # 2. Thread Brain → prompt 化
    # ============================================================
    if thread_brain:
        tb_prompt = build_tb_prompt(thread_brain)
        scoring = build_scoring_prompt(thread_brain)
    else:
        tb_prompt = ""
        scoring = ""

    # ============================================================
    # 3. InputPacket 組み立て
    # ============================================================
    packet = {
        "user_text": user_text,
        "runtime_memory_text": runtime_text,
        "thread_brain_prompt": tb_prompt,
        "tb_scoring": scoring,
    }

    return packet
