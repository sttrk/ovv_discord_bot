# ovv/bis/pipeline.py
# ============================================================
# [MODULE CONTRACT]
# NAME: pipeline
# LAYER: BIS-PIPE (Coordinator)
#
# ROLE:
#   - Boundary_Gate → Interface_Box → Ovv-Core → Stabilizer → final_text
#   - PERSIST 層（PostgreSQL）との I/O を一元的に管理する。
#
# INPUT:
#   - packet: InputPacket (ovv.bis.boundary_gate.InputPacket)
#
# OUTPUT:
#   - final_answer: str（Discord にそのまま送信可能）
#
# MUST:
#   - runtime_memory / thread_brain の永続化は database.pg のみ経由する
#   - state_manager / interface_box / ovv_call / stabilizer を直列に呼び出す
#
# MUST NOT:
#   - Discord API を呼ばない
#   - commands.Bot に依存しない
#   - debug 用の挙動を紛れ込ませない（※専用フックのみ許可）
# ============================================================

from __future__ import annotations

from typing import Optional

from ovv.bis.boundary_gate import InputPacket

# [IFACE] state hint
from ovv.bis.state_manager import decide_state

# [IFACE] Interface Box
from ovv.bis.interface_box import build_interface_packet

# [STAB] Stabilizer
from ovv.bis.stabilizer import extract_final_answer

# [FILTER] TB constraint filter
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

# [CORE] Ovv Core caller
from ovv.ovv_call import call_ovv

# [PERSIST] PostgreSQL access
import database.pg as db_pg

# [DEBUG] InterfacePacket capture hook (BIS Debug Layer)
from ovv.bis.capture_interface_packet import capture_interface_packet


# ============================================================
# [PIPELINE] run_ovv_pipeline_from_boundary
# ============================================================

def run_ovv_pipeline_from_boundary(packet: InputPacket) -> str:
    """
    Boundary_Gate から渡された InputPacket を起点に、
    runtime_memory / thread_brain / state_manager / interface_box / ovv_core / stabilizer
    を直列パイプラインとして実行する。
    """

    context_key = packet.context_key
    session_id = packet.session_id
    user_text = packet.text
    is_task = packet.is_task_channel

    print(f"[BIS-PIPE] START: ctx={context_key}, session={session_id}, is_task={is_task}")

    # -----------------------------------------
    # [PERSIST] runtime_memory append
    # -----------------------------------------
    try:
        db_pg.append_runtime_memory(
            session_id=session_id,
            role="user",
            content=user_text,
            limit=40 if is_task else 12,
        )
        print("[BIS-PIPE] runtime_memory append OK")
    except Exception as e:
        print("[BIS-PIPE] runtime_memory append ERROR:", repr(e))

    # -----------------------------------------
    # [PERSIST] runtime_memory load
    # -----------------------------------------
    try:
        mem = db_pg.load_runtime_memory(session_id)
        print(f"[BIS-PIPE] runtime_memory load OK (len={len(mem)})")
    except Exception as e:
        print("[BIS-PIPE] runtime_memory load ERROR:", repr(e))
        mem = []

    # -----------------------------------------
    # [PERSIST/CORE] ThreadBrain の利用・更新
    # -----------------------------------------
    tb_summary: Optional[dict] = None

    try:
        tb_summary = db_pg.generate_thread_brain(context_key, mem)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)
            db_pg.save_thread_brain(context_key, tb_summary)
            print("[BIS-PIPE] ThreadBrain generate/save OK")
        else:
            print("[BIS-PIPE] ThreadBrain generate: None (mem empty or summarizer returned None)")
    except Exception as e:
        print("[BIS-PIPE] ThreadBrain generate/save ERROR:", repr(e))

    # -----------------------------------------
    # [IFACE] state_hint 決定
    # -----------------------------------------
    try:
        state_hint = decide_state(
            context_key=context_key,
            user_text=user_text,
            recent_mem=mem,
            task_mode=is_task,
        )
        print("[BIS-PIPE] state_manager decide_state OK")
    except Exception as e:
        print("[BIS-PIPE] state_manager decide_state ERROR:", repr(e))
        state_hint = {}

    # -----------------------------------------
    # [IFACE] Interface_Box で Core 入力パケットを組み立て
    # -----------------------------------------
    try:
        boundary_dict = packet.to_dict()
        iface_packet = build_interface_packet(
            boundary_packet=boundary_dict,
            runtime_memory=mem,
            thread_brain=tb_summary,
            state_hint=state_hint,
        )
        print("[BIS-PIPE] InterfaceBox build_interface_packet OK")
    except Exception as e:
        print("[BIS-PIPE] InterfaceBox ERROR:", repr(e))
        return "Ovv の内部処理（InterfaceBox）でエラーが発生しました。"

    # -----------------------------------------
    # [DEBUG] InterfacePacket Capture Hook
    # -----------------------------------------
    try:
        capture_interface_packet(iface_packet)
        print("[BIS-PIPE] Debug capture_interface_packet OK")
    except Exception as e:
        # デバッグ機構は本流を止めない
        print("[BIS-PIPE] Debug capture_interface_packet ERROR:", repr(e))

    # -----------------------------------------
    # [CORE] Ovv-Core 呼び出し
    # -----------------------------------------
    try:
        raw_ans = call_ovv(context_key, iface_packet)
        print("[BIS-PIPE] Core call_ovv OK")
    except Exception as e:
        print("[BIS-PIPE] Core call_ovv ERROR:", repr(e))
        return "Ovv のコア処理でエラーが発生しました。"

    # -----------------------------------------
    # [STAB] Stabilizer で Discord 向けの最終テキストを抽出
    # -----------------------------------------
    try:
        final_ans = extract_final_answer(raw_ans)
        print("[BIS-PIPE] Stabilizer extract_final_answer OK")
    except Exception as e:
        print("[BIS-PIPE] Stabilizer ERROR:", repr(e))
        final_ans = "Ovv の応答整形中にエラーが発生しました。"

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    print(f"[BIS-PIPE] END: ctx={context_key}, session={session_id}")
    return final_ans