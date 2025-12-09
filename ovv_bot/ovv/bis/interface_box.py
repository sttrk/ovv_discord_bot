# ============================================================
# MODULE CONTRACT: BIS / Interface Box
# ROLE: Boundary → Core の中間層（翻訳・整形・制約処理）
# RESPONSIBILITY:
#   - Boundary_Gate の raw_input(dict) を受け取り packet を生成
#   - ConstraintFilter を適用
#   - pipeline を構築し Core 処理を実行
#   - Stabilizer に渡す “整形済み構造” を返す
# INBOUND:
#   - raw_input (dict)
# OUTBOUND:
#   - {
#       "packet": BIS packet,
#       "core_result": dict,
#       "trace": {...}
#     }
# CONSTRAINT:
#   - BoundaryGate / Core の責務を越境しない
#   - pipeline と capture_packet の責務を吸収しない
# ============================================================

from ovv.external_services.notion.ops.builders import build_notion_ops
from ovv.core.ovv_core import run_ovv_core

# BIS 内部依存
from .capture_interface_packet import capture_packet
from .pipeline import build_pipeline
from .state_manager import StateManager
from .constraint_filter import apply_constraint_filter


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Interface Entry Point
# ------------------------------------------------------------
def handle_request(raw_input: dict):
    """
    Boundary_Gate から渡された raw_input を受け取り、
    BIS → Core → Stabilizer の処理に流す “Interface Box 本体”。
    """

    # 1. InterfacePacket の生成
    packet = capture_packet(raw_input)

    # 2. 制約フィルタ
    packet = apply_constraint_filter(packet)

    # 3. NotionOps & State
    notion_ops = build_notion_ops()
    state = StateManager()

    # 4. Core パイプラインの構築
    pipeline = build_pipeline(
        core_fn=run_ovv_core,
        notion_ops=notion_ops,
        state=state,
    )

    # 5. Core 実行
    core_result = pipeline(packet)

    # 6. Stabilizer で扱いやすい戻り値構造にする
    return {
        "packet": packet,
        "core_result": core_result,
        "trace": {
            "iface": "interface_box",
            "pipeline": "build_pipeline",
        },
    }