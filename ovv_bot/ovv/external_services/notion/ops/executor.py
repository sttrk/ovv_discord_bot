# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: NotionOps Executor v3.1
#
# ROLE:
#   - "builders.py" が構築した NotionOps を実際に Notion API へ反映する。
#   - TaskDB のタスクページを作成・更新する。
#
# INPUT:
#   notion_ops: {
#       "kind": "task_db",
#       "task_id": str,
#       "database_id": str,
#       "command_type": "task_create" | "task_start" | "task_end",
#       "ops": [...],
#       "user": {...},
#       "meta": {...}
#   }
#
# OUTPUT:
#   None（成功 / 失敗はログ出力で通知）
#
# CONSTRAINT:
#   - 1 notion_ops 内に複数アクションがある場合でも逐次処理する。
#   - エラーは握りつぶさずログを出すが、Ovv 本体の動作は止めない。
# ============================================================

from __future__ import annotations
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone

import os
from openai import OpenAI

# ------------------------------------------------------------
# Notion 用 HTTP API (v1)
# ------------------------------------------------------------
import requests

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_API_BASE = "https://api.notion.com/v1/"
NOTION_API_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_API_VERSION,
}


# ============================================================
# Utility
# ============================================================

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _notion_search_page_by_task_id(database_id: str, task_id: str) -> Optional[str]:
    """
    TaskDB 内で Task ID が一致するページを検索する。
    見つかったら page_id を返す。無ければ None。
    """
    url = f"{NOTION_API_BASE}databases/{database_id}/query"
    body = {
        "filter": {
            "property": "Task ID",
            "rich_text": {"equals": task_id},
        }
    }

    try:
        res = requests.post(url, json=body, headers=HEADERS, timeout=10)
        data = res.json()
        results = data.get("results", [])
        if not results:
            return None
        return results[0]["id"]
    except Exception as e:
        print("[NotionOps] search error:", repr(e))
        return None


def _notion_create_task_page(database_id: str, task_id: str, title: str, user_info: Dict[str, str]):
    """
    Task page を新規作成する。
    """
    url = f"{NOTION_API_BASE}pages"
    body = {
        "parent": {"database_id": database_id},
        "properties": {
            "Task ID": {"rich_text": [{"text": {"content": task_id}}]},
            "name": {"title": [{"text": {"content": title}}]},
            "status": {"select": {"name": "not_started"}},
            "created_by": {"rich_text": [{"text": {"content": user_info.get('id', '')}}]},
            "created_at": {"date": {"start": _now_iso()}},
            "started_at": {"date": None},
            "ended_at": {"date": None},
            "duration_time": {"number": None},
        }
    }

    try:
        res = requests.post(url, json=body, headers=HEADERS, timeout=10)
        if res.status_code >= 300:
            print("[NotionOps] create page error:", res.text)
        return res.json()
    except Exception as e:
        print("[NotionOps] create page exception:", repr(e))


def _notion_update_task_properties(page_id: str, props: Dict[str, Any]):
    url = f"{NOTION_API_BASE}pages/{page_id}"
    body = {"properties": props}

    try:
        res = requests.patch(url, json=body, headers=HEADERS, timeout=10)
        if res.status_code >= 300:
            print("[NotionOps] update error:", res.text)
        return res.json()
    except Exception as e:
        print("[NotionOps] update exception:", repr(e))


# ============================================================
# Action handlers
# ============================================================

def _handle_ensure_task_page(notion_ops: Dict[str, Any], op: Dict[str, Any]):
    database_id = notion_ops["database_id"]
    task_id = op["task_id"]
    title = op.get("title", "New Task")
    user_info = notion_ops.get("user", {})

    # 1. 該当タスクがあるか検索
    page_id = _notion_search_page_by_task_id(database_id, task_id)

    if page_id:
        # 存在する → title / status だけ最低限更新
        props = {
            "name": {"title": [{"text": {"content": title}}]},
            "status": {"select": {"name": op.get("initial_status", "not_started")}},
        }
        _notion_update_task_properties(page_id, props)
        return

    # 2. 無い → 新規作成
    _notion_create_task_page(database_id, task_id, title, user_info)


def _handle_mark_session_start(notion_ops: Dict[str, Any], op: Dict[str, Any]):
    database_id = notion_ops["database_id"]
    task_id = op["task_id"]

    page_id = _notion_search_page_by_task_id(database_id, task_id)
    if not page_id:
        print("[NotionOps] session_start: page not found; creating one.")
        _notion_create_task_page(database_id, task_id, f"Task {task_id}", notion_ops.get("user", {}))
        page_id = _notion_search_page_by_task_id(database_id, task_id)

    props = {
        "started_at": {"date": {"start": _now_iso()}},
        "status": {"select": {"name": "active"}},
    }
    _notion_update_task_properties(page_id, props)


def _handle_mark_session_end(notion_ops: Dict[str, Any], op: Dict[str, Any]):
    database_id = notion_ops["database_id"]
    task_id = op["task_id"]

    page_id = _notion_search_page_by_task_id(database_id, task_id)
    if not page_id:
        print("[NotionOps] session_end: page not found; creating one.")
        _notion_create_task_page(database_id, task_id, f"Task {task_id}", notion_ops.get("user", {}))
        page_id = _notion_search_page_by_task_id(database_id, task_id)

    # duration は PG の Persist を正とするため、Notion では ended_at のみ更新
    props = {
        "ended_at": {"date": {"start": _now_iso()}},
        "status": {"select": {"name": "paused"}},
    }
    _notion_update_task_properties(page_id, props)


# ============================================================
# Public API
# ============================================================

async def execute_notion_ops(notion_ops: Optional[Dict[str, Any]], **kwargs):
    """
    BIS / Stabilizer から呼び出される唯一のエントリポイント。
    ノンブロッキング保証は不要。内部は同期 HTTP。
    """

    if not notion_ops:
        return

    kind = notion_ops.get("kind")
    if kind != "task_db":
        print("[NotionOps] Unsupported ops kind:", kind)
        return

    ops: List[Dict[str, Any]] = notion_ops.get("ops") or []

    for op in ops:
        action = op.get("action")

        if action == "ensure_task_page":
            _handle_ensure_task_page(notion_ops, op)

        elif action == "mark_session_start":
            _handle_mark_session_start(notion_ops, op)

        elif action == "mark_session_end":
            _handle_mark_session_end(notion_ops, op)

        else:
            print("[NotionOps] Unknown action:", action)