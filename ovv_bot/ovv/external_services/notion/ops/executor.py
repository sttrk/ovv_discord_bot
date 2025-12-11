# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Executor v3.7
#
# ROLE:
#   - BIS / Stabilizer から受け取った NotionOps(dict) を
#     Notion TaskDB に適用する唯一のレイヤ。
#
# RESPONSIBILITY TAGS:
#   [EXT_WRITE]  Notion API 書き込みの唯一の実行点
#   [EXT_TRACE]  NotionOps の入力・出力・エラーを完全に記録
#   [FUTURE]     DBスキーマ変更に備えた property-safe update
#
# CONSTRAINTS:
#   - Core / Persist / BIS と逆参照しない
#   - 入力された ops を改変しない（Stabilizer の責務）
# ============================================================

from typing import Dict, Any, Optional
from datetime import datetime, timezone
import traceback

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Public Entry
# ============================================================

async def execute_notion_ops(ops: Dict[str, Any], context_key: str, user_id: str):
    """
    [EXT_WRITE] NotionOps を Notion TaskDB に適用する唯一の入口。
    """

    if not ops:
        print("[NotionOps] skip: ops is empty")
        return

    # ---- Debug Trace ----
    print("==== NotionOps EXEC TRACE ====")
    print("context_key:", context_key)
    print("user_id:", user_id)
    print("ops:", ops)
    print("==============================")

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] disabled → client is None")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] Task DB ID missing → skip")
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
        print("[NotionOps] EXEC ERROR:", repr(e))
        traceback.print_exc()


# ============================================================
# Create
# ============================================================

def _create_task_item(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    created_by = ops.get("created_by", "")
    task_name = ops.get("task_name", f"Task {task_id}")

    props = {
        "name": {"title": [{"text": {"content": task_name}}]},
        "task_id": {"rich_text": [{"text": {"content": task_id}}]},
        "status": {"select": {"name": "not_started"}},
        "created_by": {"rich_text": [{"text": {"content": created_by}}]},
        "created_at": {"date": {"start": _now_iso()}},
        "started_at": {"date": None},
        "ended_at": {"date": None},
        "duration": {"number": 0},
    }

    print("[NotionOps] CREATE props:", props)

    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASK_DB_ID},
            properties=props,
        )
        print(f"[NotionOps] task_create OK: {task_id} → {res.get('id')}")

    except Exception as e:
        print("[NotionOps] create_task_item ERROR:", repr(e))
        traceback.print_exc()


# ============================================================
# Status Update
# ============================================================

def _update_task_status(notion, ops: Dict[str, Any], status: str):
    task_id = ops["task_id"]
    page = _find_page_by_task_id(notion, task_id)

    if page is None:
        print(f"[NotionOps] status update skip: task not found → {task_id}")
        return

    # DBに存在しないプロパティは書かない
    timestamp_prop = {
        "in_progress": "started_at",
        "completed":  "ended_at",
        "paused":     None,       # paused_at は DB に存在しない
        "not_started": None,
    }.get(status)

    props = {
        "status": {"select": {"name": status}},
    }

    if timestamp_prop == "started_at":
        props["started_at"] = {"date": {"start": _now_iso()}}

    elif timestamp_prop == "ended_at":
        props["ended_at"] = {"date": {"start": _now_iso()}}

    else:
        # paused → DB に timestamp が無いので更新しない
        print("[NotionOps] paused: timestamp not updated (property missing)")

    print(f"[NotionOps] UPDATE_STATUS props ({task_id}) →", props)

    try:
        notion.pages.update(page_id=page["id"], properties=props)
        print(f"[NotionOps] status update OK → {status}")

    except Exception as e:
        print("[NotionOps] update_status ERROR:", repr(e))
        traceback.print_exc()


# ============================================================
# Duration Update
# ============================================================

def _update_task_duration(notion, ops: Dict[str, Any]):
    task_id = ops["task_id"]
    duration_seconds = ops["duration_seconds"]

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] duration update skip: no such task → {task_id}")
        return

    props = {"duration": {"number": duration_seconds}}
    print(f"[NotionOps] UPDATE_DURATION props ({task_id}) →", props)

    try:
        notion.pages.update(page_id=page["id"], properties=props)
        print(f"[NotionOps] duration update OK → {duration_seconds}")

    except Exception as e:
        print("[NotionOps] duration update ERROR:", repr(e))
        traceback.print_exc()


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
        print(f"[NotionOps] FIND ({task_id}) → {len(items)} hit(s)")
        return items[0] if items else None

    except Exception as e:
        print("[NotionOps] find ERROR:", repr(e))
        traceback.print_exc()
        return None