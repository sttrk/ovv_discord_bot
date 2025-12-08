# debug/health_monitor.py
# ============================================================================
# [MODULE CONTRACT]
# NAME: health_monitor
# LAYER: BIS-DIAG (Diagnostics Layer)
#
# ROLE:
#   - BIS パイプラインの「自動ヘルス監視装置」。
#   - 各レイヤ（Boundary → Interface → Core → Stabilizer → Persist）の
#     挙動に破綻がないかを静的・動的に検査し、異常があれば ALERT チャンネルへ通知する。
#
# INPUT:
#   - bot: discord.Client / commands.Bot
#   - (内部参照) last_interface_packet（capture_interface_packet により保存された直近の IFACE Packet）
#
# OUTPUT:
#   - None（ただし alert チャンネルへ Discord メッセージ送信）
#
# MUST:
#   - Ovv パイプラインの「検査のみ」を行い、挙動を変更してはならない
#   - Exception を握りつぶさず、alert へ報告する
#   - Discord API 使用は alert チャンネルのみに限定
#   - InterfacePacket の構造を破壊しない
#   - Persist / TB / runtime_memory の読み込みは read-only で行う
#
# MUST NOT:
#   - Ovv Core を呼び出さない
#   - Pipeline の動作を変更しない
#   - ThreadBrain / runtime_memory を書き換えない
#   - Stabilizer へ入力する生データ（raw_core_answer）を改変しない
#
# DEPENDENCY:
#   - ovv.bis.capture_interface_packet.get_last_interface_packet
#   - database.pg（read-only access）
#   - ovv.bis.stabilizer.extract_final_answer
#
# TRIGGER:
#   - bot.on_message の最後で run_health_monitor(bot) を await する事で発火
#
# ============================================================================
import os
import discord

from ovv.bis.capture_interface_packet import get_last_interface_packet
from ovv.bis.stabilizer import extract_final_answer
import database.pg as db_pg


# ============================================================================
# Alert チャンネル ID（環境変数優先）
# ============================================================================
ALERT_CHANNEL_ID = 1446059143571181601
_env_id = os.getenv("OVV_ALERT_CHANNEL_ID")
if _env_id:
    try:
        ALERT_CHANNEL_ID = int(_env_id)
    except ValueError:
        print("[HEALTH] Invalid OVV_ALERT_CHANNEL_ID; using fallback.")


# ============================================================================
# 内部：alert 送信ヘルパー
# ============================================================================
async def _send_alert(bot, stage: str, detail: str):
    """
    ALERT チャンネルに BIS 障害情報を送信。
    本体は完全非同期で、Ovv の通常動作には影響しない。
    """
    ch = bot.get_channel(ALERT_CHANNEL_ID)
    if ch is None:
        print(f"[HEALTH] ALERT CHANNEL ({ALERT_CHANNEL_ID}) NOT FOUND")
        return

    text = (
        "=== OVV HEALTH ALERT ===\n"
        f"Stage: {stage}\n"
        f"Detail: {detail}\n"
    )
    await ch.send(f"```txt\n{text}\n```")


# ============================================================================
# PUBLIC API
# run_health_monitor — Ovv 自動ヘルスチェック（BIS-DIAG）
# ============================================================================
async def run_health_monitor(bot):
    """
    BIS レイヤの健全性を診断する。
    Boundary → Interface → Persist → TB → Core/Stabilizer の
    重要ポイントで異常があれば alert に通知する。
    """

    # --------------------------------------------------------
    # 1. InterfacePacket が正しく capture されているか
    # --------------------------------------------------------
    iface = get_last_interface_packet()
    if iface is None:
        await _send_alert(bot, "IFACE", "No InterfacePacket was captured.")
        return

    # --------------------------------------------------------
    # 2. runtime_memory が正常にロードできるか
    # --------------------------------------------------------
    session_id = iface.get("session_id")
    try:
        mem = db_pg.load_runtime_memory(session_id)
        if mem is None:
            raise ValueError("runtime_memory returned None (expected list)")
    except Exception as e:
        await _send_alert(bot, "PERSIST:runtime_memory", repr(e))
        return

    # --------------------------------------------------------
    # 3. ThreadBrain のロード検査
    # --------------------------------------------------------
    ctx = iface.get("context_key")
    try:
        tb = db_pg.load_thread_brain(ctx)
        # TB は None の場合も正常。破損している場合のみエラー。
    except Exception as e:
        await _send_alert(bot, "PERSIST:thread_brain", repr(e))
        return

    # --------------------------------------------------------
    # 4. Core → Stabilizer パスが有効か（raw_core_answer）
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # すべて問題なし（成功時は無音）
    # --------------------------------------------------------
    return