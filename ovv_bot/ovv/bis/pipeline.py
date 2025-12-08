# ovv/bis/pipeline.py
# Ovv Flow Pipeline v1.1 – Boundary → Interface → Core → Stabilizer
#
# 全チャンネルで ThreadBrain を生成する A モード対応版。

from __future__ import annotations

from typing import Optional

from ovv.bis.boundary_gate import InputPacket
from ovv.bis.state_manager import decide_state
from ovv.bis.interface_box import build_interface_packet
from ovv.bis.stabilizer import extract_final_answer
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

from ovv.ovv_call import call_ovv
import database.pg as db_pg


# ============================================================
# [PIPELINE] Main Stream
#  Boundary → Interface → Core → Stabilizer
# ============================================================
def run_ovv_pipeline_from_boundary(packet: InputPacket) -> str:

    # -----------------------------------------
    # UNPACK boundary packet
    # -----------------------------------------
    session_id = packet.session_id
    context_key = packet.context_key
    user_text = packet.text

    # -----------------------------------------
    # [PERSIST] runtime_memory append
    # -----------------------------------------
    db_pg.append_runtime_memory(
        session_id=session_id,
        role="user",
        content=user_text,
        limit=40
    )

    # -----------------------------------------
    # [PERSIST] runtime_memory load
    # -----------------------------------------
    mem = db_pg.load_runtime_memory(session_id)

    # -----------------------------------------
    # [PERSIST/CORE] ThreadBrain 全チャンネル生成
    # A モード：is_task を無視して常に TB 生成
    # -----------------------------------------
    tb_summary = db_pg.generate_thread_brain(context_key, mem)

    if tb_summary:
        tb_summary = filter_constraints_from_thread_brain(tb_summary)
        db_pg.save_thread_brain(context_key, tb_summary)
    else:
        tb_summary = db_pg.load_thread_brain(context_key)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)

    # -----------------------------------------
    # [IFACE] state_hint 決定
    # -----------------------------------------
    state_hint = decide_state(
        context_key=context_key,
        user_text=user_text,
        recent_mem=mem,
        task_mode=False  # 全チャンネル TB 時は task_mode に依存しない
    )

    # -----------------------------------------
    # [IFACE] Interface Packet 構築
    # -----------------------------------------
    iface_packet = build_interface_packet(
        boundary_packet={
            "text": user_text,
            "session_id": session_id,
            "context_key": context_key,
            "is_task_channel": False,
        },
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    # -----------------------------------------
    # [CORE] Ovv-Core 呼び出し
    # -----------------------------------------
    raw_ans = call_ovv(context_key, iface_packet)

    # -----------------------------------------
    # [STAB] Stabilizer – FINAL 抽出
    # -----------------------------------------
    final_ans = extract_final_answer(raw_ans)

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。"

    return final_ans