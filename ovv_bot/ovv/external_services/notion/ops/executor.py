# ovv/external_services/notion/ops/executor.py
"""
NotionOps Executor — paused / end / start 完全対応
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Public entry
# ============================================================

async def execute_notion_ops(ops: Dict[str, Any], context_key: str, user_id: str):
    if not ops:
        return

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion disabled → skip")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] Task DB ID missing")
        return

    op = ops.get("op")

    try:
        if op == "task_create":
            _create_task_item(notion, ops)

        elif op == "task_start":
            _update_task_status(notion, ops, "in_progress")

        elif op == "task_paused":
            _update_task_status(notion, ops, "paused")

        elif op == "task_end":
            _update_task_status(notion, ops, "completed")

        else:
            print(f"[NotionOps] Unknown op: {op}")

    except Exception as e:
        print("[NotionOps] Fatal error:", repr(e))


# ============================================================
# Task DB operations
# ============================================================

def _create_task_item(notion, ops):
    task_id = ops["task_id"]
    created_by = ops.get("created_by", "")
    msg = ops.get("core_message", "")

    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "task_id": {"rich_text": [{"text": {"content": task_id}}]},
                "name": {"title": [{"text": {"content": f"Task {task_id}"}}]},
                "status": {"select": {"name": "not_started"}},
                "created_by": {"rich_text": [{"text": {"content": created_by}}]},
                "created_at": {"date": {"start": _now_iso()}},
                "message": {"rich_text": [{"text": {"content": msg}}]},
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create error:", repr(e))


def _update_task_status(notion, ops, status: str):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)

    if page is None:
        print(f"[NotionOps] No such task {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "status": {"select": {"name": status}},
                f"{status}_at": {"date": {"start": _now_iso()}},
            },
        )
        print(f"[NotionOps] status → {status}")

    except Exception as e:
        print("[NotionOps] update error:", repr(e))


def _find_page_by_task_id(notion, task_id: str):
    try:
        result = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "property": "task_id",
                "rich_text": {"equals": task_id},
            },
        )
        items = result.get("results", [])
        return items[0] if items else None

    except Exception as e:
        print("[NotionOps] find error:", repr(e))
        return None