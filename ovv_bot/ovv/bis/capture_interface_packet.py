# ovv/bis/capture_interface_packet.py
# ============================================================
# MODULE CONTRACT: BIS / Capture Interface Packet
# ROLE:
#   - Boundary_Gate から渡された InputPacket / dict を
#     BIS 標準 packet(dict) に正規化する。
#
# INPUT:
#   - raw_input: InputPacket or dict
#
# OUTPUT:
#   - packet: dict (BIS 標準パケット)
#
# CONSTRAINT:
#   - Discord の生 I/O はここで完結させ、下層には渡さない。
#   - Core / external_services への依存は持たない。
# ============================================================

from typing import Any, Dict


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def capture_packet(raw_input: Any) -> Dict[str, Any]:
    """
    Boundary_Gate の InputPacket もしくは dict から
    BIS 標準 packet(dict) を生成する。
    """

    # --------------------------------------------------------
    # ケース1: Boundary_Gate.InputPacket オブジェクト
    # --------------------------------------------------------
    if hasattr(raw_input, "raw_message"):
        # InputPacket の属性
        context_key = _safe_getattr(raw_input, "context_key")
        command_type = _safe_getattr(raw_input, "command_type")
        payload = _safe_getattr(raw_input, "payload")
        user_meta = _safe_getattr(raw_input, "user_meta")
        message = _safe_getattr(raw_input, "raw_message")

        # Discord message オブジェクトから必要情報を抽出
        content = _safe_getattr(message, "content")
        channel = _safe_getattr(message, "channel")
        guild = _safe_getattr(message, "guild")
        author = _safe_getattr(message, "author")

        channel_id = _safe_getattr(channel, "id")
        guild_id = _safe_getattr(guild, "id")
        user_id = _safe_getattr(author, "id")
        username = _safe_getattr(author, "name") or _safe_getattr(
            author, "display_name"
        )

        packet: Dict[str, Any] = {
            # 入力メタ
            "source": "discord",
            "context_key": context_key,
            "command_type": command_type,
            "payload": payload,
            "user_meta": user_meta,
            # Discord 生情報（必要最低限）
            "raw_content": content,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "username": username,
        }
        return packet

    # --------------------------------------------------------
    # ケース2: すでに dict で渡された場合（後方互換用）
    # --------------------------------------------------------
    if isinstance(raw_input, dict):
        packet = dict(raw_input)  # 浅いコピー
        packet.setdefault("source", "discord")
        return packet

    # --------------------------------------------------------
    # フォールバック: 想定外の型
    # --------------------------------------------------------
    return {
        "source": "unknown",
        "raw_input_repr": repr(raw_input),
        "context_key": None,
        "command_type": None,
        "payload": None,
        "user_meta": None,
    }