# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.8.2
#   (Debugging Subsystem v1.0 compliant: trace_id + checkpoints + failsafe)
#
# ROLE:
#   - Discord on_message → BIS パイプラインへの入口。
#   - Discord メッセージ → InputPacket 変換を一元管理。
#   - パイプライン開始前に InputPacket を capture（dbg_packet 用）
#   - 例外発生時は BG_FAILSAFE に集約し、Discord へ返す唯一の失敗出口となる。
#
# RESPONSIBILITY TAGS:
#   [ENTRY_BG]     Discord message を受けて入口処理
#   [CMD_ROUTE]    コマンド検出と正規化（ルーティングのみ）
#   [PACKETIZE]    InputPacket 構築
#   [CAPTURE]      dbg_packet 用 capture
#   [FAILSAFE]     失敗出口の一元化（No Silent Death）
#   [TRACE]        trace_id の生成と伝播（Single Trace Rule）
#
# CONSTRAINTS (HARD):
#   - Core / Persist / Notion / WBS(PG) には直接触れない。
#   - ovv.bis.interface_box.handle_request() のみを呼ぶ。
#   - Debug Command Suite は Gate-Assist（discord.py commands）側の責務。
#     Boundary_Gate は debug 入力を BIS に流さない（二重応答/境界汚染防止）。
#
# CHANGELOG:
#   - v3.8.1:
#       - 非コマンド文を drop せず "free_chat" として BIS に流す（UI版Ovv寄せ）
#       - "!" から始まる未知コマンドは "unknown_command" として Core に委譲
#   - v3.8.2:
#       - "!wbs+" を "wbs_show_full" にマップ（Stableを壊さず Volatile overview 表示の入口）
# ============================================================

from __future__ import annotations

from typing import Optional, Tuple, Any, Dict
import traceback
import json
import uuid
from datetime import datetime, timezone

from .types import InputPacket
from .interface_box import handle_request
from .capture_interface_packet import capture  # dbg_packet 用


# ------------------------------------------------------------
# Debug Flag
# ------------------------------------------------------------

DEBUG_BIS = True  # Render ログに内部スタックトレースを出す（構造ログは常に出す）


# ------------------------------------------------------------
# Debug Command Suite (Gate-Assist)
# ------------------------------------------------------------

_DEBUG_COMMAND_HEADS = {
    "bs", "!bs",
    "dbg_flow", "!dbg_flow",
    "dbg_packet", "!dbg_packet",
    "dbg_mem", "!dbg_mem",
    "dbg_all", "!dbg_all",
    "wipe", "!wipe",
    "help", "!help",
    "dbg_help", "!dbg_help",
}


# ------------------------------------------------------------
# Debugging Subsystem v1.0 — Checkpoints (FIXED)
# ------------------------------------------------------------

LAYER_BG = "BG"

CP_BG_ENTRY = "BG_ENTRY"
CP_BG_VALIDATE_INPUT = "BG_VALIDATE_INPUT"
CP_BG_BUILD_PACKET = "BG_BUILD_PACKET"
CP_BG_DISPATCH_CORE = "BG_DISPATCH_CORE"
CP_BG_FAILSAFE = "BG_FAILSAFE"


# ------------------------------------------------------------
# Logging (Structured JSON)
# ------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(
    *,
    trace_id: str,
    checkpoint: str,
    layer: str,
    level: str,
    summary: str,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "trace_id": trace_id,
        "checkpoint": checkpoint,
        "layer": layer,
        "level": level,
        "summary": summary,
        "timestamp": _now_iso(),
    }
    if error is not None:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False))


def _log_debug(*, trace_id: str, checkpoint: str, summary: str) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        layer=LAYER_BG,
        level="DEBUG",
        summary=summary,
    )


def _log_error(
    *,
    trace_id: str,
    checkpoint: str,
    summary: str,
    code: str,
    exc: Exception,
    at: str,
    retryable: bool = False,
) -> None:
    _log_event(
        trace_id=trace_id,
        checkpoint=checkpoint,
        layer=LAYER_BG,
        level="ERROR",
        summary=summary,
        error={
            "code": code,
            "type": type(exc).__name__,
            "message": str(exc),
            "at": at,
            "retryable": retryable,
        },
    )


# ------------------------------------------------------------
# Command 判定
# ------------------------------------------------------------

def _detect_command_type(raw: str) -> Optional[str]:
    """
    Discord の先頭トークンから command_type を決定する。
    Boundary_Gate は「検出と正規化」までが責務。

    HARD:
      - Debug Command Suite は Gate-Assist 側で処理されるため、ここでは対象外。
    """
    if not raw:
        return None

    head = raw.strip().split()[0].lower()

    # ---- Debug suite: DO NOT ROUTE TO BIS ----
    if head in _DEBUG_COMMAND_HEADS:
        return None

    mapping = {
        # Task / thread lifecycle
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",

        # ThreadWBS user-ack commands
        "!wy": "wbs_accept",
        "!wn": "wbs_reject",
        "!we": "wbs_edit",

        # ThreadWBS work_item lifecycle
        "!wd": "wbs_done",
        "!wx": "wbs_drop",

        # WBS show (stable)
        "!wbs": "wbs_show",
        "!w": "wbs_show",

        # WBS show (stable + volatile overview)
        "!wbs+": "wbs_show_full",

        # 旧互換
        "!task": "task_create",
        "!task_s": "task_start",
        "!task_start": "task_start",
        "!task_p": "task_paused",
        "!task_pause": "task_paused",
        "!task_e": "task_end",
        "!task_end": "task_end",
        "!task_c": "task_end",
        "!task_completed": "task_end",
    }

    return mapping.get(head)


def _strip_head_token(raw: str) -> str:
    """
    先頭トークン（コマンド）を除いた残りを content として返す。
    """
    if not raw:
        return ""
    parts = raw.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _looks_like_command(raw: str) -> bool:
    s = (raw or "").lstrip()
    return s.startswith("!")


# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------

def _safe_get_channel(message: Any) -> Any:
    return getattr(message, "channel", None)


def _extract_discord_context(message: Any) -> Tuple[str, str, Any]:
    """
    returns: (channel_id, thread_name, channel_obj)
    """
    channel = _safe_get_channel(message)
    channel_id = str(getattr(channel, "id", "") or "")
    thread_name = str(getattr(channel, "name", "") or "")
    return channel_id, thread_name, channel


def _extract_author_meta(message: Any) -> Tuple[str, str]:
    author = getattr(message, "author", None)
    author_id = str(getattr(author, "id", "") or "")
    user_name = (
        getattr(author, "display_name", None)
        or getattr(author, "name", None)
        or ""
    )
    return author_id, str(user_name)


def _build_input_packet_failsafe(
    *,
    trace_id: str,
    raw_content: str,
    command_type: str,
    content: str,
    author_id: str,
    channel_id: str,
    context_key: str,
    task_id: str,
    user_meta: Dict[str, Any],
    meta: Dict[str, Any],
) -> InputPacket:
    """
    InputPacket の schema 差分に耐える FAILSAFE builder。
    """
    kwargs: Dict[str, Any] = dict(
        raw=raw_content,
        source="discord",
        command=command_type,
        content=content,
        author_id=author_id,
        channel_id=channel_id,
        context_key=context_key,
        task_id=task_id,
        user_meta=user_meta,
        meta=dict(meta),
    )

    kwargs["trace_id"] = trace_id

    try:
        return InputPacket(**kwargs)  # type: ignore[arg-type]
    except TypeError:
        kwargs.pop("trace_id", None)
        kwargs["meta"]["trace_id"] = trace_id
        return InputPacket(**kwargs)  # type: ignore[arg-type]


def _bg_failsafe_message(trace_id: str, last_checkpoint: str) -> str:
    return (
        "[Boundary Error] internal failure in BIS pipeline.\n"
        f"- trace_id: {trace_id}\n"
        f"- last_checkpoint: {last_checkpoint}"
    )


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

async def handle_discord_input(message: Any) -> None:
    """
    bot.py → ONLY ENTRY.
    Discord → InputPacket → BIS Pipeline.
    """

    trace_id = str(uuid.uuid4())
    last_checkpoint = CP_BG_ENTRY
    _log_debug(trace_id=trace_id, checkpoint=CP_BG_ENTRY, summary="bg entry")

    try:
        # Bot自身の発言は無視
        if getattr(getattr(message, "author", None), "bot", False):
            last_checkpoint = CP_BG_VALIDATE_INPUT
            _log_debug(trace_id=trace_id, checkpoint=CP_BG_VALIDATE_INPUT, summary="drop bot message")
            return

        raw_content = (getattr(message, "content", "") or "").strip()

        last_checkpoint = CP_BG_VALIDATE_INPUT
        if not raw_content:
            _log_debug(trace_id=trace_id, checkpoint=CP_BG_VALIDATE_INPUT, summary="drop empty message")
            return

        # Debug suite は Gate-Assist 側で処理する前提なので、BIS では扱わない
        head = raw_content.split()[0].lower()
        if head in _DEBUG_COMMAND_HEADS:
            _log_debug(trace_id=trace_id, checkpoint=CP_BG_VALIDATE_INPUT, summary="debug suite isolated (gate-assist)")
            return

        # 1) known commands -> mapped command_type
        # 2) non-command text -> free_chat
        # 3) unknown bang-command -> unknown_command
        command_type = _detect_command_type(raw_content)
        if command_type is None:
            if _looks_like_command(raw_content):
                command_type = "unknown_command"
            else:
                command_type = "free_chat"

        # Discord context → Ovv context
        channel_id, thread_name, channel = _extract_discord_context(message)
        if not channel_id:
            _log_debug(trace_id=trace_id, checkpoint=CP_BG_VALIDATE_INPUT, summary="drop empty channel_id")
            return

        author_id, user_name = _extract_author_meta(message)

        # Discord Thread = task_id = context_key（現行方針）
        context_key = channel_id
        task_id = context_key

        user_meta = {"user_id": author_id, "user_name": user_name}

        # content rule:
        # - free_chat: full raw
        # - other: strip head token
        if command_type == "free_chat":
            content = raw_content
        else:
            content = _strip_head_token(raw_content).strip()

        # ---- Build packet ----
        last_checkpoint = CP_BG_BUILD_PACKET
        _log_debug(trace_id=trace_id, checkpoint=CP_BG_BUILD_PACKET, summary="build input packet")

        packet = _build_input_packet_failsafe(
            trace_id=trace_id,
            raw_content=raw_content,
            command_type=command_type,
            content=content,
            author_id=author_id,
            channel_id=channel_id,
            context_key=context_key,
            task_id=task_id,
            user_meta=user_meta,
            meta={
                "discord_channel_id": channel_id,
                "discord_message_id": str(getattr(message, "id", "") or ""),
                "discord_thread_name": thread_name,
            },
        )

        # ---- Capture (non-fatal) ----
        try:
            capture(packet)
        except Exception as e:
            if DEBUG_BIS:
                _log_error(
                    trace_id=trace_id,
                    checkpoint=CP_BG_BUILD_PACKET,
                    summary="capture failed (non-fatal)",
                    code="E_BG_CAPTURE",
                    exc=e,
                    at="BG_BUILD_PACKET",
                    retryable=False,
                )
                traceback.print_exc()

        if DEBUG_BIS:
            print("[Boundary_Gate] Captured InputPacket:", packet)

        # ---- Dispatch to BIS pipeline ----
        last_checkpoint = CP_BG_DISPATCH_CORE
        _log_debug(trace_id=trace_id, checkpoint=CP_BG_DISPATCH_CORE, summary="dispatch interface_box.handle_request")

        final_message: Optional[str] = None
        try:
            final_message = await handle_request(packet)
        except Exception as e:
            last_checkpoint = CP_BG_FAILSAFE
            _log_error(
                trace_id=trace_id,
                checkpoint=CP_BG_FAILSAFE,
                summary="pipeline exception routed to failsafe",
                code="E_BG_PIPELINE",
                exc=e,
                at="BG_DISPATCH_CORE",
                retryable=False,
            )
            if DEBUG_BIS:
                print("==== BIS PIPELINE EXCEPTION (Boundary_Gate) ====")
                print("trace_id:", trace_id)
                print("last_checkpoint:", last_checkpoint)
                print("-- InputPacket --")
                try:
                    print(packet)
                except Exception:
                    print("<unprintable packet>")
                print("-- Traceback --")
                traceback.print_exc()
                print("================================================")
            final_message = _bg_failsafe_message(trace_id, last_checkpoint)

        # ---- Discord reply ----
        if final_message and channel is not None:
            try:
                await channel.send(final_message)
            except Exception as e:
                _log_error(
                    trace_id=trace_id,
                    checkpoint=CP_BG_FAILSAFE,
                    summary="failed to send discord message",
                    code="E_BG_SEND",
                    exc=e,
                    at="ST_SEND_DISCORD",
                    retryable=False,
                )
                if DEBUG_BIS:
                    traceback.print_exc()

    except Exception as e:
        last_checkpoint = CP_BG_FAILSAFE
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_BG_FAILSAFE,
            summary="unexpected boundary exception routed to failsafe",
            code="E_BG_UNEXPECTED",
            exc=e,
            at=last_checkpoint,
            retryable=False,
        )
        if DEBUG_BIS:
            traceback.print_exc()

        try:
            channel = _safe_get_channel(message)
            if channel is not None:
                await channel.send(_bg_failsafe_message(trace_id, last_checkpoint))
        except Exception:
            if DEBUG_BIS:
                traceback.print_exc()