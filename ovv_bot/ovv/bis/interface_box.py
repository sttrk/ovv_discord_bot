# ovv/bis/capture_interface_packet.py
# ============================================================
# Capture the last InputPacket for debug commands.
# ============================================================

from __future__ import annotations
from typing import Optional
import json

_last_packet = None


def capture(packet):
    """
    BIS pipeline の入口（Boundary_Gate → Interface_Box 手前）で呼ばれる。
    """
    global _last_packet
    try:
        # packet は dataclass / object の場合があるため強制 dict 化
        if hasattr(packet, "__dict__"):
            _last_packet = packet.__dict__
        else:
            _last_packet = packet
    except Exception:
        _last_packet = {"error": "failed to capture packet"}


def get_last_interface_packet() -> Optional[dict]:
    return _last_packet


def debug_dump() -> str:
    """
    Discord 表示用に整形済文字列を返す。
    """
    if _last_packet is None:
        return "(No packet captured)"

    try:
        text = json.dumps(_last_packet, indent=2, ensure_ascii=False)
        # Discord 2000 文字制限
        return text[:1900]
    except Exception:
        return "(packet dump failed)"