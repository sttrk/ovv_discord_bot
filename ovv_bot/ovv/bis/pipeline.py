# ============================================================
# MODULE CONTRACT: BIS / Pipeline
# ROLE: Interface → Core の橋渡し（補助モジュール）
# RESPONSIBILITY:
#   - BIS 標準 packet(dict) を Core 呼び出し payload に変換する
#   - Core の実行を一元化する
#   - 外部依存（Notion / State）を payload に同梱する
# INBOUND:
#   - packet (dict) — capture_interface_packet が生成した BIS packet
#   - core_fn — ovv.core.ovv_core.run_ovv_core
# OUTBOUND:
#   - core_result — Core が返す dict
# CONSTRAINT:
#   - Boundary_Gate への逆依存は禁止（BIS上の逆流禁止）
#   - Core を直接 import せず、core_fn として注入される前提
# ============================================================

from typing import Any, Callable, Dict


def build_pipeline(
    core_fn: Callable[[Dict[str, Any]], Any],
    notion_ops: Any,
    state: Any,
) -> Callable[[Dict[str, Any]], Any]:
    """
    Pipeline Builder
    Interface_Box が Core を呼ぶ際に使うパイプラインを構築する。
    """

    # --------------------------------------------------------
    # RESPONSIBILITY TAG: Pipeline Execution Unit
    # --------------------------------------------------------
    def pipeline(packet: Dict[str, Any]) -> Any:
        """
        1. Boundary／InterfaceBox から渡された dict(packet) を受け取る
        2. Core に渡す payload を組成する
        3. Core 実行を行う
        """
        core_payload: Dict[str, Any] = {
            "input_packet": packet,
            "notion_ops": notion_ops,
            "state": state,
        }

        # Core を実行（run_ovv_core など）
        core_result = core_fn(core_payload)

        return core_result

    return pipeline
