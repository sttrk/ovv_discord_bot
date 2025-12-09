# ovv/bis/interface_box.py
# Interface Box — BISアーキテクチャの中間層（Boundary → Core の翻訳を担当）

from ovv.external_services.notion.ops.builders import build_notion_ops
from ovv.core.ovv_core import run_ovv_core
from .capture_interface_packet import capture_packet
from .pipeline import build_pipeline
from .state_manager import StateManager
from .constraint_filter import apply_constraint_filter


def handle_request(raw_input: dict):
    """
    Boundary_Gate から渡された raw_input を受け取り、
    Ovv Core が扱える packet に変換して実行し、Stabilizer に渡せる形で返す。
    """

    # 1. Interface Packet の生成
    packet = capture_packet(raw_input)

    # 2. Constraint Filter の適用（入力検査 / 例外化）
    packet = apply_constraint_filter(packet)

    # 3. Notion Ops（必要であれば pipeline に統合）
    notion_ops = build_notion_ops()
    state = StateManager()

    # 4. Core 実行用 pipeline を組み立て
    pipeline = build_pipeline(core_fn=run_ovv_core, notion_ops=notion_ops, state=state)

    # 5. Core を実行
    core_result = pipeline(packet)

    # 6. Boundary → IFACE → CORE の trace を戻り値に付与（Stabilizer が使う）
    return {
        "packet": packet,
        "core_result": core_result,
        "trace": {
            "iface": "interface_box",
            "pipeline": pipeline.__name__ if hasattr(pipeline, "__name__") else "pipeline",
        },
    }