# ============================================================
# [MODULE CONTRACT]
# NAME: ovv_call
# ROLE: CORE-CALL (Layer 3 — Ovv-Core Caller)
#
# INPUT:
#   context_key: int
#   input_packet: dict    # Interface_Box v1.0 が生成する論理入力
#
# OUTPUT:
#   raw_answer: str        # Stabilizer が [FINAL] 抽出を行う前の LLM 生出力
#
# MUST:
#   - inject(SYSTEM_PROMPT)
#   - inject(OVV_CORE)
#   - inject(OVV_EXTERNAL)
#   - include(ThreadBrainPrompt, ThreadBrainScoring)
#   - include(StateHint)
#   - include(RuntimeMemory)
#   - append_runtime_memory
#   - preserve_message_order
#
# MUST_NOT:
#   - mutate(input_packet)
#   - return JSON object
#   - return non-[FINAL]構造（整形は Stabilizer が行う）
#   - alter ThreadBrain
#
# DEPENDENCY:
#   - Layer 2 Interface Box（input_packet）
#   - Layer 5 Persistence（append_runtime_memory / audit_log）
#   - Layer 3 Core（OpenAI LLM）
# ============================================================

from typing import List, Dict, Any
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# ============================================================
# [CORE] Load Core / External Logic
# ============================================================
from ovv.core_loader import load_core, load_external
import database.pg as db_pg


# ============================================================
# [CORE] Embedded Specification
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
7. MAY trigger CDC (self-audit) sparingly.
""".strip()


# ============================================================
# [CORE] SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

Ovv Soft-Core:
{OVV_SOFT_CORE}

重要ルール:
- 内部思考や候補案は出さず、[FINAL] のみを返す。
- JSON / 配列 / YAML / XML などの構造は出力禁止。
- Thread Brain の high_level_goal, next_actions などは尊重するが、
  最新のユーザー意図が常に最優先。
""".strip()


# ============================================================
# [CORE] OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# [CORE] call_ovv — Interface → Core → RawOutput
# ============================================================
def call_ovv(context_key: int, input_packet: Dict[str, Any]) -> str:
    """
    [CORE] Ovv-Core 呼び出し（純粋思考レイヤ）
    - Interface Box v1.0 の出力（input_packet）を前提に使用する。
    - message ordering を保持し、Stabilizer 前段の「生出力」を返す。
    """

    # ------------------------------
    # [IFACE] Read InterfacePacket
    # ------------------------------
    # Interface_Box v1.0準拠: user_text → "input"
    user_text: str = input_packet.get("input", "") or ""

    runtime_mem: List[Dict[str, Any]] = input_packet.get("memory", []) or []
    tb_prompt: str = input_packet.get("tb_prompt", "") or ""
    tb_scoring: str = input_packet.get("tb_scoring", "") or ""
    state_hint: Dict[str, Any] = input_packet.get("state", {}) or {}

    # ------------------------------
    # [CORE] Build LLM Messages
    # ------------------------------
    messages: List[Dict[str, str]] = []

    # System embedding
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # TB prompt
    if tb_prompt:
        messages.append({"role": "system", "content": tb_prompt})

    # TB scoring
    if tb_scoring:
        messages.append({"role": "system", "content": tb_scoring})

    # State hint
    if state_hint:
        state_text = "[STATE_HINT]\n" + json.dumps(state_hint, ensure_ascii=False)
        messages.append({"role": "system", "content": state_text})

    # Runtime memory
    for m in runtime_mem:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if content:
            messages.append({"role": role, "content": content})

    # Current user
    messages.append({"role": "user", "content": user_text})

    # ------------------------------
    # [CORE] Call LLM
    # ------------------------------
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
        )

        raw = resp.choices[0].message.content or ""

        # --------------------------
        # [PERSIST] Save memory
        # --------------------------
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # --------------------------
        # [CORE → STAB] Return as-is
        # --------------------------
        return raw[:1900]  # Discord 安全圏

    except Exception as e:
        print("[call_ovv error]", repr(e))

        try:
            db_pg.log_audit(
                "openai_error",
                {
                    "context_key": context_key,
                    "user_text": user_text[:500],
                    "error": repr(e),
                },
            )
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。"