# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface_Box v1.3
#
# ROLE:
#   - Boundary_Gate から受け取った InputPacket を正規化し、
#     Core に安全に受け渡すための「薄いインターフェース層」
#
# RESPONSIBILITY TAGS:
#   [INTERFACE]   InputPacket 正規化
#   [DELEGATE]    Core.handle_packet への完全委譲
#   [GUARD]       不正・不足フィールドの最小ガード
#   [DEBUG]       Debugging Subsystem v1.0（観測のみ）
#
# CONSTRAINTS:
#   - 推論しない
#   - 状態を持たない
#   - 命名・CDC・業務判断は行わない
#   - context_splitter は使用しない
# ============================================================

from __future__ import annotations

from typing import Optional
import json

from ovv.bis.types import InputPacket
from ovv.core.ovv_core import handle_packet, CoreResult


# ------------------------------------------------------------
# Debug logging (observation only)
# ------------------------------------------------------------

LAYER_BIS = "BIS"
CP_IFACE_DISPATCH = "IFACE_DISPATCH"


def _log_dispatch(packet: InputPacket) -> None:
    payload = {
        "trace_id": getattr(packet, "trace_id", None) or "UNKNOWN",
        "checkpoint": CP_IFACE_DISPATCH,
        "layer": LAYER_BIS,
        "level": "DEBUG",
        "summary": "interface dispatch to core",
    }
    print(json.dumps(payload, ensure_ascii=False))


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

def handle_request(packet: InputPacket) -> CoreResult:
    """
    Interface_Box の単一エントリ。

    - packet の最低限の整合性のみを確認
    - Core に完全委譲
    """
    if not isinstance(packet, InputPacket):
        # Boundary_Gate が保証する前提だが、念のためのガード
        return CoreResult(discord_output="Invalid input packet.")

    # 観測のみ
    _log_dispatch(packet)

    # 完全委譲
    return handle_packet(packet)