from typing import Tuple, Dict, Any
from ovv.bis.types import InputPacket

def handle_free_chat(packet: InputPacket) -> Tuple[str, Dict[str, Any]]:
    """
    - 意図を軽く整理
    - volatile にのみ反映
    """
    text = (getattr(packet, "content", "") or "").strip()

    # 超ミニマル分類（推論しない）
    intent = "question" if text.endswith("？") or text.endswith("?") else "note"

    volatile_patch = {
        "intent": {
            "state": "unconfirmed",
            "kind": intent,
            "summary": text[:120],
        }
    }

    reply = (
        "了解。今の考えをメモしておく。\n"
        "確定するなら !wy / !we を使ってくれ。"
    )

    return reply, volatile_patch