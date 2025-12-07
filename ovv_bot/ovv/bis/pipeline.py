# ovv/bis/pipeline.py
# Ovv Flow Pipeline v1.1 – Boundary → Interface → Core → Stabilizer
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
# MUST_NOT:
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

from typing import Optional, Dict, Any
from dataclasses import asdict, is_dataclass

# [GATE INPUT]
from ovv.bis.boundary_gate import InputPacket

# [IFACE] state hint
from ovv.bis.state_manager import decide_state

# [IFACE] Interface Box (NEW SPEC)
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
# Utility — Convert BoundaryPacket dataclass → dict
# ============================================================
def _packet_to_dict(packet: InputPacket) -> Dict[str, Any]:
    if is_dataclass(packet):
        return asdict(packet)
    # fallback
    return packet.__dict__


# ============================================================
# [PIPELINE] run_ovv_pipeline_from_boundary
# ============================================================
def run_ovv_pipeline_from_boundary(packet: InputPacket) -> str:
    """
    Boundary_Gate から渡された InputPacket を起点に、
    runtime_memory / thread_brain / state_manager / interface_box / ovv_core / stabilizer
    を直列パイプラインとして実行する。

    Gate / Discord / bot インスタンスには一切依存しない。
    """

    # Convert dataclass → dict (InterfaceBox 仕様準拠)
    boundary_packet: Dict[str, Any] = _packet_to_dict(packet)

    session_id = boundary_packet["session_id"]
    context_key = boundary_packet["context_key"]
    user_text = boundary_packet["text"]
    is_task = boundary_packet["is_task_channel"]

    # -----------------------------------------
    # [PERSIST] runtime_memory append
    # -----------------------------------------
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
        task_mode=is_task,
    )

    # -----------------------------------------
    # [IFACE] InterfaceBox（新仕様）へ統合データを渡す
    # -----------------------------------------
    iface_packet = build_interface_packet(
        boundary_packet=boundary_packet,
        runtime_memory=mem,
        thread_brain=tb_summary,
        state_hint=state_hint,
    )

    # -----------------------------------------
    # [CORE] Ovv-Core 呼び出し
    # -----------------------------------------
    raw_ans = call_ovv(context_key, iface_packet)

    # -----------------------------------------
    # [STAB] Stabilizer で Discord 向け最終出力へ変換
    # -----------------------------------------
    final_ans = extract_final_answer(raw_ans)

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    return final_ans