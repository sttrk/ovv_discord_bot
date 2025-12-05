# ovv/interface_box.py
# Interface_Box v0.1 (Minimal Edition for A5)
#
# 役割:
#   - Boundary_Gate を通過した「通常メッセージ」を、
#     Ovv 推論に最適化された InputPacket に整形する。
#   - Ovv 本体はこのパケットだけを見ればよい状態にする。
#
# 注意:
#   - Discord には依存しない（生テキスト＋メタ情報のみ扱う）。
#   - I/O や例外処理は一切行わない（bot.py / pg に任せる）。
#   - 推論や判断は行わず、「分類・整形・縮約」に限定する。

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict

from ovv.state_manager import decide_state
from ovv.threadbrain_adapter import build_tb_prompt
import database.pg as db_pg


# ============================================================
# InputPacket 定義
# ============================================================

@dataclass
class InputPacket:
    """
    Ovv コアに渡す「統一入力パケット」。
    B(oundary_Gate) → I(nterface_Box) → Ovv → S(tabilizer)
    の中で、Interface_Box が責任を持って構築する唯一の構造体。

    A5-Minimal では以下のフィールドのみ使用する。
    """
    context_key: int              # スレッド/チャンネルに対応する一意キー
    raw_user_text: str            # Discord 生テキスト
    intent: str                   # "normal" | "task_related" | "system_op" | "ambiguous"
    needs_clarify: bool           # Clarify を優先すべきか
    state_hint: Optional[Dict]    # state_manager.decide_state の結果（なければ None）
    tb_hint: Optional[str]        # Thread Brain からの要約ヒント（なければ None）


# ============================================================
# intent 判定（最小セット）
# ============================================================

_AMBIGUOUS_TOKENS = {
    "これ", "それ", "あれ", "この", "その", "あの",
    "前の", "さっきの", "上の", "下の",
    "どれでも", "なんでも", "いい感じ", "いいように",
    "いい感じに", "さっき言ってたやつ", "例のやつ",
}

_SYSTEM_LIKE_PREFIXES = ("!dbg", "!tt", "!bs", "!br", "!ping")


def _classify_intent(
    raw_text: str,
    is_task_channel: bool,
) -> str:
    """
    A5-Minimal 用 intent 判定。
    - system_op は原則 Boundary_Gate で処理済みだが、
      将来 Ovv 側で見たいケースもあるので軽く検出だけしておく。
    """
    text = (raw_text or "").strip()

    if not text:
        # 空文字は最も危険なので ambiguous に寄せる
        return "ambiguous"

    # system_op らしいもの（!コマンド系）
    if text.startswith("!"):
        for p in _SYSTEM_LIKE_PREFIXES:
            if text.startswith(p):
                return "system_op"
        # その他の !〜 も system_op に入れておく
        return "system_op"

    # task チャンネルなら基本は task_related
    if is_task_channel:
        # ただし露骨に曖昧表現しかない場合は ambiguous 寄せ
        if any(tok in text for tok in _AMBIGUOUS_TOKENS) and len(text) <= 15:
            return "ambiguous"
        return "task_related"

    # 通常チャンネル：
    # 曖昧語だらけで対象が見えない場合は ambiguous
    if any(tok in text for tok in _AMBIGUOUS_TOKENS):
        # 「〜について教えて」「〜の話」など対象が明示されていれば normal
        if "について" in text or "の話" in text or "どうすれば" in text:
            return "normal"
        return "ambiguous"

    # それ以外は normal
    return "normal"


# ============================================================
# needs_clarify 判定（最小版）
# ============================================================

def _should_clarify(
    intent: str,
    raw_text: str,
    tb_summary: Optional[Dict],
) -> bool:
    """
    Clarify を優先すべきかどうかの最小ロジック。
    - intent が ambiguous → True
    - 前回 Thread Brain に unresolved が溜まっており、
      かつ今回の入力が明確に別トピックではなさそうな場合 → True 寄り
    """
    if intent == "ambiguous":
        return True

    text = (raw_text or "").strip()
    if not tb_summary:
        return False

    unresolved = tb_summary.get("unresolved") or []
    if not unresolved:
        return False

    # 簡易：本文が短く、かつ「次」「続き」などの場合は Clarify 寄り
    low = text.lower()
    if len(text) <= 15 and any(
        kw in low for kw in ["次", "つぎ", "続き", "つづき", "next", "continue"]
    ):
        return True

    return False


# ============================================================
# Public: InputPacket を構築するメイン関数
# ============================================================

def build_input_packet(
    context_key: int,
    raw_user_text: str,
    recent_mem: List[dict],
    is_task_channel: bool,
) -> InputPacket:
    """
    Interface_Box のメインエントリ。
    - Discord に依存しない引数だけを受け取り、
      Ovv に渡すための InputPacket を組み立てる。
    - この関数は「例外を投げない」ことを前提とし、
      何があっても最低限のパケットを返す。
    """

    # 1) Thread Brain summary を取得（存在しなくてもよい）
    try:
        tb_summary = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[Interface_Box] load_thread_brain error:", repr(e))
        tb_summary = None

    # 2) state_manager から簡易 state_hint を取得
    try:
        state_hint = decide_state(
            context_key=context_key,
            user_text=raw_user_text,
            recent_mem=recent_mem,
            task_mode=is_task_channel,
        )
    except Exception as e:
        print("[Interface_Box] decide_state error:", repr(e))
        state_hint = None

    # 3) tb_hint（Thread Brain → テキスト要約）
    try:
        tb_hint = build_tb_prompt(tb_summary) if tb_summary else None
        if tb_hint == "":
            tb_hint = None
    except Exception as e:
        print("[Interface_Box] build_tb_prompt error:", repr(e))
        tb_hint = None

    # 4) intent 判定
    try:
        intent = _classify_intent(raw_user_text, is_task_channel=is_task_channel)
    except Exception as e:
        print("[Interface_Box] classify_intent error:", repr(e))
        intent = "normal"

    # 5) Clarify 必要判定
    try:
        needs_clarify = _should_clarify(intent, raw_user_text, tb_summary)
    except Exception as e:
        print("[Interface_Box] should_clarify error:", repr(e))
        needs_clarify = False

    # 6) 統一パケット構築
    pkt = InputPacket(
        context_key=context_key,
        raw_user_text=raw_user_text,
        intent=intent,
        needs_clarify=needs_clarify,
        state_hint=state_hint,
        tb_hint=tb_hint,
    )

    return pkt