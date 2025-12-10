# ovv/external_services/notion/ops/executor.py
"""
NotionOps Executor
Notion Task DB に書き込みを行う唯一の層。
"""

from typing import Dict, Any, Optional, Iterable
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_ops(ops: Any) -> Iterable[Dict[str, Any]]:
    """
    Stabilizer 側からは list[dict] も dict も来得るため、
    ここで正規化して順次処理する。
    """
    if ops is None:
        return []

    if isinstance(ops, dict):
        return [ops]

    if isinstance(ops, (list, tuple)):
        return [o for o in ops if isinstance(o, dict)]

    print("[NotionOps] Unexpected ops type:", type(ops))
    return []


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

async def execute_notion_ops(ops: Any, context_key: Optional[str], user_id: Optional[str]):
    """
    NotionOps を Notion API に適用する。
    Core / BIS からはここだけ呼ばれる。

    注意:
      notion_client は同期 API のため、この関数は async だが
      内部処理は同期的に実行される。
    """

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion client is None → Skipped.")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] NOTION_TASK_DB_ID missing → Skip all task ops.")
        return

    for op in _iter_ops(ops):
        kind = op.get("op")
        try:
            if kind == "task_create":
                _create_task_item(notion, op)

            elif kind == "task_start":
                _update_task_on_start(notion, op)

            elif kind == "task_paused":
                _update_task_on_paused(notion, op)

            elif kind == "task_end":
                _update_task_on_end(notion, op)

            else:
                print(f"[NotionOps] Unknown op: {kind}")

        except Exception as e:
            print("[NotionOps] execute_notion_ops error:", repr(e))


# ------------------------------------------------------------
# Task DB operations
#
# Notion Task DB プロパティ名（あなたの DB に合わせている）:
#   - task_id        (rich_text)
#   - name           (title)
#   - status         (select: not_started / in_progress / paused / completed)
#   - created_by     (rich_text)
#   - created_at     (date)
#   - started_at     (date)
#   - ended_at       (date)
#   - duration_time  (number or formula) ※ A案では直接は更新しない
# ------------------------------------------------------------

def _create_task_item(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    created_by = op.get("created_by") or ""
    msg = op.get("core_message", "")

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
                "status": {
                    "select": {"name": "not_started"}
                },
                "created_by": {
                    "rich_text": [{"text": {"content": created_by}}]
                },
                "created_at": {
                    "date": {"start": _now_iso()}
                },
                "message": {
                    "rich_text": [{"text": {"content": msg}}]
                },
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


def _update_task_on_start(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "status": {"select": {"name": "in_progress"}},
                "started_at": {"date": {"start": _now_iso()}},
            },
        )
        print(f"[NotionOps] task_start {task_id}")

    except Exception as e:
        print("[NotionOps] _update_task_on_start error:", repr(e))


def _update_task_on_paused(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "status": {"select": {"name": "paused"}},
                # started_at / ended_at はここでは触らない
            },
        )
        print(f"[NotionOps] task_paused {task_id}")

    except Exception as e:
        print("[NotionOps] _update_task_on_paused error:", repr(e))


def _update_task_on_end(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] Task not found in DB → {task_id}")
        return

    page_id = page["id"]

    try:
        notion.pages.update(
            page_id=page_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "ended_at": {"date": {"start": _now_iso()}},
                # duration_time は Notion 側 formula に任せる前提（A案）
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