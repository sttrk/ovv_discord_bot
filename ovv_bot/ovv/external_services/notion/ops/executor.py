# external_services/notion/ops/executor.py
# ---------------------------------------------------------------------
# NotionOps Executor
#  - Notion に対する全ての書き込み処理はここからのみ実行される
#  - A5-Minimal Critical 修正の中核
#  - InterfaceBox から渡される notion_ops を実行し、ログし、安全に失敗を扱う
# ---------------------------------------------------------------------

import asyncio
from external_services.notion.client import NotionClient
from external_services.notion.schemas import load_schema
from audit.audit_logger import write_audit_log
from utils.error_utils import (
    NotionSchemaError,
    NotionAPIError,
    NotionOpsValidationError,
)


class NotionOpResult:
    def __init__(self, action, ok, error_type=None, error_message=None):
        self.action = action
        self.ok = ok
        self.error_type = error_type
        self.error_message = error_message


class NotionOpsExecutionResult:
    def __init__(self, *, success, results, error_summary=None):
        self.success = success
        self.results = results
        self.error_summary = error_summary


# ---------------------------------------------------------
# 事前バリデーション
# ---------------------------------------------------------
def validate_notion_ops(notion_ops: dict):
    if not isinstance(notion_ops, dict):
        raise NotionOpsValidationError("notion_ops must be a dict")

    if "ops" not in notion_ops:
        raise NotionOpsValidationError("notion_ops missing 'ops' key")

    if not isinstance(notion_ops["ops"], list):
        raise NotionOpsValidationError("'ops' must be a list")

    return True


# ---------------------------------------------------------
# 個別 Notion 操作の実行
# ---------------------------------------------------------
async def _execute_single_op(client: NotionClient, op: dict):
    action = op.get("action")
    target = op.get("target", {})
    params = op.get("params", {})

    # action が未定義なら構造エラー
    if not action:
        raise NotionOpsValidationError("action is missing in op")

    # action ごとに分岐（用途拡張可能）
    if action == "create_task":
        return await client.create_task(params)

    if action == "update_task_status":
        task_id = target.get("task_id")
        return await client.update_task_status(task_id, params)

    if action == "update_title":
        task_id = target.get("task_id")
        return await client.update_title(task_id, params)

    if action == "append_comment":
        task_id = target.get("task_id")
        return await client.append_comment(task_id, params)

    # 将来：WBS / KB 向け
    if action == "create_wbs_item":
        return await client.create_wbs_item(params)

    if action == "update_kb_entry":
        return await client.update_kb_entry(target.get("kb_id"), params)

    raise NotionOpsValidationError(f"Unsupported action: {action}")


# ---------------------------------------------------------
# メイン：NotionOps 実行
# ---------------------------------------------------------
async def execute_notion_ops(
    notion_ops: dict,
    *,
    context_key: str,
    user_id: str | None,
    request_id: str | None
) -> NotionOpsExecutionResult:

    # 1. バリデーション
    try:
        validate_notion_ops(notion_ops)
    except NotionOpsValidationError as e:
        write_audit_log(
            event_type="notion_ops_validation_error",
            context_key=context_key,
            user_id=user_id,
            request_id=request_id,
            details=str(e),
        )
        return NotionOpsExecutionResult(
            success=False,
            results=[],
            error_summary=str(e)
        )

    client = NotionClient()
    schema = load_schema()

    results: list[NotionOpResult] = []

    # -----------------------------------------------------
    # 2. 各 op を順次実行
    # -----------------------------------------------------
    for op in notion_ops["ops"]:
        action = op.get("action")

        try:
            # スキーマ検証（簡易）
            if action not in schema["allowed_actions"]:
                raise NotionSchemaError(f"Action '{action}' not in schema")

            await _execute_single_op(client, op)

            # 成功
            results.append(NotionOpResult(action=action, ok=True))

        # ----------------------------
        # 各種エラー分類
        # ----------------------------
        except NotionSchemaError as e:
            write_audit_log(
                event_type="notion_schema_error",
                context_key=context_key,
                user_id=user_id,
                request_id=request_id,
                details=str(e),
            )
            results.append(
                NotionOpResult(action, False, "schema_error", str(e))
            )

        except NotionAPIError as e:
            write_audit_log(
                event_type="notion_api_error",
                context_key=context_key,
                user_id=user_id,
                request_id=request_id,
                details=str(e),
            )
            results.append(
                NotionOpResult(action, False, "api_error", str(e))
            )

        except NotionOpsValidationError as e:
            write_audit_log(
                event_type="notion_ops_validation_error",
                context_key=context_key,
                user_id=user_id,
                request_id=request_id,
                details=str(e),
            )
            results.append(
                NotionOpResult(action, False, "validation_error", str(e))
            )

        except Exception as e:
            write_audit_log(
                event_type="notion_unexpected_error",
                context_key=context_key,
                user_id=user_id,
                request_id=request_id,
                details=str(e),
            )
            results.append(
                NotionOpResult(action, False, "unexpected_error", str(e))
            )

    # -----------------------------------------------------
    # 3. 成功 / 失敗 判定
    # -----------------------------------------------------
    failures = [r for r in results if not r.ok]

    if failures:
        summary = f"{len(failures)} ops failed"
        return NotionOpsExecutionResult(
            success=False,
            results=results,
            error_summary=summary
        )

    return NotionOpsExecutionResult(
        success=True,
        results=results,
        error_summary=None
    )
