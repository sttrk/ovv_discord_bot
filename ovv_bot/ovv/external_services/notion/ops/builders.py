# external_services/notion/ops/builders.py
# ---------------------------------------------------------------------
# NotionOps Builders
#  - Core の出力と Request 情報から NotionOps(dict) を組み立てる。
#  - executor.py が実行できる形:
#       {"ops": [ { "action": ..., "target": {...}, "params": {...} }, ... ]}
#  - Core が notion_ops を明示的に返してくれた場合はそれを優先し、
#    何もなければ command_type に応じたシンプルなデフォルトを組み立てる。
# ---------------------------------------------------------------------

from typing import Any, Dict, Optional


def _ensure_ops_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Core から返ってくる可能性のある様々な形の notion_ops を、
    Executor が期待する canonical 形式 {"ops": [...]} に正規化する。
    不明な形式の場合は None を返し、上位が「opsなし」と判断できるようにする。
    """
    if raw is None:
        return None

    # すでに {"ops": [...]} 形式
    if isinstance(raw, dict) and "ops" in raw and isinstance(raw["ops"], list):
        return raw

    # 単一 op dict の場合 {"action": ..., ...}
    if isinstance(raw, dict) and "action" in raw:
        return {"ops": [raw]}

    # すでに list[op] の場合
    if isinstance(raw, list):
        return {"ops": raw}

    # よくあるラップパターン: {"notion": {"ops": [...]}}
    if isinstance(raw, dict) and "notion" in raw:
        inner = raw["notion"]
        if isinstance(inner, dict) and "ops" in inner and isinstance(inner["ops"], list):
            return inner

    return None


def _extract_core_defined_ops(core_output: Any) -> Optional[Dict[str, Any]]:
    """
    Core 側が明示的に NotionOps を返している場合にそれを取り出す。
    想定パターン:
      - core_output["notion_ops"]
      - core_output["notion"]["ops"]
    など。
    """
    if not isinstance(core_output, dict):
        return None

    # パターン1: 直接 notion_ops キーを持っている
    if "notion_ops" in core_output:
        normalized = _ensure_ops_dict(core_output["notion_ops"])
        if normalized:
            return normalized

    # パターン2: core_output["notion"] 内に ops を持っている
    notion_part = core_output.get("notion")
    if notion_part is not None:
        normalized = _ensure_ops_dict(notion_part)
        if normalized:
            return normalized

    return None


def _build_fallback_ops_for_task_create(request: Any) -> Optional[Dict[str, Any]]:
    """
    Core が notion_ops を返さなかった場合のフォールバック。
    最低限、!Task（task_create）の場合だけは title から create_task を組み立てる。
    それ以外（start/end など）は Core 側のサポートが前提なので、ここでは生成しない。
    """
    payload = getattr(request, "payload", {}) or {}
    title = payload.get("title")
    if not title:
        return None

    user_meta = getattr(request, "user_meta", {}) or {}
    created_by = user_meta.get("user_name") or user_meta.get("user_id")

    op = {
        "action": "create_task",
        "target": {},  # task_id は Notion 側で採番される前提
        "params": {
            "title": title,
        },
    }

    if created_by:
        op["params"]["created_by"] = created_by

    return {"ops": [op]}


def build_notion_ops(core_output: Any, request: Any) -> Optional[Dict[str, Any]]:
    """
    InterfaceBox から呼ばれるエントリポイント。

    優先順位:
      1. Core が notion_ops を明示的に返している場合 → それを正規化してそのまま使う
      2. そうでなければ、command_type に応じて簡易な fallback ops を組み立てる
      3. それもできなければ None を返し、Stabilizer→Executor では「何もしない」
    """
    # 1. Core 定義の ops があればそれを最優先
    core_defined = _extract_core_defined_ops(core_output)
    if core_defined:
        return core_defined

    # 2. Fallback: command_type に応じて最低限の ops を組み立てる
    command_type = getattr(request, "command_type", None)

    if command_type == "task_create":
        fallback = _build_fallback_ops_for_task_create(request)
        if fallback:
            return fallback

    # task_start / task_end などは、どの task を対象にするかの判断が Core 側のロジックに依存するため、
    # ここで安易に fallback を組み立てると誤更新リスクが高い。
    # そのため、Core 側で notion_ops を返していない場合は何もせず None を返す。
    #
    # 例:
    #   - Core が「直近の未完了タスク」を選んで status を変えたい
    #   - 特定の ID を指定して更新したい
    # などのルールは Core プロンプト側で制御し、その結果として notion_ops を返す前提とする。

    return None