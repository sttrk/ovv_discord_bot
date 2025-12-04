# debug/__init__.py
"""
Ovv Debug System package.

- debug_static_messages: 各 debug チャンネル用の固定メッセージ
- debug_router:          チャンネル名と役割のマッピング
- debug_boot:            起動時の boot_log / 初期メッセージ送信
- debug_commands:        Discord コマンド (!sql, !diag など)
"""

from .debug_boot import send_boot_message
from .debug_commands import setup_debug_commands

__all__ = [
    "send_boot_message",
    "setup_debug_commands",
]