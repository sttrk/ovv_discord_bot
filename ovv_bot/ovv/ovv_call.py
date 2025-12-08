# ============================================================
# [MODULE CONTRACT]
# NAME: ovv_call
# ROLE: OvvCoreCallLayer (BIS Layer 3)
#
# INPUT:
#   - context_key: int
#   - iface_packet: dict (Interface_Box 出力)
#
# OUTPUT:
#   - raw_answer: str（Stabilizer が [FINAL] を抽出）
#
# MUST:
#   - SYSTEM_PROMPT / OVV_CORE / OVV_EXTERNAL を注入する
#   - ThreadBrainPrompt / TB-Scoring / StateHint / RuntimeMemory を含める
#   - assistant 応答を runtime_memory に append する
#   - ChatCompletionMessage を辞書として扱わない（subscript しない）
#
# MUST NOT:
#   - iface_packet を変更してはならない
#   - JSON を勝手に返してはならない
#   - ThreadBrain を mutate してはならない
# ============================================================

from typing import List, Dict, Any
import json
from openai import OpenAI

from config import OPENAI_API_KEY

# Core / External spec loader
from ovv.core_loader import load_core, load_external

# Persistence（最小限の append のみ許可）
import database.pg as db_pg


# ============================================================
# Load Core / External
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
6. MUST NOT phase-mix.
7. MAY trigger CDC but sparingly and structured.
""".strip()


# ============================================================
# SYSTEM PROMPT（完全BIS対応）
# ============================================================

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- ThreadBrain（TB）と runtime_memory を参照し、最終回答だけを [FINAL] として返す。
- 内部思考・草案・プロセス・検討メモは出力してはならない。

Ovv Soft-Core:
{OVV_SOFT_CORE}

出力規則（絶対遵守）:
- 出力は必ず `[FINAL]` ブロック 1つだけ。
- JSON / YAML / dict / XML 等の構造化出力は禁止（ユーザーが明示要求した場合のみ例外）。
- 途中経過・思考過程・推論理由は禁止。
- 過去メッセージが JSON でも、あなた自身は JSON を返さない。

目的:
- 高い安定性と可読性を保持しつつ、OvvCore と External Contract の仕様に従う。
""".strip()


# ============================================================
# OpenAI Client
# ============================================================

client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv（BIS Layer 3 の本体）
# ============================================================

def call_ovv(context_key: int, iface_packet: Dict[str, Any]) -> str:
    """
    Interface_Box → Ovv-Core 呼び出し
    - GPT-4.1 ChatCompletionMessage の正しい扱い
    - Stabilizer が [FINAL] を抽出できるよう raw を返す
    """

    # --------------------------------------------------------
    # Unpack（読み取りのみ、変更禁止）
    # --------------------------------------------------------
    user_text: str = iface_packet.get("input", "") or ""
    runtime_mem: List[Dict[str, Any]] = iface_packet.get("memory", []) or []
    tb_prompt: str = iface_packet.get("tb_prompt", "") or ""
    tb_scoring: str = iface_packet.get("tb_scoring", "") or ""
    state_hint: Dict[str, Any] = iface_packet.get("state", {}) or {}

    # --------------------------------------------------------
    # Message Assembly (GPT-4.1)
    # --------------------------------------------------------

    messages: List[Dict[str, str]] = []

    # SYSTEM
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # CORE / EXTERNAL を assistant ロールで注入
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # TB PROMPT
    if tb_prompt:
        messages.append({"role": "system", "content": tb_prompt})

    # TB-SCORING
    if tb_scoring:
        messages.append({"role": "system", "content": tb_scoring})

    # STATE HINT
    if state_hint:
        messages.append({
            "role": "system",
            "content": "[STATE_HINT]\n" + json.dumps(state_hint, ensure_ascii=False),
        })

    # runtime_memory
    for m in runtime_mem:
        r = m.get("role") or "user"
        c = m.get("content") or ""
        if c:
            messages.append({"role": r, "content": c})

    # current user message
    messages.append({"role": "user", "content": user_text})

    # --------------------------------------------------------
    # Call LLM（ChatCompletionMessage 対応）
    # --------------------------------------------------------

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
        )

        # ChatCompletionMessage（辞書アクセス禁止）
        msg = resp.choices[0].message
        raw = msg.content or ""

        # --------------------------------------------------------
        # runtime_memory へ append（副作用 OK）
        # --------------------------------------------------------
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))

        # audit（失敗しても止めない）
        try:
            db_pg.log_audit(
                "openai_error",
                {"context_key": context_key, "user_text": user_text, "error": repr(e)},
            )
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。再試行してください。"