# ovv/bis/types.py
# BISレイヤ共通の型定義

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class InputPacket:
    """
    Discord から入ってくる1メッセージを、BISレイヤで扱いやすい形に正規化したもの。

    - raw: 元の辞書（Boundary_Gateで組み立て）
    - source: 入力元（基本は "discord"）
    - command: "!dbg_flow" などのコマンド名（接頭の "!" を除いたものを想定）
    - content: メッセージ生テキスト
    - author_id: 送信者ID
    - channel_id: チャンネルID
    """
    raw: Dict[str, Any]
    source: str = "discord"
    command: Optional[str] = None
    content: Optional[str] = None
    author_id: Optional[str] = None
    channel_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Core に渡しやすいように dict 化するヘルパ。"""
        return asdict(self)
