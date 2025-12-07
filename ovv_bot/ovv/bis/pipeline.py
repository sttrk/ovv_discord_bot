# ovv/bis/pipeline.py

"""
[MODULE CONTRACT]
NAME: pipeline
ROLE: IFACE + CORE + STAB + PERSIST Dispatcher

INPUT:
  - BoundaryPacket:
      - context_key: int
      - session_id: str
      - is_task_channel: bool
      - text: str

OUTPUT:
  - final_text: str  # Discord へそのまま送信可能な安定テキスト

SIDE EFFECTS:
  - runtime_memory append（session_id 単位）
  - runtime_memory load
  - thread_brain generate / load / save（context_key 単位）
  - audit_log へのエラーログ（必要時）

MUST:
  - BoundaryPacket → InputPacket への変換フローを一元管理すること
  - runtime_memory の append / load をここで完結させること
  - ThreadBrain の generate / load / save をここで完結させること
  - Ovv-Core をこのモジュールからのみ呼び出すこと
  - Stabilizer を経由した [FINAL] 抽出を必ず行うこと
  - bot.py に Core / Persist / Stabilizer の責務を戻さないこと

MUST NOT:
  - Discord I/O（send 等）を直接呼ばないこと
  - Boundary Gate の責務（Discord Message の検疫）を重複実装しないこと
  - Ovv-Core の内部仕様（プロンプト内容等）を書き換えないこと

DEPENDENCY:
  - database.pg（runtime_memory / thread_brain / audit_log）
  - ovv.bis.interface_box.build_input_packet
  - ovv.bis.state_manager.decide_state
  - ovv.ovv_call.call_ovv
  - ovv.bis.stabilizer.extract_final_answer
  - ovv.bis.constraint_filter.filter_constraints_from_thread_brain
"""

from typing import Optional, Dict, Any, List

# ============================================================
# [PERSIST] PostgreSQL / ThreadBrain / Audit
# ============================================================
import database.pg as db_pg

# runtime_memory
_load_runtime_memory = db_pg.load_runtime_memory
_append_runtime_memory = db_pg.append_runtime_memory

# ThreadBrain
_load_thread_brain = db_pg.load_thread_brain
_save_thread_brain = db_pg.save_thread_brain
_generate_thread_brain = db_pg.generate_thread_brain

# Audit Log（任意）
_log_audit = getattr(db_pg, "log_audit", None)

# ============================================================
# [IFACE] Interface Box / State Manager
# ============================================================
from ovv.bis.interface_box import build_input_packet as build_interface_packet
from ovv.bis.state_manager import decide_state

# ============================================================
# [CORE] Ovv Core Call Layer
# ============================================================
from ovv.ovv_call import call_ovv

# ============================================================
# [STAB] Stabilizer（[FINAL] 抽出）
# ============================================================
from ovv.bis.stabilizer import extract_final_answer

# ============================================================
# [FILTER] ThreadBrain Constraint Filter
# ============================================================
from ovv.bis.constraint_filter import filter_constraints_from_thread_brain


# ============================================================
# 型メモ（BoundaryPacket 想定）
# ============================================================

class BoundaryPacketLike:
    """
    想定される属性:
      - context_key: int
      - session_id: str
      - is_task_channel: bool
      - text: str
    """
    context_key: int
    session_id: str
    is_task_channel: bool
    text: str


# ============================================================
# [PERSIST] runtime_memory append helper
# ============================================================
def _append_user_message(
    session_id: str,
    user_text: str,
    is_task: bool,
) -> None:
    """
    [PERSIST]
    - ユーザー発話を runtime_memory に追加する。
    - タスクchは深め(40)、通常chは浅め(12)で保持。
    """
    limit = 40 if is_task else 12
    _append_runtime_memory(
        session_id=session_id,
        role="user",
        content=user_text,
        limit=limit,
    )


# ============================================================
# [PERSIST] ThreadBrain 更新ロジック
# ============================================================
def _update_thread_brain(
    context_key: int,
    session_id: str,
    recent_mem: List[Dict[str, Any]],
    is_task: bool,
) -> Optional[Dict[str, Any]]:
    """
    [PERSIST]
    - タスクch: 毎ターン TB を再生成して保存。
    - 通常ch: 既存 TB があれば読み出しのみ。
    - いずれも constraint_filter に通してから返す。
    """
    tb_summary: Optional[Dict[str, Any]] = None

    if is_task:
        # タスクチャンネルでは毎回再生成
        tb_summary = _generate_thread_brain(context_key, recent_mem)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)
            _save_thread_brain(context_key, tb_summary)
    else:
        # 通常チャンネルは既存 TB のみ利用
        tb_summary = _load_thread_brain(context_key)
        if tb_summary:
            tb_summary = filter_constraints_from_thread_brain(tb_summary)

    return tb_summary


# ============================================================
# [IFACE] Main Pipeline Entry
# ============================================================
def run_ovv_pipeline_from_boundary(boundary_packet: BoundaryPacketLike) -> str:
    """
    [IFACE]
    BoundaryPacket から Interface → Core → Stabilizer までを一括実行し、
    Discord へそのまま送信可能な最終テキスト（final_text）を返す。

    bot.py 側は:
        final_text = run_ovv_pipeline_from_boundary(boundary_packet)
        await message.channel.send(final_text)

    という形でのみ利用する。
    """

    # --------------------------------------------------------
    # 1) BoundaryPacket 展開
    # --------------------------------------------------------
    context_key: int = getattr(boundary_packet, "context_key")
    session_id: str = getattr(boundary_packet, "session_id")
    is_task: bool = getattr(boundary_packet, "is_task_channel")
    user_text: str = getattr(boundary_packet, "text") or ""

    try:
        # ----------------------------------------------------
        # 2) runtime_memory append（ユーザー発話）
        # ----------------------------------------------------
        _append_user_message(session_id, user_text, is_task)

        # ----------------------------------------------------
        # 3) runtime_memory load
        # ----------------------------------------------------
        mem: List[Dict[str, Any]] = _load_runtime_memory(session_id) or []

        # ----------------------------------------------------
        # 4) ThreadBrain 更新 / 取得
        # ----------------------------------------------------
        tb_summary = _update_thread_brain(
            context_key=context_key,
            session_id=session_id,
            recent_mem=mem,
            is_task=is_task,
        )

        # ----------------------------------------------------
        # 5) 軽量ステート推定（State Manager）
        # ----------------------------------------------------
        state_hint: Dict[str, Any] = decide_state(
            context_key=context_key,
            user_text=user_text,
            recent_mem=mem,
            task_mode=is_task,
        )

        # ----------------------------------------------------
        # 6) Interface_Box: InputPacket 構築
        # ----------------------------------------------------
        input_packet: Dict[str, Any] = build_interface_packet(
            user_text=user_text,
            runtime_memory=mem,
            thread_brain=tb_summary,
            state_hint=state_hint,
        )

        # ----------------------------------------------------
        # 7) Ovv-Core 呼び出し
        # ----------------------------------------------------
        raw_ans: str = call_ovv(context_key, input_packet)

        # ----------------------------------------------------
        # 8) Stabilizer: [FINAL] 抽出
        # ----------------------------------------------------
        final_ans: str = extract_final_answer(raw_ans)

        # ----------------------------------------------------
        # 9) フェイルセーフ
        # ----------------------------------------------------
        if not final_ans:
            return "Ovv の応答生成に問題が発生しました。少し時間をおいてもう一度試してください。"

        return final_ans

    except Exception as e:
        # ----------------------------------------------------
        # 10) Pipeline レベルのエラーハンドリング
        # ----------------------------------------------------
        if _log_audit is not None:
            try:
                _log_audit(
                    "pipeline_error",
                    {
                        "context_key": context_key,
                        "session_id": session_id,
                        "user_text": user_text[:500],
                        "error": repr(e),
                    },
                )
            except Exception:
                # ログの失敗は握りつぶす
                pass

        return "Ovv 内部処理中に予期しないエラーが発生しました。少し時間をおいてもう一度試してください。"