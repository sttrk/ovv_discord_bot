"""
NotionOps Executor v3.3 (status 正式版)
Persist v3.0（duration_seconds）と Notion Task DB を完全同期する。
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

async def execute_notion_ops(ops: Any, context_key: str, user_id: str):
    """
    Stabilizer v3.3 から渡される ops は以下いずれか:
      • None
      • dict
      • list[dict]

    すべて list[dict] に正規化して順番に処理する。
    """

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion client is None → Skipped.")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] NOTION_TASK_DB_ID missing → Skip all task ops.")
        return

    ops_list = _normalize_ops(ops)

    for op in ops_list:
        try:
            t = op.get("op") or op.get("type")

            if t == "task_create":
                _create_task_item(notion, op)

            elif t == "task_start":
                _update_task_on_start(notion, op)

            elif t == "task_end":
                _update_task_on_end(notion, op)

            elif t == "update_task_duration":
                _update_task_duration(notion, op)

            else:
                print(f"[NotionOps] Unknown op: {t}")

        except Exception as e:
            print("[NotionOps] executor error:", repr(e))


# ------------------------------------------------------------
# Normalizer
# ------------------------------------------------------------

def _normalize_ops(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    print("[NotionOps] unexpected ops type:", type(raw))
    return []


# ------------------------------------------------------------
# Task DB operations
# ------------------------------------------------------------

def _create_task_item(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    created_by = ops.get("created_by") or ""
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
                "duration_time": {"number": 0},
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


def _update_task_on_start(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found → {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "status": {"select": {"name": "in_progress"}},
                "started_at": {"date": {"start": _now_iso()}},
            },
        )
        print(f"[NotionOps] task_start {task_id}")

    except Exception as e:
        print("[NotionOps] update_start error:", repr(e))


def _update_task_on_end(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found → {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "status": {"select": {"name": "completed"}},
                "ended_at": {"date": {"start": _now_iso()}},
            },
        )
        print(f"[NotionOps] task_end {task_id}")

    except Exception as e:
        print("[NotionOps] update_end error:", repr(e))


def _update_task_duration(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    duration = ops.get("duration_seconds")

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found → {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "duration_time": {"number": duration},
            },
        )
        print(f"[NotionOps] duration_sync {task_id} = {duration}")

    except Exception as e:
        print("[NotionOps] update_duration error:", repr(e))


# ------------------------------------------------------------
# Finder
# ------------------------------------------------------------

def _find_page_by_task_id(notion, task_id: str):
    try:
        result = notion.databases.query(
            database_id=NOTION_TASK_DB_ID,
            filter={
                "property": "task_id",
                "rich_text": {"equals": task_id},
            },
        )
        res = result.get("results", [])
        return res[0] if res else None
    except Exception as e:
        print("[NotionOps] find_page error:", repr(e))
        return None