# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: External Service / Notion / Executor
#
# ROLE:
#   - Interface_Box / Stabilizer から渡された notion_ops を実行する。
#   - NotionClient を用いて Notion API へ反映する。
#
# INPUT:
#   - notion_ops: dict
#   - context_key: str | None
#   - user_id: str | None
#
# OUTPUT:
#   - None（Notionへ副作用を書き込む）
#
# CONSTRAINT:
#   - 絶対に "external_services" で import してはならない。
#     Ovv の正式パス "ovv.external_services..." を必ず使う。
# ============================================================

from typing import Any, Dict

from ovv.external_services.notion.client import NotionClient


async def execute_notion_ops(
    notion_ops: Dict[str, Any],
    *,
    context_key: str = None,
    user_id: str = None,
    request_id: str = None,
) -> None:
    """
    NotionOps(dict) を実行する。
    各 Op は builder が構築し、ここは副作用のみに徹する。
    """

    client = NotionClient()

    # ops は {"type": "...", "data": {...}} のリスト前提
    if not notion_ops:
        return

    for op in notion_ops.get("ops", []):
        op_type = op.get("type")
        data = op.get("data", {})

        # ---- 個別操作（例） ----
        if op_type == "create_page":
            await client.create_page(data)

        elif op_type == "update_page":
            await client.update_page(data)

        elif op_type == "append_block":
            await client.append_block(data)

        # 追加の NotionOps が増えたらここに追記
        # -------------------------

    return