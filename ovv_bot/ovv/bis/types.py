from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class InputPacket:
    """
    BIS → Core に渡す標準入力パケット。

    互換性維持のため、旧実装で使用していたフィールド名も保持する。
    - raw, source, command, content, author_id, channel_id
      （元の simple InputPacket）
    に加えて、task_id / context_key / user_meta / meta を拡張している。
    """

    raw: str
    source: str
    command: str
    content: str
    author_id: str
    channel_id: str

    # Persist v3.0 / BIS 用の拡張フィールド
    context_key: Optional[str] = None
    task_id: Optional[str] = None
    user_meta: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def user_input(self) -> str:
        """
        一部の旧コードが参照している互換用プロパティ。
        content と同一。
        """
        return self.content


@dataclass
class BISEnvelope:
    """
    Discord などのフロントから BIS 入口までの薄いラッパー。
    現行実装では必須ではないが、他モジュールからの import 互換のため定義しておく。
    """

    message: Any
    content: str
    author_id: str
    channel_id: str
    context_key: Optional[str] = None
    command: Optional[str] = None