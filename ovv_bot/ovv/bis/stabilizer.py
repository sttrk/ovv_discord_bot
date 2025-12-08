# ovv/bis/stabilizer.py
# ---------------------------------------------------------------------
# Stabilizer Layer
# ・Discord 出力の整形
# ・NotionOps Executor を最後に実行
# ---------------------------------------------------------------------

from external_services.notion.ops.executor import execute_notion_ops


class Stabilizer:
    def __init__(self, *, message_for_user, notion_ops, context_key, user_id):
        self.message_for_user = message_for_user
        self.notion_ops = notion_ops
        self.context_key = context_key
        self.user_id = user_id

    async def finalize(self):
        # 1. Discord 返信内容の整形（必要に応じて拡張）
        formatted = self.message_for_user

        # 2. NotionOps の実行（任意・非同期処理でも可）
        if self.notion_ops:
            await execute_notion_ops(
                self.notion_ops,
                context_key=self.context_key,
                user_id=self.user_id,
                request_id=None,
            )

        return formatted