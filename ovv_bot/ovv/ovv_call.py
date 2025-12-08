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
#   - raw_answer: str
#
# MUST:
#   - inject SYSTEM_PROMPT / OVV_CORE / OVV_EXTERNAL
#   - include ThreadBrainPrompt / TB-Scoring / StateHint / RuntimeMemory
#   - append assistant reply to runtime_memory
#   - return raw LLM output (Stabilizer が [FINAL] を抽出)
#
# MUST NOT:
#   - modify iface_packet
#   - return JSON unlessユーザーが明示要求
#   - mutate ThreadBrain
# ============================================================

from typing import List, Dict, Any
import json
from openai import OpenAI

from config import OPENAI_API_KEY

# Core / External spec loader
from ovv.core_loader import load_core, load_external

# PERSIST（最小限の append のみ許可）
import database.pg as db_pg

# ============================================================
# Load Core / External (Static)
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
7. MAY trigger CDC but sparingly.
""".strip()

# ============================================================
# SYSTEM PROMPT（BIS-Compatible）
# ============================================================

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- Thread Brain（TB）/ Runtime Memory を参照しながら最終回答だけを返す。
- 思考過程・草案・内部ログは出さない。
- 出力は必ず [FINAL] セクション 1本だけ。

Ovv Soft-Core:
{OVV_SOFT_CORE}

出力ルール:
- 必ず `[FINAL]` ブロックのみを返す。
- JSON や辞書形式は禁止（ユーザーが要求した場合のみ可）。
- 内部思考や下書きは禁止。
""".strip()

# ============================================================
# OpenAI Client
# ============================================================

client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# call_ovv (BIS Layer 3)
# ============================================================

def call_ovv(context_key: int, iface_packet: Dict[str, Any]) -> str:
    """
    Interface_Box → Ovv-Core 呼び出し
    - GPT-4.1 の公式フォーマットに完全準拠
    - ChatCompletionMessage を subscript しない
    - Stabilizer に渡すため raw のまま返す
    """

    # --------------------------------------------------------
    # Unpack iface_packet（責務：値を読むだけ / 変更禁止）
    # --------------------------------------------------------
    user_text: str = iface_packet.get("input", "") or ""
    runtime_mem: List[Dict[str, Any]] = iface_packet.get("memory", []) or []
    tb_prompt: str = iface_packet.get("tb_prompt", "") or ""
    tb_scoring: str = iface_packet.get("tb_scoring", "") or ""
    state_hint: Dict[str, Any] = iface_packet.get("state", {}) or {}

    # --------------------------------------------------------
    # Message Assembly（GPT-4.1 形式）
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
            "content": "[STATE_HINT]\n" + json.dumps(state_hint, ensure_ascii=False)
        })

    # RUNTIME MEMORY
    for m in runtime_mem:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if content:
            messages.append({"role": role, "content": content})

    # CURRENT USER MESSAGE
    messages.append({"role": "user", "content": user_text})

    # --------------------------------------------------------
    # Call LLM（GPT-4.1 正式対応）
    # --------------------------------------------------------

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages
        )

        # GPT-4.1 の返り値
        msg = resp.choices[0].message          # ← ChatCompletionMessage（辞書ではない）
        raw = msg.content or ""                # ← 正しい取り出し方

        # runtime_memory append（安全サブルーチン）
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] runtime_memory append error:", repr(e))

        # Stabilizer が扱えるよう raw を返す
        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))

        # Audit log（失敗しても Ovv は止めない）
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
