# notion/notion_api.py
import json
from datetime import datetime, timezone
from typing import Optional, List

from notion_client import Client
from config import (
    NOTION_API_KEY,
    NOTION_TASKS_DB_ID,
    NOTION_SESSIONS_DB_ID,
    NOTION_LOGS_DB_ID,
)

# Audit 用
from database.pg import log_audit

# Notion Client（モジュール単位で保持）
notion = Client(auth=NOTION_API_KEY)


# ============================================================
# 1. Create Task
# ============================================================

async def create_task(name: str, goal: str, thread_id: int, channel_id: int) -> Optional[str]:
    now = datetime.now(timezone.utc).isoformat()
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(channel_id)}}]},
                "created_at": {"date": {"start": now}},
                "updated_at": {"date": {"start": now}},
            },
        )
        return page["id"]

    except Exception as e:
        log_audit("notion_error", {
            "op": "create_task",
            "error": repr(e),
            "name": name,
            "goal": goal,
        })
        return None


# ============================================================
# 2. Start Session
# ============================================================

async def start_session(task_id: str, name: str, thread_id: int) -> Optional[str]:
    now = datetime.now(timezone.utc)
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "start_time": {"date": {"start": now.isoformat()}},
                "created_at": {"date": {"start": now.isoformat()}},
                "updated_at": {"date": {"start": now.isoformat()}},
            },
        )
        return page["id"]

    except Exception as e:
        log_audit("notion_error", {
            "op": "start_session",
            "task_id": task_id,
            "error": repr(e),
        })
        return None


# ============================================================
# 3. End Session
# ============================================================

async def end_session(session_id: str, summary: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()

    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": now}},
                "summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
                "updated_at": {"date": {"start": now}},
            },
        )
        return True

    except Exception as e:
        log_audit("notion_error", {
            "op": "end_session",
            "session_id": session_id,
            "error": repr(e),
        })
        return False


# ============================================================
# 4. Append Logs
# ============================================================

async def append_logs(session_id: str, logs: List[dict]) -> bool:
    try:
        for log in logs:
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {"rich_text": [{"text": {"content": log["id"]}}]},
                },
            )
        return True

    except Exception as e:
        log_audit("notion_error", {
            "op": "append_logs",
            "session_id": session_id,
            "count": len(logs),
            "error": repr(e),
        })
        return False


# ============================================================
# 5. Debug Helpers
# ============================================================

def notion_health() -> str:
    """デバッグ用：Notion API が生きているか軽くチェック"""
    try:
        users = notion.users.list()
        return f"OK ({len(users.get('results', []))} users)"
    except Exception as e:
        return f"FAIL ({repr(e)})"
