# debug/health_monitor.py
# ============================================================
# [MODULE CONTRACT]
# NAME: health_monitor
# ROLE: Auto BIS Health Checker
#
# PURPOSE:
#   - Ovv の BIS パイプラインが正常に機能しているかを
#     自動で検査し、異常時は alert チャンネルに通知する。
#
# MUST:
#   - 本番ユーザーには一切影響を与えない
#   - Pipeline, Core の挙動を変更しない
#   - Discord API を使用するのは「alert チャンネルのみ」
#
# ============================================================

import os
import discord

# InterfacePacket キャプチャ（正常パイプラインの証拠）
from ovv.bis.capture_interface_packet import get_last_interface_packet

# DB
import database.pg as db_pg

# Stabilizer を使って [FINAL] 抽出テスト
from ovv.bis.stabilizer import extract_final_answer


# ------------------------------------------------------------
# Alert チャンネル指定（環境変数）
# ------------------------------------------------------------
ALERT_CHANNEL_ID_RAW = os.getenv("OVV_ALERT_CHANNEL_ID")
ALERT_CHANNEL_ID = None
try:
    if ALERT_CHANNEL_ID_RAW:
        ALERT_CHANNEL_ID = int(ALERT_CHANNEL_ID_RAW)
except ValueError:
    print("[HEALTH] Invalid OVV_ALERT_CHANNEL_ID")


# ------------------------------------------------------------
# 内部：異常通知
# ------------------------------------------------------------
async def _send_alert(bot, stage: str, detail: str):
    if not ALERT_CHANNEL_ID:
        print("[HEALTH] ALERT CHANNEL NOT CONFIGURED")
        return

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


# ------------------------------------------------------------
# PUBLIC: run_health_monitor() 
#   bot.on_message の最後に入れるだけで動く
# ------------------------------------------------------------
async def run_health_monitor(bot):

    # 1) InterfacePacket が取れているか
    iface = get_last_interface_packet()
    if iface is None:
        await _send_alert(bot, "IFACE", "No InterfacePacket captured.")
        return

    # 2) runtime_memory が壊れていないか
    session_id = iface.get("session_id")
    try:
        mem = db_pg.load_runtime_memory(session_id)
        if mem is None:
            raise ValueError("runtime_memory returned None")
    except Exception as e:
        await _send_alert(bot, "PERSIST:runtime_memory", repr(e))
        return

    # 3) ThreadBrain が破損していないか
    ctx = iface.get("context_key")
    try:
        tb = db_pg.load_thread_brain(ctx)
        # TB は存在しない場合もあるため None は正常扱い
    except Exception as e:
        await _send_alert(bot, "PERSIST:thread_brain", repr(e))
        return

    # 4) Core の応答が Stabilizer の仕様に適合しているか
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

    # 上記すべて正常 → 通知なし（silent success）
    return