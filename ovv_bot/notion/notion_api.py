# notion/notion_api.py
# Notion CRUD Wrapper - September Stable Edition

from datetime import datetime, timezone
from typing import List, Optional

from notion_client import Client

from config import (
    NOTION_API_KEY,
    NOTION_TASKS_DB_ID,
    NOTION_SESSIONS_DB_ID,
    NOTION_LOGS_DB_ID,
)
from database.pg import log_audit

# 共有 Notion クライアント
notion_client = Client(auth=NOTION_API_KEY)


# ============================================================
# Task / Session / Logs
# ============================================================

async def create_task(name: str, goal: str, thread_id: int, channel_id: int) -> Optional[str]:
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        page = notion_client.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(channel_id)}}]},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return page["id"]

    except Exception as e:
        log_audit("notion_error", {"op": "create_task", "error": repr(e)})
        return None


async def start_session(task_id: str, name: str, thread_id: int) -> Optional[str]:
    now = datetime.now(timezone.utc)
    try:
        page = notion_client.pages.create(
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
        log_audit("notion_error", {"op": "start_session", "error": repr(e)})
        return None


async def end_session(session_id: str, summary: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    try:
        notion_client.pages.update(
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
        log_audit("notion_error", {"op": "end_session", "error": repr(e)})
        return False


async def append_logs(session_id: str, logs: List[dict]) -> bool:
    try:
        for log in logs:
            notion_client.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {
                        "rich_text": [
                            {"text": {"content": log["content"][:2000]}}
                        ]
                    },
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {
                        "rich_text": [{"text": {"content": log["id"]}}]
                    },
                },
            )
        return True

    except Exception as e:
        log_audit("notion_error", {"op": "append_logs", "error": repr(e)})
        return False
