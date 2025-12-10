# ovv/external_services/notion/ops/executor.py
from typing import Any, Dict, List, Optional

from notion_client import Client

from config_notion import NOTION_API_KEY, NOTION_TASK_DB_ID

notion = Client(auth=NOTION_API_KEY)


async def execute_notion_ops(
    ops: List[Dict[str, Any]],
    context_key: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    NotionOps 実行エントリポイント。

    ops 例:
      - {"type": "create_task", "payload": {...}}
      - {"type": "update_task_status", "task_id": "...", "status": "..."}
      - {"type": "update_task_duration", "task_id": "...", "duration_seconds": 123}
    """
    if not ops:
        return

    for op in ops:
        if not isinstance(op, dict):
            continue

        op_type = op.get("type")

        try:
            if op_type == "create_task":
                await _op_create_task(op)
            elif op_type == "update_task_status":
                await _op_update_task_status(op)
            elif op_type == "update_task_duration":
                await _op_update_task_duration(op)
            else:
                print("[NotionOps] unknown op type:", op_type)
        except Exception as e:
            # ここでは落とさずログだけ
            print("[NotionOps] error executing op:", op_type, repr(e))


# ------------------------------------------------------------
# Helper: Task Page 検索
# ------------------------------------------------------------

def _find_task_page_id_by_task_id(task_id: str) -> Optional[str]:
    """
    Notion TaskDB 内で、task_id プロパティが一致するページIDを返す。
    - TaskDB のプロパティキーは "task_id" を想定。
    """
    resp = notion.databases.query(
        database_id=NOTION_TASK_DB_ID,
        filter={
            "property": "task_id",
            "rich_text": {"equals": task_id},
        },
        page_size=1,
    )
    results = resp.get("results") or []
    if not results:
        return None
    return results[0]["id"]


# ------------------------------------------------------------
# OP: update_task_duration
# ------------------------------------------------------------

async def _op_update_task_duration(op: Dict[str, Any]) -> None:
    """
    duration_seconds を Notion TaskDB の duration_time プロパティに同期する。
    """
    task_id = op.get("task_id")
    duration_seconds = op.get("duration_seconds")

    if not task_id or duration_seconds is None:
        return

    page_id = _find_task_page_id_by_task_id(str(task_id))
    if not page_id:
        print("[NotionOps] task page not found for task_id:", task_id)
        return

    # duration_time は「数値プロパティ」として定義している想定
    notion.pages.update(
        page_id=page_id,
        properties={
            "duration_time": {
                "number": int(duration_seconds),
            }
        },
    )


# ------------------------------------------------------------
# 既存の create_task / update_task_status などはそのまま
# ------------------------------------------------------------

async def _op_create_task(op: Dict[str, Any]) -> None:
    # ここは既存実装を残す想定
    payload = op.get("payload") or {}
    notion.pages.create(
        parent={"database_id": NOTION_TASK_DB_ID},
        properties=payload,
    )


async def _op_update_task_status(op: Dict[str, Any]) -> None:
    page_id = op.get("page_id")
    status = op.get("status")
    if not page_id or not status:
        return

    notion.pages.update(
        page_id=page_id,
        properties={
            "status": {
                "status": {"name": status},
            }
        },
    )