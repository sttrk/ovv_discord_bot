# ovv/bis/boundary_gate.py
# ============================================================
# MODULE CONTRACT: BIS / Boundary_Gate v3.8.1
#   (free_chat pass-through enabled)
# ============================================================

from __future__ import annotations

from typing import Optional, Tuple, Any, Dict
import traceback
import json
import uuid
from datetime import datetime, timezone

from .types import InputPacket
from .interface_box import handle_request
from .capture_interface_packet import capture


DEBUG_BIS = True

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

LAYER_BG = "BG"

CP_BG_ENTRY = "BG_ENTRY"
CP_BG_VALIDATE_INPUT = "BG_VALIDATE_INPUT"
CP_BG_BUILD_PACKET = "BG_BUILD_PACKET"
CP_BG_DISPATCH_CORE = "BG_DISPATCH_CORE"
CP_BG_FAILSAFE = "BG_FAILSAFE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_event(*, trace_id: str, checkpoint: str, layer: str, level: str,
               summary: str, error: Optional[Dict[str, Any]] = None) -> None:
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


def _log_error(*, trace_id: str, checkpoint: str, summary: str,
               code: str, exc: Exception, at: str,
               retryable: bool = False) -> None:
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


def _detect_command_type(raw: str) -> Optional[str]:
    if not raw:
        return None

    head = raw.strip().split()[0].lower()

    if head in _DEBUG_COMMAND_HEADS:
        return None

    mapping = {
        "!t": "task_create",
        "!ts": "task_start",
        "!tp": "task_paused",
        "!tc": "task_end",
        "!wy": "wbs_accept",
        "!we": "wbs_edit",
        "!wd": "wbs_done",
        "!wx": "wbs_drop",
        "!wbs": "wbs_show",
        "!w": "wbs_show",
    }
    return mapping.get(head)


def _strip_head_token(raw: str) -> str:
    if not raw:
        return ""
    parts = raw.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _safe_get_channel(message: Any) -> Any:
    return getattr(message, "channel", None)


def _extract_discord_context(message: Any) -> Tuple[str, str, Any]:
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
        trace_id=trace_id,
    )
    try:
        return InputPacket(**kwargs)
    except TypeError:
        kwargs.pop("trace_id", None)
        kwargs["meta"]["trace_id"] = trace_id
        return InputPacket(**kwargs)


async def handle_discord_input(message: Any) -> None:
    trace_id = str(uuid.uuid4())
    last_checkpoint = CP_BG_ENTRY
    _log_debug(trace_id=trace_id, checkpoint=CP_BG_ENTRY, summary="bg entry")

    try:
        if getattr(getattr(message, "author", None), "bot", False):
            return

        raw_content = (getattr(message, "content", "") or "").strip()
        if not raw_content:
            return

        head = raw_content.split()[0].lower()
        if head in _DEBUG_COMMAND_HEADS:
            return

        # ★ ここが変更点：None → free_chat
        command_type = _detect_command_type(raw_content) or "free_chat"

        channel_id, thread_name, channel = _extract_discord_context(message)
        if not channel_id:
            return

        author_id, user_name = _extract_author_meta(message)
        context_key = channel_id
        task_id = context_key

        user_meta = {"user_id": author_id, "user_name": user_name}
        content = _strip_head_token(raw_content).strip()

        last_checkpoint = CP_BG_BUILD_PACKET
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

        try:
            capture(packet)
        except Exception:
            pass

        last_checkpoint = CP_BG_DISPATCH_CORE
        final_message = await handle_request(packet)

        if final_message and channel is not None:
            await channel.send(final_message)

    except Exception as e:
        _log_error(
            trace_id=trace_id,
            checkpoint=CP_BG_FAILSAFE,
            summary="unexpected boundary exception",
            code="E_BG_UNEXPECTED",
            exc=e,
            at=last_checkpoint,
            retryable=False,
        )
        if DEBUG_BIS:
            traceback.print_exc()