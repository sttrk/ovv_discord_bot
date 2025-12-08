# ============================================================
# [MODULE CONTRACT]
# NAME: ovv_call
# LAYER: BIS-3 (Core Call Layer)
#
# ROLE:
#   - Interface_Box が組み立てた iface_packet を元に LLM を呼び出す。
#   - SYSTEM_PROMPT / Core Spec / External Contract / TB / State / Memory を統合し、
#     1 回の chat.completions によって raw_answer を得る。
#
# INPUT:
#   - context_key: int
#   - iface_packet: dict
#
# OUTPUT:
#   - raw_answer: str（Stabilizer が [FINAL] を抽出）
#
# MUST:
#   - ChatCompletionMessage を dict のように subscript しない
#   - runtime_memory に assistant 応答を append する
#
# MUST NOT:
#   - iface_packet を変更しない
#   - ThreadBrain を mutate しない
#   - Discord / Notion / 直接 I/O に触れない（print は除く）
# ============================================================

from typing import List, Dict, Any
import json

from openai import OpenAI
from config import OPENAI_API_KEY

from ovv.core_loader import load_core, load_external
import database.pg as db_pg


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
6. MUST NOT phase-mix.
7. MAY trigger CDC but sparingly and structured.
""".strip()

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
# call_ovv
# ============================================================

def call_ovv(context_key: int, iface_packet: Dict[str, Any]) -> str:
    """
    Interface_Box → Ovv-Core 呼び出し
    - GPT-4.1 ChatCompletionMessage の正しい扱い
    - Stabilizer が [FINAL] を抽出できるよう raw を返す
    """

    user_text: str = iface_packet.get("input", "") or ""
    runtime_mem: List[Dict[str, Any]] = iface_packet.get("memory", []) or []
    tb_prompt: str = iface_packet.get("tb_prompt", "") or ""
    tb_scoring: str = iface_packet.get("tb_scoring", "") or ""
    state_hint: Dict[str, Any] = iface_packet.get("state", {}) or {}

    # --------------------------------------------------------
    # Message Assembly
    # --------------------------------------------------------

    messages: List[Dict[str, str]] = []

    # SYSTEM
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # CORE / EXTERNAL
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

    # RUNTIME MEMORY
    for m in runtime_mem:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if content:
            messages.append({"role": role, "content": content})

    # CURRENT USER MESSAGE
    messages.append({"role": "user", "content": user_text})

    print(f"[BIS-3] CoreCall: messages={len(messages)} (ctx={context_key})")

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
        )

        # ChatCompletionMessage（辞書ではない）
        msg = resp.choices[0].message
        raw = msg.content or ""

        print(f"[BIS-3] CoreCall: LLM responded (len={len(raw)})")

        # runtime_memory append（安全側）
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[BIS-3] runtime_memory append error:", repr(e))

        return raw[:1900]

    except Exception as e:
        print("[BIS-3] CoreCall ERROR:", repr(e))
        try:
            db_pg.log_audit(
                "openai_error",
                {
                    "context_key": context_key,
                    "error": repr(e),
                    "user_text": user_text[:200],
                },
            )
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。時間を置いて再度お試しください。"