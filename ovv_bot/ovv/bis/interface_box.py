# ovv/bis/interface_box.py
"""
Interface_Box
Boundary_Gate から受け取った InputPacket を Core に渡し、
Core の出力を Stabilizer に転送する中間レイヤ。

責務：
- 入力の正規化（軽度）
- task_id / user_meta の受け渡し
- Core との I/O 接続
- Stabilizer へのブリッジ

禁止事項：
- Core ロジックの混入
- Notion API への直接アクセス
- DB 永続化ロジックの混入
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ovv.core.ovv_core import run_core
from .stabilizer import Stabilizer


# ------------------------------------------------------------
# InputPacket（BIS 正式仕様）
# ------------------------------------------------------------

@dataclass
class InputPacket:
    context_key: str                # Discord thread_id
    user_input: str                 # ユーザーのメッセージ内容（正規化後）
    task_id: Optional[str] = None   # Persist v3.0 用 task_id
    user_meta: Dict[str, Any] = field(default_factory=dict)
    raw_event: Any = None           # Boundary の生イベント（監査用に保持）


# ------------------------------------------------------------
# Packet Builder（Boundary_Gate から呼ばれる）
# ------------------------------------------------------------

def capture_interface_packet(context_key: str,
                             user_input: str,
                             task_id: Optional[str],
                             user_meta: Dict[str, Any],
                             raw_event: Any = None) -> InputPacket:
    """
    Boundary_Gate → Interface_Box の入口。
    BIS InputPacket を生成する。
    """
    return InputPacket(
        context_key=context_key,
        user_input=user_input,
        task_id=task_id,
        user_meta=user_meta or {},
        raw_event=raw_event,
    )


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------

async def handle_request(packet: InputPacket) -> Dict[str, Any]:
    """
    Interface_Box の中心メソッド。
    Core へ packet を渡し、戻り値を Stabilizer に送る。

    戻り値：
        dict（Stabilizer が整形した Discord 向け最終レスポンス）
    """

    # 1) Core 実行（同期 or 非同期は Core 内で吸収）
    core_result = run_core(
        context_key=packet.context_key,
        user_input=packet.user_input,
        task_id=packet.task_id,
        user_meta=packet.user_meta,
    )

    # 2) Stabilizer に転送
    stabilizer = Stabilizer()
    final_output = await stabilizer.process(
        context_key=packet.context_key,
        core_output=core_result,
        user_id=packet.user_meta.get("user_id"),
        task_id=packet.task_id,
    )

    return final_output