from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ============================================================
# BIS Core Data Structures
# ============================================================

@dataclass
class InputPacket:
    """
    BIS → Core に渡す標準入力パケット。

    Debugging Subsystem v1.0 対応:
      - trace_id を正式フィールドとして保持する（Single Trace Rule）
      - trace_id は Boundary_Gate でのみ生成される
      - 下流レイヤは読み取り専用で使用する

    互換性維持のため、旧実装で使用していたフィールド名も保持する。
    """

    # ========================================================
    # Original / Compatibility Fields
    # ========================================================

    raw: str
    source: str
    command: str
    content: str
    author_id: str
    channel_id: str

    # ========================================================
    # Debugging Subsystem v1.0 (NEW / OFFICIAL)
    # ========================================================

    trace_id: Optional[str] = None
    """
    Debug trace identifier (UUIDv4).
    - MUST be generated at Boundary_Gate only
    - MUST be propagated unchanged
    - MUST NOT be generated / overwritten downstream
    """

    # ========================================================
    # BIS / Persist Extended Fields
    # ========================================================

    context_key: Optional[str] = None
    task_id: Optional[str] = None
    user_meta: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ========================================================
    # Compatibility Helpers
    # ========================================================

    @property
    def user_input(self) -> str:
        """
        旧コード互換用プロパティ。
        content と同一。
        """
        return self.content

    def get_trace_id(self) -> str:
        """
        安全な trace_id 取得ヘルパ。

        優先順位:
          1. self.trace_id
          2. self.meta["trace_id"]
          3. "UNKNOWN"

        NOTE:
          - Boundary_Gate が正式フィールドに設定するのが正
          - meta はフォールバック用途のみ
        """
        if isinstance(self.trace_id, str) and self.trace_id:
            return self.trace_id

        if isinstance(self.meta, dict):
            mt = self.meta.get("trace_id")
            if isinstance(mt, str) and mt:
                return mt

        return "UNKNOWN"


# ============================================================
# Thin Envelope (Compatibility Only)
# ============================================================

@dataclass
class BISEnvelope:
    """
    Discord などのフロントから BIS 入口までの薄いラッパー。

    現行の Boundary_Gate / BIS パイプラインでは使用されていないが、
    旧コード・外部モジュールとの import 互換のため定義を維持する。

    Debugging Subsystem v1.0:
      - trace_id は持たせない
      - 観測対象外（Boundary_Gate で InputPacket に変換される）
    """

    message: Any
    content: str
    author_id: str
    channel_id: str
    context_key: Optional[str] = None
    command: Optional[str] = None