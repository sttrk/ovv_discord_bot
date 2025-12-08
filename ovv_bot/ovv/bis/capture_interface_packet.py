# ovv/bis/capture_interface_packet.py
# ============================================================
# [MODULE CONTRACT]
# NAME: capture_interface_packet
# LAYER: BIS-Debug (Instrumentation Layer)
#
# ROLE:
#   - 最新の InterfacePacket を保持し、デバッグコマンドから参照できるようにする
#
# MUST:
#   - pipeline 以外に副作用を与えない
#   - Discord API に触れない
#   - Ovv Core ロジックに干渉しない
# ============================================================

_last_iface_packet = None


def capture_interface_packet(packet: dict):
    """
    Interface_Box → Core の直前で、最終的な InterfacePacket を一時保存する。
    デバッグ用であり、本流の処理には影響しない。
    """
    global _last_iface_packet
    _last_iface_packet = packet


def get_last_interface_packet():
    """
    debug_commands から参照するためのゲッター。
    """
    return _last_iface_packet