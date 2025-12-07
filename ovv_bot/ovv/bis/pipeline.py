# ovv/bis/pipeline.py
# Ovv Flow Pipeline v1.1 – Boundary → Interface → Core → Stabilizer
#
# [MODULE CONTRACT]
# NAME: pipeline
# ROLE: IFACE + CORE + STAB + PERSIST Coordinator
#
# INPUT:
#   - packet: InputPacket (from ovv.bis.boundary_gate)
#
# OUTPUT:
#   - final_answer: str
#       Discord にそのまま送信可能な安定テキスト
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
#   - ThreadBrain は「domain 専用（Domain-Only TB）」で Core に渡すこと
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
#   - ovv.bis.domain_control_splitter.split_thread_brain
#   - ovv.ovv_call.call_ovv
#   - database.pg
#

from __future__ import annotations

from typing import Optional, Dict, Any

from ovv.bis.boundary_gate import InputPacket

# [IFACE] state hint
from ovv.bis.state_manager import decide_state

# [IFACE] Interface Box
from ovv.bis.interface_box import build_interface_packet

# [STAB] Stabilizer
from ovv.bis.stabilizer import extract_final_answer

# [FILTER] TB constraint filter
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain

# [FILTER] Domain / Control Splitter
from ovv.bis.domain_control_splitter import split_thread_brain

# [CORE] Ovv Core caller
from ovv.ovv_call import call_ovv

# [PERSIST] PostgreSQL access
import database.pg as db_pg


# ============================================================
# [IFACE][CORE][STAB][PERSIST]
# run_ovv_pipeline_from_boundary
#
# ROLE:
#   - IFACE + CORE + STAB + PERSIST の「調整役」
#   - Gate / Discord / bot には依存しない純粋パイプライン
#
# FLOW:
#   InputPacket
#     → runtime_memory append/load
#     → ThreadBrain generate/load
#     → constraint_filter
#     → domain/control split
#     → state_hint 決定
#     → Interface_Box（domain TB のみ）
#     → Ovv-Core
#     → Stabilizer
# ============================================================
def run_ovv_pipeline_from_boundary(packet: InputPacket) -> str:
    """
    Boundary_Gate から渡された InputPacket を起点に、
    runtime_memory / thread_brain / state_manager / interface_box / ovv_core / stabilizer
    を直列パイプラインとして実行する。

    - Gate / Discord / bot インスタンスには一切依存しない。
    - ThreadBrain は「ドメイン専用（Domain-Only）」のものだけを Interface_Box / Core に渡す。
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
    #   - raw TB を PG から取得（generate or load）
    #   - constraint_filter で危険制約・形式制約を除去
    #   - domain/control に分離（Domain-Only TB 専用レーン）
    # -----------------------------------------
    tb_summary_raw: Optional[Dict[str, Any]] = None

    if is_task:
        # タスク系スレッドでは常に TB を最新化
        tb_summary_raw = db_pg.generate_thread_brain(context_key, mem)
    else:
        # 通常スレッドでは既存 TB を読むだけ
        tb_summary_raw = db_pg.load_thread_brain(context_key)

    # constraint_filter（形式制約などの一次除去）
    tb_filtered: Optional[Dict[str, Any]] = (
        filter_constraints_from_thread_brain(tb_summary_raw) if tb_summary_raw else None
    )

    # Domain / Control 分離（構造レベルでの汚染除去）
    domain_tb, control_tb = split_thread_brain(tb_filtered)

    # タスク系スレッドの場合のみ、Domain-Only TB を保存
    if is_task and domain_tb:
        db_pg.save_thread_brain(context_key, domain_tb)

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
    #   - boundary_packet: Discord 物理層情報（packet.to_dict）
    #   - runtime_memory: 直近メモリ
    #   - thread_brain: Domain-Only TB のみ
    #   - state_hint: 状態ヒント
    # -----------------------------------------
    boundary_packet_dict: Dict[str, Any] = packet.to_dict()

    input_packet = build_interface_packet(
        boundary_packet=boundary_packet_dict,
        runtime_memory=mem,
        thread_brain=domain_tb,   # ★ Domain-Only TB のみ Core へ渡す
        state_hint=state_hint,
    )

    # -----------------------------------------
    # [CORE] Ovv-Core 呼び出し
    #   - 現時点では control_tb は未使用
    #   - 将来、prompt_control 等で利用可能
    # -----------------------------------------
    raw_ans = call_ovv(
        context_key,
        input_packet,
        # prompt_control=control_tb  # ← 将来の拡張ポイント
    )

    # -----------------------------------------
    # [STAB] Stabilizer で Discord 向けの最終テキストを抽出
    # -----------------------------------------
    final_ans = extract_final_answer(raw_ans)

    if not final_ans:
        final_ans = "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

    return final_ans