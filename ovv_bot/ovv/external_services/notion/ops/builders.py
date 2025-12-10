# ovv/external_services/notion/ops/builders.py
"""
NotionOps Builder
Core の出力と Request(InputPacket) を基に NotionOps(dict) を組み立てる。

役割:
  - Core の mode を解釈して、Notion TaskDB に対する操作(op)を決定する。
  - Persist v3.0 の task_id と対応づける（= Discord thread_id ベース TEXT）。
  - NotionOps Executor に渡すための最小限のペイロードを構築する。

前提:
  - Core v2.x から返却される core_output は、少なくとも以下を含む:
      - mode : str
          "task_create" | "task_start" | "task_pause" | "task_end" | "free_chat" | ...
      - message_for_user : str （Discordに返すメッセージ本体）
  - Request(InputPacket) は、少なくとも以下の属性を持つ:
      - task_id  : str | None
      - user_meta: dict (user_name / user_id などを含み得る)

制約:
  - builders は DB や Notion API を直接叩かない。
  - NotionOps の構造を決めるだけで、新しいルールや制約を捏造しない。
"""

from typing import Any, Dict, Optional


def build_notion_ops(core_output: Dict[str, Any], request: Any) -> Optional[Dict[str, Any]]:
    """
    Core の結果から Notion DB に反映する内容を組み立てる。
    Persist v3.0 以降、task_id は request.task_id と一致する。

    Parameters
    ----------
    core_output : dict
        Core v2.x の生出力。
        期待キー:
          - mode : str
          - message_for_user : str

    request : Any
        Boundary_Gate / Interface_Box から Core に渡された InputPacket。
        少なくとも:
          - task_id   属性（str | None）
          - user_meta 属性（dict | None）

    Returns
    -------
    None
        Notion に対する操作が不要な場合（free_chat 等）。

    dict
        NotionOps Executor にそのまま渡す命令。
        形式例:
          {
              "op": "task_start",         # or "task_create" / "task_pause" / "task_end"
              "task_id": "<thread_id>",
              "created_by": "<user_name or id>",
              "core_message": "<Discord向けメッセージ>",
          }
    """

    if not isinstance(core_output, dict):
        return None

    # --- 1. Core の mode を解釈 ---
    command_type = core_output.get("mode")
    if command_type not in ("task_create", "task_start", "task_pause", "task_end"):
        # free_chat / debug などは NotionOps 不要
        return None

    # --- 2. InputPacket から task_id / user 情報を抽出 ---
    task_id = getattr(request, "task_id", None)
    user_meta = getattr(request, "user_meta", {}) or {}

    # created_by は user_name を優先し、なければ user_id
    created_by = user_meta.get("user_name") or user_meta.get("user_id") or ""

    if not task_id:
        # task_id が無い場合、Notion TaskDB と紐付けられないため何もしない
        return None

    # --- 3. Core の message_for_user も Notion 側にログとして持たせる ---
    msg = core_output.get("message_for_user", "") or ""

    # --- 4. NotionOps ペイロードを構築 ---
    return {
        "op": command_type,        # "task_create" / "task_start" / "task_pause" / "task_end"
        "task_id": str(task_id),   # TEXT として扱う
        "created_by": created_by,
        "core_message": msg,
    }