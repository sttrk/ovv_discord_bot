# ovv/external_services/notion/ops/executor.py
"""
NotionOps Executor — TaskDB (name/title, duration=number) 完全対応版
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
    """
    NotionOps(dict) を Notion DB に適用する唯一のエントリ。
    """

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
            _update_task_status(notion, ops, status="in_progress")

        elif op == "task_paused":
            _update_task_status(notion, ops, status="paused")

        elif op == "task_end":
            _update_task_status(notion, ops, status="completed")

        elif op == "update_task_duration":
            _update_task_duration(notion, ops)

        else:
            print(f"[NotionOps] Unknown op: {op}")

    except Exception as e:
        print("[NotionOps] Fatal error:", repr(e))


# ============================================================
# Task Create
# ============================================================

def _create_task_item(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    created_by = ops.get("created_by", "")
    task_name = ops.get("task_name", f"Task {task_id}")

    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": task_name}}]},
                "task_id": {"rich_text": [{"text": {"content": task_id}}]},
                "status": {"select": {"name": "not_started"}},
                "created_by": {"rich_text": [{"text": {"content": created_by}}]},
                "created_at": {"date": {"start": _now_iso()}},
                "started_at": {"date": None},
                "ended_at": {"date": None},
                "duration": {"number": 0},
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


# ============================================================
# Status 更新
# ============================================================

def _update_task_status(notion, ops: Dict[str, Any], status: str):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)

    if page is None:
        print(f"[NotionOps] No such task {task_id}")
        return

    timestamp_prop = {
        "in_progress": "started_at",
        "paused": "paused_at",      # paused_at は DB 上にないので更新しない
        "completed": "ended_at",
        "not_started": None,
    }.get(status)

    properties = {
        "status": {"select": {"name": status}},
    }

    if timestamp_prop == "started_at":
        properties["started_at"] = {"date": {"start": _now_iso()}}

    elif timestamp_prop == "ended_at":
        properties["ended_at"] = {"date": {"start": _now_iso()}}

    try:
        notion.pages.update(
            page_id=page["id"],
            properties=properties,
        )
        print(f"[NotionOps] status → {status}")

    except Exception as e:
        print("[NotionOps] update_status error:", repr(e))


# ============================================================
# Duration 更新（task_end → Stabilizer が ops 追加）
# ============================================================

def _update_task_duration(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    duration_seconds = ops["duration_seconds"]

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task for duration {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "duration": {"number": duration_seconds}
            },
        )
        print(f"[NotionOps] duration update {task_id} = {duration_seconds}")

    except Exception as e:
        print("[NotionOps] duration update error:", repr(e))


# ============================================================
# Helper
# ============================================================

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