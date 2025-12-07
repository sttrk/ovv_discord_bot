# ovv/bis/pipeline.py
# Ovv Flow Pipeline v1.0 – Boundary → Interface → Core → Stabilizer
#
# [MODULE CONTRACT]
# NAME: pipeline
# ROLE: IFACE + CORE + STAB + PERSIST Coordinator
#
# INPUT:
#   - InputPacket (from ovv.bis.boundary_gate)
# OUTPUT:
#   - final_answer: str (Discord にそのまま送信可能な安定テキスト)
#
# SIDE EFFECTS:
#   - PostgreSQL:
#       - runtime_memory append / load
#       - thread_brain generate / load / save
#   - ※ Notion への書き込みはここでは行わない（将来拡張）
#
# MUST:
#   - Boundary_Gate から受け取った InputPacket を唯一の入口とする
#   - Interface_Box / Ovv-Core / Stabilizer の責務を越境しない
#   - runtime_memory / thread_brain の I/O 詳細は database.pg に委譲する
#
# MUST NOT:
#   - Discord API を直接呼ばない
#   - bot インスタンスに依存しない
#   - Debug 用の挙動を紛れ込ませない
#
# DEPENDENCY:
#   - ovv.bis.state_manager.decide_state
#   - ovv.bis.interface_box.build_interface_packet
#   - ovv.bis.stabilizer.extract_final_answer
#   - ovv.bis.constraint_filter.filter_constraints_from_thread_brain
#   - ovv.ovv_call.call_ovv
#   - database.pg
#

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


# ============================================================
# [PIPELINE] run_ovv_pipeline_from_boundary
#  ROLE:
#    - IFACE+CORE+STAB+PERSIST の「調整役」
#    - Gate / Discord / bot には依存しない純粋パイプライン
# ============================================================
def run_ovv_pipeline_from_boundary(packet: InputPacket) -> str:
    """
    Boundary_Gate から渡された InputPacket を起点に、
    runtime_memory / thread_brain / state_manager / interface_box / ovv_core / stabilizer
    を直列パイプラインとして実行する。

    Gate / Discord / bot インスタンスには一切依存しない。
    """

    # -----------------------------------------
    # [PERSIST] runtime_memory append
    # -----------------------------------------
    session_id = packet.session_id
    context_key = packet.context_key
    user_text = packet.text
    is_task = packet.is_task_channel

    db_pg.append_runtime_memory(
        session_id,
        "user",
        user_text,
        limit=40 if is_task else 12,
    )

    # -----------------------------------------
    # [PERSIST] runtime_memory load
    # -----------------------------------------
    mem = db_pg.load_runtime_memory(session_id)

    # -----------------------------------------
    # [PERSIST/CORE] ThreadBrain の利用・更新
    # -----------------------------------------
    tb_summary: Optional[dict] = None

    if is_task:
        # タスク系スレッドでは常に TB を最新化
        tb_summary = db_pg.generate_thread_brain(context_key, mem)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)
            db_pg.save_thread_brain(context_key, tb_summary)
    else:
        # 通常スレッドでは既存 TB を読むだけ
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
        task_mode=is_task,
    )

    # -----------------------------------------
    # [IFACE] Interface_Box で Core 入力パケットを組み立て
    # -----------------------------------------
    input_packet = build_interface_packet(
        user_text=user_text,
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    # -----------------------------------------
    # [CORE] Ovv-Core 呼び出し
    # -----------------------------------------
    raw_ans = call_ovv(context_key, input_packet)

    # -----------------------------------------
    # [STAB] Stabilizer で Discord 向けの最終テキストを抽出
    # -----------------------------------------
    final_ans = extract_final_answer(raw_ans)

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    return final_ans