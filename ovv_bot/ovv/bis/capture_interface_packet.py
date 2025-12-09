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

def capture_packet(raw_input: dict):
    """
    Discord Boundary から受け取った raw_input を
    BIS が扱う Interface Packet に変換する最低限の実装。
    後で拡張可能。
    """
    return {
        "source": "discord",
        "raw": raw_input,
        "command": raw_input.get("command"),
        "content": raw_input.get("content"),
        "author_id": raw_input.get("author_id"),
    }