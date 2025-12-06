# debug/debug_commands.py
# Debug Command Suite v1.0 – Memory/ThreadBrain Inspection
#
# ROLE:
#   - debug_router からのみ呼び出される「安全なデバッグ操作層」
#
# MUST:
#   - PG / Notion / OvvCore に副作用を与えない
#   - 読み出し専用の検査のみ行う
#   - Discord に安全に送信できる 1900 文字制限を守る
#   - debug_context から依存を受ける（依存は逆方向にしない）
#
# MUST NOT:
#   - Ovv Core を直接呼ばない
#   - ThreadBrain を更新しない
#   - runtime_memory を変更しない
#
# ENTRYPOINTS:
#   - dbg_mem
#   - dbg_all

import json
from discord.ext import commands
from ovv.bis.boundary_gate import build_input_packet as build_boundary_packet
from debug.debug_context import debug_context


# ============================================================
# Helper: truncate for Discord safety
# ============================================================
def _truncate(text: str) -> str:
    if len(text) > 1900:
        return text[:1900] + "\n...[truncated]"
    return text


# ============================================================
# dbg_mem – show runtime_memory for current session
# ============================================================
async def dbg_mem(bot, message):
    boundary_packet = build_boundary_packet(message)
    if boundary_packet is None:
        await message.channel.send("[DBG] context_key を取得できません。")
        return

    session_id = boundary_packet.session_id
    mem = debug_context.load_mem(session_id)

    if not mem:
        await message.channel.send("[DBG] runtime_memory は空です。")
        return

    formatted = json.dumps(mem, ensure_ascii=False, indent=2)
    await message.channel.send(f"```json\n{_truncate(formatted)}\n```")


# ============================================================
# dbg_all – show both runtime_memory & thread_brain
# ============================================================
async def dbg_all(bot, message):
    boundary_packet = build_boundary_packet(message)
    if boundary_packet is None:
        await message.channel.send("[DBG] context_key を取得できません。")
        return

    ck = boundary_packet.context_key
    session_id = boundary_packet.session_id

    mem = debug_context.load_mem(session_id)
    tb = debug_context.brain_load(ck)

    payload = {
        "context_key": ck,
        "session_id": session_id,
        "runtime_memory": mem,
        "thread_brain": tb,
    }

    formatted = json.dumps(payload, ensure_ascii=False, indent=2)
    await message.channel.send(f"```json\n{_truncate(formatted)}\n```")