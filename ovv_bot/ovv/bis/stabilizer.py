# ovv/bis/stabilizer.py
# ============================================================
# MODULE CONTRACT: BIS / Stabilizer
#
# ROLE:
#   - Discord に返すメッセージの最終整形
#   - NotionOps Executor を最後に実行
#
# INPUT:
#   - message_for_user: str
#   - notion_ops: dict | None
#   - context_key: str | None
#   - user_id: str | None
#
# OUTPUT:
#   - finalize() -> str  # Discord に送るメッセージ
#
# CONSTRAINT:
#   - Core / Boundary_Gate へ逆依存しない。
#   - external_services の実行順序をここで集中管理する。
# ============================================================

from ovv.external_services.notion.ops.executor import execute_notion_ops


class Stabilizer:
    def __init__(self, *, message_for_user, notion_ops, context_key, user_id):
        self.message_for_user = message_for_user
        self.notion_ops = notion_ops
        self.context_key = context_key
        self.user_id = user_id

    async def finalize(self) -> str:
        """
        1. Discord 返信内容の整形
        2. NotionOps の実行（あれば）
        3. 整形済みメッセージを返す
        """

        # 1. Discord 返信内容（今はそのまま。将来ここで装飾してよい）
        formatted = self.message_for_user

        # 2. NotionOps の実行
        if self.notion_ops:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
                request_id=None,
            )

        # 3. Discord 用メッセージを返す
        return formatted