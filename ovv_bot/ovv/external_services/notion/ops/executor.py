"""
NotionOps Executor
Notion DB に書き込みを行う唯一の層。
"""

from typing import Dict, Any, Optional

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------
async def execute_notion_ops(ops: Dict[str, Any], context_key: str, user_id: str):
    """
    NotionOps(dict) を Notion API に適用する。
    Core / BIS からはここだけ呼ばれる。
    """

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion client is None → Skipped.")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] NOTION_TASK_DB_ID missing → Skip all task ops.")
        return

    op = ops.get("op")

    if op == "task_create":
        await _create_task_item(notion, ops)

    elif op == "task_start":
        await _update_task_status(notion, ops, status="in_progress")

    elif op == "task_end":
        await _update_task_status(notion, ops, status="completed")

    else:
        print(f"[NotionOps] Unknown op: {op}")


# ------------------------------------------------------------
# Task DB operations
# ------------------------------------------------------------

async def _create_task_item(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    created_by = ops["created_by"]
    msg = ops.get("core_message", "")

    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "task_id": {"rich_text": [{"text": {"content": task_id}}]},
                "name": {"title": [{"text": {"content": f"Task {task_id}"}}]},
                "status": {"select": {"name": "not_started"}},
                "created_by": {"rich_text": [{"text": {"content": created_by}}]},
                "created_at": {"date": {"start": _now()}},
                "message": {"rich_text": [{"text": {"content": msg}}]},
            }
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


async def _update_task_status(notion, ops: Dict[str, Any], status: str):
    """
    status ∈ {"not_started", "in_progress", "completed"}
    """

    task_id = ops["task_id"]

    # Notion の item を task_id で検索
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id,
            properties={
                "status": {"select": {"name": status}},
            }
        )
        print(f"[NotionOps] task_status_update {task_id} → {status}")

    except Exception as e:
        print("[NotionOps] update_task_status error:", repr(e))


# ------------------------------------------------------------
# Helper: Notion finder
# ------------------------------------------------------------

def _find_page_by_task_id(notion, task_id: str):
    """
    task_id プロパティで一致する Notion ページを検索する。
    """
    try:
        result = notion.databases.query(
            **{
                "database_id": NOTION_TASK_DB_ID,
                "filter": {
                    "property": "task_id",
                    "rich_text": {"equals": task_id},
                },
            }
        )
        results = result.get("results", [])
        return results[0] if results else None

    except Exception as e:
        print("[NotionOps] find_page_by_task_id error:", repr(e))
        return None


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()