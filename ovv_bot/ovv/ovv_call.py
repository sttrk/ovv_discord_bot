# ovv/ovv_call.py
# Ovv Call Layer - BIS-I Edition v1 (A5 Reasoning Upgrade + TB/State/Scoring)

from typing import List, Optional, Dict
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Thread Brain / Memory 参照用（循環依存なし）
import database.pg as db_pg

# 軽量ステート判定 / ThreadBrain アダプタ / スコアリング
from ovv.state_manager import decide_state
from ovv.threadbrain_adapter import build_tb_prompt
from ovv.tb_scoring import build_scoring_prompt


# ============================================================
# Core / External / Soft-Core
# ============================================================

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination.
4. MUST respect scope boundaries.
5. SHOULD decompose → reconstruct for stability.
6. MUST NOT phase-mix（設計・推論・出力を混ぜない）。
7. MAY trigger CDC（自己監査）but sparingly and 構造化して扱う。
""".strip()


# ============================================================
# SYSTEM_PROMPT（A5 + BIS-I: ThreadBrain / State / FINAL-only）
# ============================================================

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- ユーザーの継続タスクを支援し、必要に応じて過去文脈・Thread Brain を参照しながら回答します。
- UI 版 Ovv と同じ哲学 (Ovv Soft-Core) を保持しつつ、Discord 版として外部システム連携を前提とします。

Ovv Soft-Core:
{OVV_SOFT_CORE}

挙動ルール:
- 「思考の構造化」は内部で行い、ユーザーには最終結論だけを返します。
- 内部思考や検討メモ、ドラフト、候補案をそのまま出力してはなりません。
- 出力は必ず次の形式に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

Thread Brain / 状態関連:
- もし [ThreadBrain] や [TB-Scoring]、[StateHint] が与えられている場合、それらは
  「長期的な方針」「優先順位」「会話モード」に関するヒントです。
- ただし、常に「最新のユーザー発言」を最優先し、矛盾する場合はユーザー発言を優先します。
- Thread Brain の high_level_goal / next_actions / unresolved は、
  方針・未解決事項として尊重しつつ、今の質問に不要な要素まで引き込まないこと。

出力ポリシー:
- 冗長な前置きや不要なメタ説明は避け、実務に直結する内容を優先する。
- コードや設計に関する回答では、関数名・ファイル名・責務境界をできるだけ明示する。
- 曖昧な点が回答品質に重大な影響を与える場合のみ、簡潔に Clarify を求める。
""".strip()


# ============================================================
# OpenAI Client
# ============================================================

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# internal: recent_mem のサニタイズ
# ============================================================

def _normalize_recent_mem(recent_mem: List[dict]) -> List[Dict[str, str]]:
    """
    recent_mem（PG runtime_memory）を LLM 用に整形する。
    - role は user / assistant のみを残す
    - content が空のものは捨てる
    """
    out: List[Dict[str, str]] = []
    for m in recent_mem:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if not content:
            continue
        if role not in ("user", "assistant"):
            # 想定外ロールは user 扱いに倒す（安全側）
            role = "user"
        out.append({"role": role, "content": content})
    return out


# ============================================================
# call_ovv: BIS-I 中核
# ============================================================

def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    A5 + BIS-I:
    - Soft-Core / Core / External / Thread Brain / State / Runtime Memory を統合して LLM に渡す。
    - 返却値は生テキストだが、[FINAL] 付きで返すことを期待（最終抽出は bot.py 側）。
    """

    # --------------------------------------------------------
    # 0) 基本チェック
    # --------------------------------------------------------
    safe_recent = _normalize_recent_mem(recent_mem or []);

    # --------------------------------------------------------
    # 1) Thread Brain の取得 + アダプト
    # --------------------------------------------------------
    tb_summary: Optional[dict] = None
    tb_prompt: str = ""
    tb_scoring: str = ""

    try:
        tb_summary = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[call_ovv] thread_brain load error:", repr(e))
        tb_summary = None

    if tb_summary:
        try:
            tb_prompt = build_tb_prompt(tb_summary) or ""
        except Exception as e:
            print("[call_ovv] build_tb_prompt error:", repr(e))
            tb_prompt = ""

        try:
            tb_scoring = build_scoring_prompt(tb_summary) or ""
        except Exception as e:
            print("[call_ovv] build_scoring_prompt error:", repr(e))
            tb_scoring = ""

    # --------------------------------------------------------
    # 2) 軽量ステート判定（数字ゲーム / task_hint など）
    #    ※ 現状 task_mode は False 固定（将来、bot 側から渡す想定）
    # --------------------------------------------------------
    state_hint: Optional[dict] = None
    try:
        state_hint = decide_state(
            context_key=context_key,
            user_text=text,
            recent_mem=safe_recent,
            task_mode=False,
        )
    except Exception as e:
        print("[call_ovv] decide_state error:", repr(e))
        state_hint = None

    # --------------------------------------------------------
    # 3) messages 構築（BIS-I: system → core/external → TB → state → context → user）
    # --------------------------------------------------------
    messages: List[dict] = []

    # system
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # core / external（assistant ロールで「既知コンテキスト」として渡す）
    if OVV_CORE:
        messages.append({"role": "assistant", "content": OVV_CORE})
    if OVV_EXTERNAL:
        messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # Thread Brain 情報
    if tb_prompt:
        messages.append({
            "role": "system",
            "content": f"[ThreadBrain]\n{tb_prompt}"
        })

    if tb_scoring:
        messages.append({
            "role": "system",
            "content": tb_scoring
        })

    # State hint
    if state_hint:
        try:
            state_json = json.dumps(state_hint, ensure_ascii=False)
        except Exception:
            state_json = str(state_hint)
        messages.append({
            "role": "system",
            "content": f"[StateHint]\n{state_json}"
        })

    # recent context（最大20件）
    for m in safe_recent[-20:]:
        messages.append({
            "role": m["role"],
            "content": m["content"],
        })

    # current user input
    messages.append({"role": "user", "content": text})

    # --------------------------------------------------------
    # 4) OpenAI 呼び出し
    # --------------------------------------------------------
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = res.choices[0].message.content.strip()

        # assistant 発話を runtime_memory に保存
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # bot 側で [FINAL] 抽出する前提で、そのまま返す（長さだけ安全に制限）
        if len(raw) > 1900:
            return raw[:1900]
        return raw

    except Exception as e:
        print("[call_ovv error]", repr(e))
        try:
            db_pg.log_audit("openai_error", {
                "context_key": context_key,
                "user_text": text[:500],
                "error": repr(e),
            })
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"
