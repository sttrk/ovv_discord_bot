# ovv/bis/pipeline.py
# ============================================================
# MODULE CONTRACT: BIS / Pipeline
#
# ROLE:
#   - Interface_Box と Core v2.0 のあいだの薄いアダプタ
#   - Core の呼び出し方法を 1 箇所に閉じ込めておく
#
# IN:
#   - core_fn : callable (run_ovv_core)
#   - notion_ops : Any (現状 None 想定 / 将来拡張用)
#   - state : dict | None （thread-state）
#
# OUT:
#   - pipeline(packet: dict) -> core_result(dict)
# ============================================================

from typing import Any, Callable, Dict


def build_pipeline(
    core_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    notion_ops: Any,
    state: Dict[str, Any] | None,
):
    """
    Interface_Box から呼ばれるビルダ。

    ここで CoreInput を組み立て、Core v2.0 を 1 パターンに固定する。
    """

    thread_state: Dict[str, Any] = state or {}

    def pipeline(packet: Dict[str, Any]) -> Dict[str, Any]:
        core_input: Dict[str, Any] = {
            "input_packet": packet,
            "notion_ops": notion_ops,
            "state": thread_state,
        }
        return core_fn(core_input)

    return pipeline