# debug/health_monitor.py
# ============================================================
# [MODULE CONTRACT]
# NAME: health_monitor
# ROLE: Auto BIS Health Checker
# ============================================================

import os
import discord

from ovv.bis.capture_interface_packet import get_last_interface_packet
import database.pg as db_pg
from ovv.bis.stabilizer import extract_final_answer

ALERT_CHANNEL_ID_RAW = os.getenv("OVV_ALERT_CHANNEL_ID")
ALERT_CHANNEL_ID = 1446059143571181601  # fallback
try:
    if ALERT_CHANNEL_ID_RAW:
        ALERT_CHANNEL_ID = int(ALERT_CHANNEL_ID_RAW)
except ValueError:
    print("[HEALTH] Invalid OVV_ALERT_CHANNEL_ID")


async def _send_alert(bot, stage: str, detail: str):
    ch = bot.get_channel(ALERT_CHANNEL_ID)
    if ch is None:
        print("[HEALTH] ALERT CHANNEL NOT FOUND")
        return
    text = (
        "=== OVV HEALTH ALERT ===\n"
        f"Stage: {stage}\n"
        f"Detail: {detail}\n"
    )
    await ch.send(f"```txt\n{text}\n```")


async def run_health_monitor(bot):

    # 1) InterfacePacket
    iface = get_last_interface_packet()
    if iface is None:
        await _send_alert(bot, "IFACE", "No InterfacePacket captured.")
        return

    # 2) runtime_memory
    session_id = iface.get("session_id")
    try:
        mem = db_pg.load_runtime_memory(session_id)
        if mem is None:
            raise ValueError("runtime_memory returned None")
    except Exception as e:
        await _send_alert(bot, "PERSIST:runtime_memory", repr(e))
        return

    # 3) ThreadBrain
    ctx = iface.get("context_key")
    try:
        tb = db_pg.load_thread_brain(ctx)
    except Exception as e:
        await _send_alert(bot, "PERSIST:thread_brain", repr(e))
        return

    # 4) Core → Stabilizer パスの確認
    raw_ans = iface.get("raw_core_answer")
    if raw_ans:
        try:
            final = extract_final_answer(raw_ans)
            if not final:
                await _send_alert(bot, "CORE/STAB", "Final answer missing or invalid.")
                return
        except Exception as e:
            await _send_alert(bot, "STABILIZER", repr(e))
            return

    # no alert = ok
    return