# ovv/external_services/notion/ops/executor.py
# ============================================================
# NotionOps Executor v3.4
#
# 仕様:
#   - Stabilizer から受け取る ops は
#       • None
#       • dict
#       • list[dict]
#     のいずれも許容する。
#
#   - A案に基づくタスク状態同期
#       task_create → status=not_started
#       task_start  → status=in_progress
#       task_paused → status=paused
#       task_end    → status=completed
#
#   - Persist v3.0 (Stabilizer) が生成する duration ops も処理:
#       { "type": "update_task_duration", "task_id": "...", "duration_seconds": 1234 }
#
# Note:
#   Notion 側に存在しないプロパティは更新しない（存在前提の spec は禁止）。
# ============================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


# ------------------------------------------------------------
# utils
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ops(ops: Any) -> List[Dict[str, Any]]:
    if ops is None:
        return []
    if isinstance(ops, dict):
        return [ops]
    if isinstance(ops, list):
        return [op for op in ops if isinstance(op, dict)]
    print("[NotionOps] unexpected ops type:", type(ops))
    return []


# ------------------------------------------------------------
# Public entry
# ------------------------------------------------------------

async def execute_notion_ops(ops: Any, context_key: str, user_id: str):
    """
    Stabilizer → executor の唯一の入口。
    ops は list/dict/None のいずれでも良い。
    """

    ops_list = _normalize_ops(ops)
    if not ops_list:
        return

    notion = get_notion_client()
    if notion is None:
        print("[NotionOps] Notion disabled → skip")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] Task DB ID missing → skip")
        return

    for op in ops_list:
        try:
            _dispatch_single_op(notion, op)
        except Exception as e:
            print("[NotionOps] Fatal error:", repr(e))


# ------------------------------------------------------------
# Dispatcher
# ------------------------------------------------------------

def _dispatch_single_op(notion, op: Dict[str, Any]):
    # explicit op
    if "op" in op:
        kind = op["op"]

        if kind == "task_create":
            _op_task_create(notion, op)
        elif kind == "task_start":
            _op_task_status(notion, op, "in_progress")
        elif kind == "task_paused":
            _op_task_status(notion, op, "paused")
        elif kind == "task_end":
            _op_task_status(notion, op, "completed")
        else:
            print(f"[NotionOps] Unknown op={kind}")
        return

    # implicit op (Stabilizer duration)
    if op.get("type") == "update_task_duration":
        _op_task_duration(notion, op)
        return

    print("[NotionOps] Unrecognized op payload:", op)


# ------------------------------------------------------------
# OP: task_create
# ------------------------------------------------------------

def _op_task_create(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    created_by = op.get("created_by", "")
    msg = op.get("core_message", "")

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


# ------------------------------------------------------------
# OP: update status (start / paused / end)
# ------------------------------------------------------------

def _op_task_status(notion, op: Dict[str, Any], status: str):
    task_id = op["task_id"]
    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task {task_id}")
        return

    props = {
        "status": {"select": {"name": status}},
    }

    # Optional: timestamp update only if Notion DB has such property
    timestamp_prop = f"{status}_at"
    existing_props = page["properties"].keys()

    if timestamp_prop in existing_props:
        props[timestamp_prop] = {"date": {"start": _now_iso()}}

    try:
        notion.pages.update(
            page_id=page["id"],
            properties=props,
        )
        print(f"[NotionOps] status → {status}")

    except Exception as e:
        print("[NotionOps] update error:", repr(e))


# ------------------------------------------------------------
# OP: duration update （Persist v3.0 → Notion）
# ------------------------------------------------------------

def _op_task_duration(notion, op: Dict[str, Any]):
    task_id = op["task_id"]
    duration = op.get("duration_seconds")
    if duration is None:
        return

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task {task_id}")
        return

    # Notion 側に duration_time が存在する場合だけ更新する
    if "duration_time" not in page["properties"]:
        print("[NotionOps] duration_time property missing → skip")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "duration_time": {"number": duration},
            },
        )
        print(f"[NotionOps] duration update → {duration}")

    except Exception as e:
        print("[NotionOps] duration update error:", repr(e))


# ------------------------------------------------------------
# Helper
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
        items = result.get("results", [])
        return items[0] if items else None

    except Exception as e:
        print("[NotionOps] find error:", repr(e))
        return None