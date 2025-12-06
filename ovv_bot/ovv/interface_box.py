# ovv/interface_box.py
# Interface_Box（BIS Layer-2）
#
# 役割:
# - Boundary_Gate から渡された raw user text / runtime_memory / thread_brain を
#   Ovv が扱いやすい InputPacket に正規化する。
# - 推論中に勝手な補完を行わない。未定義は "" もしくは None を維持。
# - 構造破壊を避け、Ovv Core が扱いやすい最小構造へ整形する。

from typing import Dict, List, Optional, Any

from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt


def _format_runtime_memory(runtime_memory: List[Dict[str, Any]], limit: int = 30) -> str:
    """
    runtime_memory（PG 保存用 JSON）を Ovv へのヒント用テキストに整形。
    あくまでヒント用なので省略形でよい。
    """
    if not runtime_memory:
        return "(no recent memory)"

    lines: List[str] = []
    for m in runtime_memory[-limit:]:
        role = (m.get("role") or "user").upper()
        content = (m.get("content") or "").replace("\n", " ")
        if len(content) > 200:
            content = content[:200] + " ..."
        lines.append(f"{role}: {content}")

    return "\n".join(lines) if lines else "(no recent memory)"


def build_input_packet(
    user_text: str,
    runtime_memory: List[Dict[str, Any]],
    thread_brain: Optional[Dict[str, Any]] = None,
    state_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Boundary_Gate → Interface_Box → Ovv の中継で使用する InputPacket を構築する。
    """

    tb_prompt = build_tb_prompt(thread_brain) if thread_brain else ""
    tb_scoring_hint = build_scoring_prompt(thread_brain) if thread_brain else ""

    packet: Dict[str, Any] = {
        "user_text": user_text,
        "runtime_memory": runtime_memory or [],
        "runtime_memory_text": _format_runtime_memory(runtime_memory or []),
        "thread_brain": thread_brain,
        "thread_brain_prompt": tb_prompt,
        "tb_scoring_hint": tb_scoring_hint,
        "state_hint": state_hint,
    }

    return packet