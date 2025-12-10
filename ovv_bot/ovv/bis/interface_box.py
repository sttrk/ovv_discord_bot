# ovv/bis/interface_box.py
# ============================================================
# MODULE CONTRACT: BIS / Interface Box
#
# LAYER: BIS-2 (Interface_Box)
#
# ROLE:
#   - Boundary_Gate から InputPacket を受け取り、
#     BIS 標準 packet(dict) に正規化する。
#   - 制約フィルタ / StateManager / Pipeline を束ねて Core を呼び出す。
#   - Core 出力と Request から NotionOps を組み立てる。
#   - Stabilizer を構築し、上位（Boundary_Gate）へ返す。
#
# INPUT:
#   - raw_input: ovv.bis.boundary_gate.InputPacket
#
# OUTPUT:
#   - dict{
#       "packet": dict,          # BIS 標準パケット
#       "core_result": Any,      # Core の戻り値
#       "notion_ops": dict|None, # Notion Executor 向け ops
#       "state": StateManager,   # 状態管理
#       "stabilizer": Stabilizer,# 出力安定化レイヤ
#       "trace": dict            # デバッグ用トレース情報
#     }
#
# CONSTRAINT:
#   - Discord の生オブジェクトを扱わない（それは Boundary_Gate の責務）。
#   - Core / Notion の詳細ロジックには踏み込まない（橋渡しに徹する）。
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
    Core の戻り値から Discord に返すメッセージを抽出する。
    仕様揺れに対して、防御的にフォールバックする。
    """
    if isinstance(core_result, dict):
        return (
            core_result.get("message_for_user")
            or core_result.get("reply_text")
            or core_result.get("content")
            or str(core_result)
        )
    return str(core_result)


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Interface Entry Point
# ------------------------------------------------------------
async def handle_request(raw_input: Any) -> Dict[str, Any]:
    """
    Boundary_Gate から渡された InputPacket を受け取り、
    BIS → Core → NotionOps → Stabilizer に渡すための中間構造を組み立てる。
    """

    # 1. InputPacket → BIS packet(dict) への正規化
    packet = capture_packet(raw_input)

    # 2. 制約フィルタ適用（発話制約・モード制御など）
    packet = apply_constraint_filter(packet)

    # 3. 状態管理オブジェクトの生成
    state = StateManager()

    # 4. Core パイプラインの構築
    #    - 現行の pipeline は notion_ops を payload に同梱する設計だが、
    #      NotionOps は Core 後に builders で構築する方針とするため
    #      ここでは None を渡す。
    pipeline = build_pipeline(
        core_fn=run_ovv_core,
        notion_ops=None,
        state=state,
    )

    # 5. Core 実行（run_ovv_core は同期関数想定）
    core_result = pipeline(packet)

    # 6. NotionOps の組み立て
    #    - Core 出力 + Request(InputPacket) から ops を構築する。
    notion_ops = build_notion_ops(core_output=core_result, request=raw_input)

    # 7. Discord 返信内容の抽出
    message_for_user = _extract_message_for_user(core_result)

    # 8. Stabilizer の構築
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=packet.get("context_key"),
        user_id=str(packet.get("user_id") or ""),
        task_id=str(packet.get("task_id") or None),
    )

    # 9. 上位（Boundary_Gate）に返却するペイロード
    return {
        "packet": packet,
        "core_result": core_result,
        "notion_ops": notion_ops,
        "state": state,
        "stabilizer": stabilizer,
        "trace": {
            "iface": "interface_box",
            "pipeline": "build_pipeline",
            "notion_ops_built": notion_ops is not None,
        },
    }