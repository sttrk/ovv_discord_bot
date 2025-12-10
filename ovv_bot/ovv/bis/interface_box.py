# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface Box
#
# LAYER: BIS-2 (Interface_Box)
#
# ROLE:
#   - Boundary_Gate から InputPacket を受け取り、BIS 標準 packet(dict) を生成
#   - 制約フィルタ適用（Identity / 発話制御）
#   - StateManager により thread-state を取得
#   - Core v2.0 を安全に呼び出す（run_ovv_core）
#   - Core 出力を Stabilizer に渡せる形に再構成
#   - NotionOps（必要なら）を構築
#
# OUT:
#   dict{
#       packet, core_result, notion_ops, state, stabilizer, trace
#   }
#
# 禁止事項:
#   - Discord 生オブジェクトの処理（Boundary_Gate の責務）
#   - Core や Persist や External 層の越境
#   - Output の直接フォーマット（Stabilizer の責務）
# ============================================================

from typing import Any, Dict

from ovv.core.ovv_core import run_ovv_core
from ovv.external_services.notion.ops.builders import build_notion_ops

from .capture_interface_packet import capture_packet
from .pipeline import build_pipeline
from .state_manager import StateManager
from .constraint_filter import apply_constraint_filter
from .stabilizer import Stabilizer


def _extract_message_for_user(core_result: Any) -> str:
    """
    Core v2.0 Minimal は message_for_user を必ず dict で返す想定。
    存在しない・壊れている場合でも落ちない防御層。
    """
    if not isinstance(core_result, dict):
        return str(core_result)

    return (
        core_result.get("message_for_user")
        or core_result.get("reply_text")
        or core_result.get("content")
        or str(core_result)
    )


# ============================================================
# RESPONSIBILITY TAG: Interface Entry Point
# ============================================================
async def handle_request(raw_input: Any) -> Dict[str, Any]:
    """
    Boundary_Gate → Interface_Box の正式入口。

    BIS 標準 packet(dict)
    → 制約フィルタ
    → StateManager（thread-state）
    → Core v2.0
    → NotionOps
    → Stabilizer
    """

    # ------------------------------
    # 1. InputPacket → packet(dict)
    # ------------------------------
    packet = capture_packet(raw_input)

    # ------------------------------
    # 2. 制約フィルタ（Identity / Style / 禁則の強制）
    # ------------------------------
    packet = apply_constraint_filter(packet)

    # ------------------------------
    # 3. 状態管理（Persist v3.0 では thread_id = task_id）
    # ------------------------------
    state = StateManager()
    thread_state = state.get(packet.get("task_id"))

    # ------------------------------
    # 4. Core パイプライン構築 → 実行
    # ------------------------------
    pipeline = build_pipeline(
        core_fn=run_ovv_core,
        notion_ops=None,
        state=thread_state,
    )

    core_result = pipeline(packet)

    # ------------------------------
    # 5. Core 出力 → NotionOps 生成
    # ------------------------------
    notion_ops = build_notion_ops(core_output=core_result, request=raw_input)

    # ------------------------------
    # 6. Discord 返信メッセージ抽出
    # ------------------------------
    message_for_user = _extract_message_for_user(core_result)

    # ------------------------------
    # 7. Stabilizer 構築
    # ------------------------------
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=packet.get("context_key"),
        user_id=str(packet.get("user_id") or ""),
        task_id=str(packet.get("task_id") or None),
        command_type=packet.get("command_type"),
        core_output=core_result,
        thread_state=thread_state,
    )

    # ------------------------------
    # 8. 上位へ返却
    # ------------------------------
    return {
        "packet": packet,
        "core_result": core_result,
        "notion_ops": notion_ops,
        "state": thread_state,
        "stabilizer": stabilizer,
        "trace": {
            "iface": "interface_box",
            "pipeline": "build_pipeline",
            "notion_ops_built": bool(notion_ops),
            "command_type": packet.get("command_type"),
        },
    }