# ovv/external_services/notion/ops/executor.py
# ============================================================
# MODULE CONTRACT: External / NotionOps Executor v2.1 (Summary + Duration)
#
# ROLE:
#   - BIS / Stabilizer から渡された NotionOps(list[dict]) を
#     Task DB（NOTION_TASK_DB_ID）に順序通り適用する。
#
# RESPONSIBILITY TAGS:
#   [EXEC_OPS]   NotionOps の逐次実行
#   [TASK_DB]    TaskDB(name/title, status, duration, summary) への反映
#   [GUARD]      Notion 無効時・設定不備時のガードとログ出力
#
# CONSTRAINTS:
#   - 呼び出し元は BIS / Stabilizer のみ（Core/BIS から直接呼ばない）
#   - ops は論理的に list[dict] とみなす（dict 単体は互換のため内部で list 化）
#   - 1 op 単位で例外を握りつぶし、残りの ops は継続実行。
# ============================================================

from __future__ import annotations

from typing import Dict, Any, Optional, List, Sequence, Union
from datetime import datetime, timezone

from ..notion_client import get_notion_client
from ..config_notion import NOTION_TASK_DB_ID


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Public entry
# ============================================================

async def execute_notion_ops(
    ops: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
    context_key: str,
    user_id: str,
) -> None:
    """
    NotionOps(list[dict]) を Notion DB に適用する唯一のエントリ。

    - 正式仕様としては list[dict] を前提とする。
    - 後方互換のため、dict 単体や tuple も許容し、内部で list 化する。
    """

    ops_list: List[Dict[str, Any]] = _normalize_ops(ops)
    if not ops_list:
        # 何もすることがない
        return

    notion = get_notion_client()
    if notion is None:
        print(f"[NotionOps] Notion disabled → skip (context_key={context_key})")
        return

    if NOTION_TASK_DB_ID is None:
        print("[NotionOps] Task DB ID missing → skip")
        return

    # ops を順番に実行
    for idx, op_dict in enumerate(ops_list):
        if not isinstance(op_dict, dict):
            print(f"[NotionOps] skip non-dict op at index {idx}: {op_dict!r}")
            continue

        op_name = op_dict.get("op")
        if not op_name:
            print(f"[NotionOps] skip invalid op at index {idx}: no 'op'")
            continue

        try:
            if op_name == "task_create":
                _create_task_item(notion, op_dict)

            elif op_name == "task_start":
                _update_task_status(notion, op_dict, status="in_progress")

            elif op_name == "task_paused":
                _update_task_status(notion, op_dict, status="paused")

            elif op_name == "task_end":
                _update_task_status(notion, op_dict, status="completed")

            elif op_name == "update_task_duration":
                _update_task_duration(notion, op_dict)

            elif op_name == "update_task_summary":
                _update_task_summary(notion, op_dict)

            else:
                print(f"[NotionOps] Unknown op: {op_name!r} (index={idx})")

        except Exception as e:
            # 1 op ごとに握りつぶし、残りの ops は継続実行
            print(
                "[NotionOps] Fatal error at index "
                f"{idx} (op={op_name!r}, task_id={op_dict.get('task_id')!r}): {e!r}"
            )


# ============================================================
# Normalization
# ============================================================

def _normalize_ops(
    raw: Union[None, Dict[str, Any], Sequence[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    呼び出し側から渡される ops を、内部表現 list[dict] に正規化する。
    """
    if raw is None:
        return []

    if isinstance(raw, dict):
        return [raw]

    if isinstance(raw, (list, tuple)):
        return [op for op in raw if isinstance(op, dict)]

    # 想定外の型は無視
    print(f"[NotionOps] unexpected ops type: {type(raw)!r}")
    return []


# ============================================================
# Task Create
# ============================================================

def _create_task_item(notion, ops: Dict[str, Any]) -> None:
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
                # summary は後続の update_task_summary で設定
            },
        )
        print(f"[NotionOps] task_create {task_id}")

    except Exception as e:
        print("[NotionOps] create_task_item error:", repr(e))


# ============================================================
# Status 更新
# ============================================================

def _update_task_status(notion, ops: Dict[str, Any], status: str) -> None:
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

    properties: Dict[str, Any] = {
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
        print(f"[NotionOps] status → {status} (task_id={task_id})")

    except Exception as e:
        print("[NotionOps] update_status error:", repr(e))


# ============================================================
# Duration 更新（task_end → Stabilizer が ops 追加）
# ============================================================

def _update_task_duration(notion, ops: Dict[str, Any]) -> None:
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
# Summary 更新（task_paused / task_end）
# ============================================================

def _update_task_summary(notion, ops: Dict[str, Any]) -> None:
    """
    Task のサマリテキストを更新する。

    前提：
      - Notion DB 側に "summary" (rich_text) プロパティが存在すること。
    """
    task_id = ops["task_id"]
    summary_text = ops.get("summary_text", "")

    if not summary_text:
        print(f"[NotionOps] empty summary_text for task {task_id} → skip")
        return

    page = _find_page_by_task_id(notion, task_id)
    if page is None:
        print(f"[NotionOps] No such task for summary {task_id}")
        return

    try:
        notion.pages.update(
            page_id=page["id"],
            properties={
                "summary": {
                    "rich_text": [
                        {"text": {"content": summary_text}}
                    ]
                }
            },
        )
        print(f"[NotionOps] summary update {task_id}")

    except Exception as e:
        print("[NotionOps] summary update error:", repr(e))


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