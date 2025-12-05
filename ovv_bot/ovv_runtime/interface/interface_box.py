# ovv_runtime/interface/interface_box.py
# BIS Architecture - Interface_Box v1.0
#
# 目的:
#   Boundary_Gate（入口層）から受け取った InputPacket を
#   Ovv 推論のための安全・安定・統一された形に整形し、
#   call_ovv → Stabilizer に渡す準備をする “中間処理専用ボックス”。
#
# 概念的役割:
#   - 入力整形
#   - メモリ/ThreadBrain のロード
#   - 推論前Hook（TB更新など）
#   - Ovv推論呼び出し
#   - 推論後Hook（TB保存など）
#   - Stabilizer が扱いやすい OutputPacket に変換
#
# 注意:
#   - Interface_Box は絶対に Discord API に触れない
#   - Notion API / DB I/O を直接扱わない
#   - Ovv コア推論（call_ovv）と Boundary_Gate を疎結合に保つ
#
# 必須入力:
#   packet: InputPacket (dict)
#     {
#       "context_key": int,
#       "user_text": str,
#       "channel_is_task": bool,
#     }
#
# 出力:
#   OutputPacket (dict)
#     {
#       "raw_output": str,
#       "final_text": str,   # FINAL 抽出前 or 抽出後は Stabilizer 次第
#       "thread_brain_updated": bool
#     }
#

from typing import Dict, Any

import database.pg as db_pg
from ovv.ovv_call import call_ovv


# ============================================================
# OutputPacket Builder
# ============================================================
def _build_output_packet(raw_output: str, final_text: str, tb_updated: bool) -> Dict[str, Any]:
    """
    Interface_Box → Stabilizer に渡すための統一形式。
    """
    return {
        "raw_output": raw_output,
        "final_text": final_text,
        "thread_brain_updated": tb_updated,
    }


# ============================================================
# FINAL 抽出（Stabilizer に最終的には移動するが、暫定的にここに置く）
# ============================================================
def _extract_final(raw: str) -> str:
    """
    [FINAL] セクションを切り出す。
    Stabilizer 実装後、この処理は移動する。
    """
    if "[FINAL]" not in raw:
        return raw.strip()
    return raw.split("[FINAL]", 1)[1].strip()


# ============================================================
# Main Inference Function
# ============================================================
async def run_inference(packet: Dict[str, Any]) -> Dict[str, Any]:
    """
    Boundary_Gate から呼ばれるメイン処理。
    BIS の中間層として、Ovv コア推論へ渡すまでを責務とする。

    packet 必須項目:
        - context_key        : チャンネル/スレッド単位キー（int）
        - user_text          : str
        - channel_is_task    : bool
    """

    context_key = packet["context_key"]
    user_text = packet["user_text"]
    is_task = packet["channel_is_task"]

    session_id = str(context_key)

    # ------------------------------------------------------------
    # 1. RuntimeMemory ロード
    # ------------------------------------------------------------
    recent_mem = db_pg.load_runtime_memory(session_id)

    # ------------------------------------------------------------
    # 2. Task チャンネルの場合 → ThreadBrain を事前更新
    # ------------------------------------------------------------
    tb_updated = False
    if is_task:
        tb = db_pg.generate_thread_brain(context_key, recent_mem)
        if tb:
            db_pg.save_thread_brain(context_key, tb)
            tb_updated = True

    # ------------------------------------------------------------
    # 3. Ovv コア推論呼び出し（call_ovv）
    # ------------------------------------------------------------
    raw_output = call_ovv(context_key, user_text, recent_mem)

    # ------------------------------------------------------------
    # 4. FINAL 抽出（暫定。後で Stabilizer に移す）
    # ------------------------------------------------------------
    final_text = _extract_final(raw_output)

    # ------------------------------------------------------------
    # 5. 出力パケット構築
    # ------------------------------------------------------------
    return _build_output_packet(
        raw_output=raw_output,
        final_text=final_text,
        tb_updated=tb_updated,
    )