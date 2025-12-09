# ============================================================
# MODULE CONTRACT: BIS / State Manager
# ROLE:
#   - BIS → Core の処理に必要な「軽量状態」を保持する。
#   - パイプライン実行中の一時的なメモリ（Runtime Memory Lite）。
#
# RESPONSIBILITY:
#   - NotionOps や Core 処理が参照する一時データを格納する。
#   - 永続ではない（ログやDBは別責務）。
#   - Interface → Core 間で共有されるが、Boundary や Stabilizer 越境は禁止。
#
# INBOUND:
#   - Interface_Box から new StateManager() として生成される。
#
# OUTBOUND:
#   - pipeline や core_fn（run_ovv_core）に渡され、dictionary として利用される。
#
# CONSTRAINT:
#   - 永続データ（PostgreSQL・Notion）は扱わない。
#   - Core の業務ロジックを内包しない。
#   - Boundary_Gate に依存してはならない。
# ============================================================

from typing import Any, Dict


# ------------------------------------------------------------
# RESPONSIBILITY TAG: Lightweight Runtime State Container
# ------------------------------------------------------------
class StateManager:
    """
    Core の処理過程で使用する一時的な状態を保持するための
    lightweight なコンテナ。

    例:
      state["user_context"] = {...}
      state["task_status"] = "processing"
    """

    def __init__(self) -> None:
        # 内部状態は dict をベースにする（柔軟性のため）
        self._state: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        """指定キーに値を設定する。"""
        self._state[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """指定キーの値を取得する（なければ default）。"""
        return self._state.get(key, default)

    def all(self) -> Dict[str, Any]:
        """全状態を dict で返す。"""
        return dict(self._state)

    def __repr__(self) -> str:
        return f"StateManager(state={self._state})"