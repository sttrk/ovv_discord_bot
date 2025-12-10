"""
NotionOps Executor
Notion DB に書き込みを行う唯一の層。
"""

from typing import Dict, Any, Optional
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

async def execute_notion_ops(ops: Dict[str, Any], context_key: str, user_id: str):
    """
    NotionOps(dict) を Notion API に適用する。
    Core / BIS からはここだけ呼ばれる。

    注意:
      notion_client は同期 API のため、この関数は async だが
      内部処理は同期的に実行される。
    """

    if not ops:
        return

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion client is None → Skipped.")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] NOTION_TASK_DB_ID missing → Skip all task ops.")
        return

    op = ops.get("op")

    try:
        if op == "task_create":
            _create_task_item(notion, ops)

        elif op == "task_start":
            _update_task_on_start(notion, ops)

        elif op == "task_end":
            _update_task_on_end(notion, ops)

        else:
            print(f"[NotionOps] Unknown op: {op}")

    except Exception as e:
        print("[NotionOps] execute_notion_ops error:", repr(e))


# ------------------------------------------------------------
# Task DB operations
#   プロパティ名はあなたの Notion DB に合わせている:
#     - task_id        (rich_text)
#     - name           (title)
#     - statsu         (select)
#     - created_by     (rich_text)
#     - created_at     (date)
#     - started_at     (date)
#     - ended_at       (date)
#     - duration_time  (number or formula) ※ここでは未操作
# ------------------------------------------------------------

def _create_task_item(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    created_by = ops.get("created_by") or ""
    msg = ops.get("core_message", "")

    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties={
                "task_id": {
                    "rich_text": [{"text": {"content": task_id}}]
                },
                "name": {
                    "title": [{"text": {"content": f"Task {task_id}"}}]
                },
                # あなたの DB では "status" ではなく "statsu"
                "statsu": {
                    "select": {"name": "not_started"}
                },
                "created_by": {
                    "rich_text": [{"text": {"content": created_by}}]
                },
                "created_at": {
                    "date": {"start": _now_iso()}
                },
                # 任意のメッセージログ
                "message": {
                    "rich_text": [{"text": {"content": msg}}]
                },
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


def _update_task_on_start(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "statsu": {"select": {"name": "in_progress"}},
                "started_at": {"date": {"start": _now_iso()}},
                # end/duration はここではリセットしない（DB設計次第）
            },
        )
        print(f"[NotionOps] task_start {task_id}")

    except Exception as e:
        print("[NotionOps] _update_task_on_start error:", repr(e))


def _update_task_on_end(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "statsu": {"select": {"name": "completed"}},
                "ended_at": {"date": {"start": _now_iso()}},
                # duration_time は Notion 側で formula に任せる前提
            },
        )
        print(f"[NotionOps] task_end {task_id}")

    except Exception as e:
        print("[NotionOps] _update_task_on_end error:", repr(e))


# ------------------------------------------------------------
# Helper: Notion finder
# ------------------------------------------------------------

def _find_page_by_task_id(notion, task_id: str) -> Optional[Dict[str, Any]]:
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