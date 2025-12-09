# ============================================================
# MODULE CONTRACT: BIS / Capture Interface Packet
# NAME: capture_interface_packet
# LAYER: BIS-2 (Interface Box 内部ユニット)
#
# ROLE:
#   - Boundary_Gate が生成した InputPacket（境界パケット）を
#     BIS 内部で扱う「標準パケット(dict)」に変換する唯一のモジュール。
#
# INPUT:
#   - raw_input: InputPacket
#       - Boundary_Gate（または ovv.bis.types）で定義された dataclass を想定
#
# OUTPUT:
#   - packet: dict
#       - BIS 全体で扱う標準構造
#
# MUST:
#   - InputPacket のフィールドを欠損なく dict にマッピングする
#   - Core, DB, Notion など他レイヤの責務を一切持たない
#
# MUST NOT:
#   - discord.Message を直接扱わない（それは Boundary_Gate の責務）
#   - LLM 呼び出し・外部 API 呼び出しを行わない
#   - 永続化や監査ログの書き込みを行わない
#
# RESPONSIBILITY TAG:
#   - Interface Packet Normalizer (InputPacket → BIS Packet)
# ============================================================

from dataclasses import asdict, is_dataclass
from typing import Any, Dict


def _to_dict(raw_input: Any) -> Dict[str, Any]:
    """
    InputPacket を dict に変換する内部ユーティリティ。
    - dataclass であれば asdict()
    - dict であればそのままコピー
    - それ以外であれば __dict__ をベースに best-effort で変換
    """
    if isinstance(raw_input, dict):
        return dict(raw_input)

    if is_dataclass(raw_input):
        return asdict(raw_input)

    # 最後の手段として __dict__ を見る（dataclass ではないクラスの互換用）
    if hasattr(raw_input, "__dict__"):
        return dict(raw_input.__dict__)

    raise TypeError(
        f"capture_interface_packet: unsupported raw_input type {type(raw_input)!r}"
    )


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Interface Packet Normalizer
# ------------------------------------------------------------
def capture_packet(raw_input: Any) -> Dict[str, Any]:
    """
    Boundary_Gate から渡される InputPacket を、
    BIS 内部で扱う標準パケット(dict)に正規化する。

    期待するフィールド（InputPacket 側）例:
      - context_key: int
      - session_id: str
      - guild_id: Optional[int]
      - channel_id: int
      - thread_id: Optional[int]
      - author_id: int
      - author_name: str
      - text: str
      - is_task_channel: bool
      - created_at: str or datetime
      - attachments_count: int
    """
    base = _to_dict(raw_input)

    packet: Dict[str, Any] = {
        # 入力ソース種別（現状は Discord 固定）
        "source": "discord",

        # セッション／コンテキスト管理用
        "context_key": base.get("context_key"),
        "session_id": base.get("session_id"),

        # Discord 側メタデータ
        "guild_id": base.get("guild_id"),
        "channel_id": base.get("channel_id"),
        "thread_id": base.get("thread_id"),

        "author_id": base.get("author_id"),
        "author_name": base.get("author_name"),

        # 実際のテキスト内容（BIS 内では content に統一）
        "content": base.get("text") or "",

        # チャンネル属性
        "is_task_channel": base.get("is_task_channel", False),

        # 付帯情報
        "created_at": base.get("created_at"),
        "attachments_count": base.get("attachments_count", 0),

        # デバッグ・トレース用に元の InputPacket を保持
        "raw": raw_input,
    }

    return packet