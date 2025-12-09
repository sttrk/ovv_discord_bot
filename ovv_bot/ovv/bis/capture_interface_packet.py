# ============================================================
# MODULE CONTRACT: BIS / Interface Packet Builder
# ROLE: Boundary_Gate → InterfaceBox の標準 packet(dict) を生成
# RESPONSIBILITY:
#   - raw_input(dict) を BIS 標準 packet(dict) に変換する唯一の場所
#   - Boundary_Gate の出力を BIS が理解できる形式へ正規化する
# INBOUND:
#   - raw_input: dict（Boundary_Gate が組み立てた値）
# OUTBOUND:
#   - packet: dict（pipeline 向け BIS packet）
# CONSTRAINT:
#   - Core への直接依存は禁止
#   - Boundary_Gate へ依存はしてよいが、逆流はしない
# ============================================================

from typing import Any, Dict


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Interface Packet Normalizer
# ------------------------------------------------------------
def capture_packet(raw_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Boundary_Gate が作った raw_input(dict) を、
    BIS が内部で使う packet(dict) に正規化する。

    raw_input expected keys:
      - command
      - content
      - author_id
      - channel_id
      - source
    """
    return {
        "source": raw_input.get("source", "discord"),
        "command": raw_input.get("command"),
        "content": raw_input.get("content"),
        "author_id": raw_input.get("author_id"),
        "channel_id": raw_input.get("channel_id"),
        "raw": raw_input,
    }
