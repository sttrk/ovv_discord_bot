# ovv/external_services/notion/ops/builders.py
# ============================================================
# MODULE CONTRACT: NotionOps Builders v3.1 (Task-DB Oriented)
#
# ROLE:
#   - BIS / Interface_Box から Core 出力と Request(InputPacket) を受け取り、
#     Notion Executor が解釈しやすい "高レベル NotionOps" を構築する。
#
#   - 旧 Core が notion_ops を直接返してくる場合は、それを優先して
#     そのまま透過させる（後方互換）。
#
# INPUT:
#   - core_output: Any
#       Core 側の戻り値（dict 想定だが防御的に Any）
#
#   - request: Any
#       Boundary_Gate → Interface_Box で渡される InputPacket / packet 相当の dict
#       期待される主なキー:
#         - command_type: "task_create" / "task_start" / "task_end" / "free_chat"
#         - task_id: Discord thread/channel ID 相当（TEXT 主キー）
#         - user_meta: { "user_id": str, "user_name": str } など
#         - payload: { "text": str }  # 生テキスト
#
# OUTPUT:
#   - notion_ops: dict | None
#
#   NotionOps v3.1 の標準フォーマット:
#
#       {
#         "kind": "task_db",
#         "version": "3.1",
#         "database_id": "<Notion TaskDB ID>",
#         "command_type": "task_create" | "task_start" | "task_end",
#         "task_id": "<Discord thread_id 等>",
#         "user": {
#             "id": "<discord user id>",
#             "name": "<discord user name>",
#         },
#         "ops": [
#            {
#               "action": "ensure_task_page",   # task_create
#               "title": "<task title>",
#               "initial_status": "not_started"
#            },
#            {
#               "action": "mark_session_start"  # task_start
#            },
#            {
#               "action": "mark_session_end"    # task_end
#            }
#         ],
#         "meta": {
#             "source": "ovv",
#             "builder": "notion_ops_v3.1",
#         }
#       }
#
#   - executor 側は上記の "意味レベル" の ops から、
#     実際の Notion API の pages.create / pages.update を構成する。
#
# MUST NOT:
#   - Notion API を直接叩かない（それは executor の責務）
#   - PostgreSQL を参照しない（Persist は Stabilizer / PG の責務）
# ============================================================

from __future__ import annotations

from typing import Any, Dict, Optional, List
import os

# Notion TaskDB の database_id は、config または環境変数から取得する前提。
# - CONFIG_NOTION_TASK_DB_ID は UI 版 Ovv 互換の名称例。
# - Fallback として、本番用に合意された ID を直書きしておく。
try:
    from config import NOTION_TASK_DB_ID as CONFIG_NOTION_TASK_DB_ID  # type: ignore
except Exception:  # pragma: no cover - config が無い環境向けフォールバック
    CONFIG_NOTION_TASK_DB_ID = None  # type: ignore


# ============================================================
# Internal helpers
# ============================================================


def _get_task_db_id() -> str:
    """
    Notion TaskDB の database_id を取得する。

    優先順:
      1. config.NOTION_TASK_DB_ID
      2. 環境変数 NOTION_TASK_DB_ID
      3. ハードコードされたデフォルト（ユーザー固有）
    """
    if CONFIG_NOTION_TASK_DB_ID:
        return str(CONFIG_NOTION_TASK_DB_ID)

    env_val = os.getenv("NOTION_TASK_DB_ID")
    if env_val:
        return env_val

    # ユーザーから共有された TaskDB ID をデフォルトとしてハードコード
    # ※ 必要ならここを書き換えてもよい。
    return "2b744d1502e880109f60e07fc922b2be"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_get(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def _extract_command_type(request: Any, core_output: Any) -> Optional[str]:
    """
    command_type を複数候補から抽出する。
    - request["command_type"]
    - core_output["core_mode"] / core_output["mode"] など
    """
    req = _as_dict(request)
    core = _as_dict(core_output)

    cmd = _safe_get(req, "command_type")
    if isinstance(cmd, str) and cmd:
        return cmd

    # 旧 Core 互換: core_mode / mode
    mode = _safe_get(core, "core_mode") or _safe_get(core, "mode")
    if isinstance(mode, str) and mode:
        return mode

    return None


def _extract_user_info(request: Any) -> Dict[str, str]:
    """
    InputPacket から user_id / user_name を抽出する。
    """
    req = _as_dict(request)
    user_meta = _as_dict(_safe_get(req, "user_meta", {}))

    user_id = str(
        user_meta.get("user_id")
        or _safe_get(req, "user_id")
        or ""
    )

    user_name = (
        user_meta.get("user_name")
        or _safe_get(req, "username")
        or ""
    )

    return {
        "id": user_id,
        "name": str(user_name),
    }


def _extract_task_id(request: Any, core_output: Any) -> Optional[str]:
    """
    task_id 候補を複数のキーから抽出する。
    - request["task_id"]
    - request["thread_id"] / request["channel_id"]
    - core_output["task_id"] など
    """
    req = _as_dict(request)
    core = _as_dict(core_output)

    for key in ("task_id", "thread_id", "channel_id"):
        val = _safe_get(req, key)
        if val:
            return str(val)

    core_tid = _safe_get(core, "task_id")
    if core_tid:
        return str(core_tid)

    return None


def _extract_payload_text(request: Any) -> str:
    """
    payload.text または raw_content / content から
    生のメッセージテキストを抽出する。
    """
    req = _as_dict(request)
    payload = _as_dict(_safe_get(req, "payload", {}))

    text = payload.get("text") or _safe_get(req, "raw_content") or _safe_get(req, "content")
    if not text:
        return ""
    return str(text)


def _extract_task_title_from_text(text: str) -> str:
    """
    "!Task xxx" のようなコマンドからタスク名を抽出する。
    仕様:
      - 先頭の "!Task" / "!task" / "！Task" / "！task" を取り除く
      - 残りを strip したものをタイトルとする
      - それでも空なら固定のプレースホルダ
    """
    if not text:
        return "New Task"

    raw = text.strip()
    prefixes = ["!Task", "!task", "！Task", "！task"]
    for p in prefixes:
        if raw.startswith(p):
            raw = raw[len(p):].strip()
            break

    if not raw:
        return "New Task"

    return raw


# ============================================================
# Public API
# ============================================================


def build_notion_ops(core_output: Any, request: Any) -> Optional[Dict[str, Any]]:
    """
    NotionOps v3.1 Builder

    優先順位:
      1. core_output 内に notion_ops があれば、それをそのまま返す（後方互換）。
      2. 無ければ command_type / task_id / user_info を元に
         Task-DB 用の高レベル NotionOps を構築する。
      3. 対象外コマンド（free_chat など）は None を返す。
    """

    # ----------------------------------------
    # 1) Core 互換: core_output 内に notion_ops がある場合
    # ----------------------------------------
    core_dict = _as_dict(core_output)
    legacy_notion_ops = core_dict.get("notion_ops")
    if legacy_notion_ops:
        # 旧設計の NotionOps は、そのまま Executor に渡す
        return legacy_notion_ops

    # ----------------------------------------
    # 2) v3.1 ビルドフロー
    # ----------------------------------------
    command_type = _extract_command_type(request, core_output)
    if command_type not in ("task_create", "task_start", "task_end"):
        # free_chat 等は NotionOps 不要
        return None

    task_id = _extract_task_id(request, core_output)
    if not task_id:
        # task_id が無ければ Task-DB に紐づけられない → NotionOps 生成しない
        return None

    user_info = _extract_user_info(request)
    text = _extract_payload_text(request)

    ops: List[Dict[str, Any]] = []

    if command_type == "task_create":
        # タスク作成: Notion 側に「タスクページが存在すること」を保証する。
        title = _extract_task_title_from_text(text)
        ops.append(
            {
                "action": "ensure_task_page",
                "task_id": task_id,
                "title": title,
                # status は Notion 側の options と合わせる必要があるため
                # とりあえず "not_started" を初期値として載せる。
                "initial_status": "not_started",
            }
        )

    elif command_type == "task_start":
        # タスク開始: セッション開始を Notion に反映させる。
        # 実際の started_at / duration 等は executor 側で決める。
        ops.append(
            {
                "action": "mark_session_start",
                "task_id": task_id,
            }
        )

    elif command_type == "task_end":
        # タスク終了: セッション終了を Notion に反映させる。
        # duration の正確な計算は Persist(PG) 側を正とし、
        # executor が後から sync することを想定。
        ops.append(
            {
                "action": "mark_session_end",
                "task_id": task_id,
            }
        )

    if not ops:
        return None

    notion_ops: Dict[str, Any] = {
        "kind": "task_db",
        "version": "3.1",
        "database_id": _get_task_db_id(),
        "command_type": command_type,
        "task_id": task_id,
        "user": user_info,
        "ops": ops,
        "meta": {
            "source": "ovv",
            "builder": "notion_ops_v3.1",
        },
    }

    return notion_ops