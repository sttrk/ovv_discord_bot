# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer
#
# ROLE:
#   - Core / NotionOps / Persist を束ね、最終的に Discord に返す文字列を確定する。
#   - NotionOps の実行（副作用）を行う。
#   - Persist v3.0（PostgreSQL）へのタスクログ書き込みは、このレイヤに集約する。
#
# INPUT:
#   - message_for_user: str
#   - notion_ops     : dict | None
#   - context_key    : str | None
#   - user_id        : str | None
#   - task_id        : str | None  # Persist v3.0 用（thread_id ベース）
#
# OUTPUT:
#   - str（Discord へ送信する最終メッセージ）
#
# CONSTRAINT:
#   - Discord API を直接叩かない（呼び出し元が行う）。
#   - Core / Boundary_Gate / Interface_Box を逆参照しない。
# ============================================================

from typing import Any, Dict, Optional

from ovv.external_services.notion.ops.executor import execute_notion_ops


class Stabilizer:
    """
    BIS 最終出力レイヤ。
    - Discord への返信メッセージを確定する
    - 必要に応じて NotionOps を実行する
    - Persist v3.0（PostgreSQL）への書き込み起点となる（現時点では未実装）
    """

    def __init__(
        self,
        message_for_user: str,
        notion_ops: Optional[Dict[str, Any]],
        context_key: Optional[str],
        user_id: Optional[str],
        task_id: Optional[str] = None,
    ):
        self.message_for_user = message_for_user
        self.notion_ops = notion_ops
        self.context_key = context_key
        self.user_id = user_id
        self.task_id = task_id  # 将来の Persist v3.0 ログ書き込みで使用

    async def finalize(self) -> str:
        """
        最終出力フェーズ。
        順序:
          1. NotionOps 実行（副作用）
          2. 将来: PostgreSQL Persist への書き込み
          3. Discord へ返すメッセージ文字列を確定
        """

        # 1. NotionOps 実行（あれば）
        if self.notion_ops:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
            )

        # 2. 将来: PostgreSQL Persist への書き込み（タスクログなど）
        #    - task_id（= thread_id）をキーとして task_session / task_log に書き込む。
        #    - 実装は Persist v3.0 のヘルパ関数確定後に追加する。

        # 3. Discord へ返す文字列を確定
        return self.message_for_user