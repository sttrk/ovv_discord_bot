# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer
#
# ROLE:
#   - Core から返されたメッセージを Discord へ安全に出すための
#     最終安定化レイヤ。
#   - NotionOps を Executor に渡すタイミングを制御する。
#   - Persist 層（PostgreSQL / v3.0）への書き込みフックを持つ。
#
# INPUT:
#   - message_for_user: str
#   - notion_ops: dict | None
#   - context_key: str | None
#   - user_id: str | None
#   - task_id: str | None  # ← v3.0 追加
#
# OUTPUT:
#   - finalize() -> str  （Discord に送るメッセージ）
#
# CONSTRAINT:
#   - Discord API を直接叩かない（Boundary_Gate の責務）。
#   - Notion / PG への具体的 I/O は external_services / database に委譲。
# ============================================================

from typing import Any, Dict, Optional

from ovv.external_services.notion.ops.executor import execute_notion_ops


class Stabilizer:
    """
    RESPONSIBILITY TAG: BIS-STABILIZER
    - Core / NotionOps / Persist の間を調停しつつ、
      Discord に返すメッセージを最終確定する。
    """

    def __init__(
        self,
        *,
        message_for_user: str,
        notion_ops: Optional[Dict[str, Any]],
        context_key: Optional[str],
        user_id: str,
        task_id: Optional[str] = None,
    ):
        self._message_for_user = message_for_user
        self._notion_ops = notion_ops
        self._context_key = context_key
        self._user_id = user_id
        self._task_id = task_id  # Persist v3.0 用

    # --------------------------------------------------------
    # internal: Persist v3.0 フック（現時点ではダミー）
    # --------------------------------------------------------
    async def _persist_v3(self) -> None:
        """
        Persist v3.0（PostgreSQL）への書き込みフック。

        いまはまだスキーマ v3.0 の導線のみを確保し、
        実際の INSERT / UPDATE ロジックは後続ステップで実装する。
        """
        # TODO:
        # - command_type / packet / state から task_session / task_log を更新
        # - database.pg / migrate_persist_v3.py で定義済みテーブルに合わせる
        return

    # --------------------------------------------------------
    # public: 最終出力
    # --------------------------------------------------------
    async def finalize(self) -> str:
        """
        1. Persist v3.0 への書き込み（将来）
        2. NotionOps があれば非同期で実行
        3. Discord に返すメッセージ文字列を返却
        """

        # 1. Persist v3.0（将来的に有効化）
        await self._persist_v3()

        # 2. NotionOps 実行
        if self._notion_ops:
            await execute_notion_ops(
                self._notion_ops,
                context_key=self._context_key,
                user_id=self._user_id,
            )

        # 3. Discord へ返す文面
        return self._message_for_user