"""
[MODULE CONTRACT]
NAME: ovv_call
ROLE: CORE_CALL_LAYER (Layer 3 – Ovv-Core 呼び出し)

INPUT:
  - context_key: int
  - input_packet: dict  # Interface_Box が生成した論理入力パケット

OUTPUT:
  - raw_answer: str  # Stabilizer が解析する生テキスト（[FINAL] を含みうる）

MUST:
  - inject(SYSTEM_PROMPT)
  - inject(OVV_CORE)
  - inject(OVV_EXTERNAL)
  - include(ThreadBrainPrompt)
  - include(ThreadBrainScoring)
  - include(StateHint)
  - include(RuntimeMemory)
  - append_runtime_memory("assistant", raw_answer)
  - preserve_message_order

MUST_NOT:
  - mutate(input_packet)
  - mutate(thread_brain)
  - return(JSON_struct)
  - return(non_FINAL_format)
  - bypass(Stabilizer)
  - violate BIS dependency (IFACE → CORE → STAB)

DEPENDENCY:
  - database.pg (append_runtime_memory only)
  - openai.ChatCompletion
  - ovv.core_loader (CORE / EXTERNAL ロード)
"""

# ============================================================
# [CORE] imports
# ============================================================
from typing import List, Dict, Any
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ — pure data loader (副作用なし)
from ovv.core_loader import load_core, load_external

# PG — runtime_memory append のみ許可（BIS の PERSIST 下位依存）
import database.pg as db_pg


# ============================================================
# [CORE] Load Core / External / Soft-Core (思想)
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
7. MAY trigger CDC only in structured form.
""".strip()


# ============================================================
# [CORE] SYSTEM_PROMPT（BIS 準拠 Final-Only モデル）
# ============================================================
SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。
あなたは内部で構造化思考を行いつつ、
ユーザーへは常に [FINAL] のみを返してください。

Ovv Soft-Core:
{OVV_SOFT_CORE}

重要ルール:
- 内部の検討ログや案を露出しない。
- JSON / YAML / dict / array などの構造化出力は禁止。
- ユーザーが JSON を要求した場合のみ例外的に応じるが、
  原則は [FINAL] 内に自然文で説明する。
- ThreadBrain（TB）情報は文脈・長期目標の参照として扱う。
""".strip()


# ============================================================
# [CORE] OpenAI Client
# ============================================================
client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# [CORE] normalize message content for v1 SDK
# ============================================================
def _normalize_openai_content(content) -> str:
    """
    BIS: Stabilizer に渡す前の正規化責務。
    OpenAI v1 の ChatCompletionMessage.content は:
      - str
      - list[str]
      - list[dict]（structured message）
    のいずれにもなりうる。

    Stabilizer が「str only」前提なので、ここで flatten して返す。
    """

    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        merged = []
        for chunk in content:
            # list[str]
            if isinstance(chunk, str):
                merged.append(chunk)
            # list[dict]
            elif isinstance(chunk, dict):
                # OpenAI structured messages often use {"text": "..."}
                if "text" in chunk:
                    merged.append(chunk["text"])
                elif "content" in chunk:
                    merged.append(chunk["content"])
                else:
                    merged.append(str(chunk))
            else:
                merged.append(str(chunk))
        return "\n".join(merged)

    return str(content)


# ============================================================
# [CORE] call_ovv — main entry
# ============================================================
def call_ovv(context_key: int, input_packet: Dict[str, Any]) -> str:
    """
    BIS Layer 3（Core 呼び出し）の唯一のエントリ。
    - Interface Layer → Core Layer の接続。
    - Stabilizer に渡すための raw_answer を生成して返す。
    """

    # ---------------------------
    # [CORE] Unpack InputPacket
    # ---------------------------
    user_text: str = input_packet.get("user_text", "") or ""
    runtime_mem: List[Dict[str, Any]] = input_packet.get("runtime_memory", []) or []
    tb_prompt: str = input_packet.get("tb_prompt", "") or ""
    tb_scoring: str = input_packet.get("tb_scoring", "") or ""
    state_hint: Dict[str, Any] = input_packet.get("state", {}) or {}

    # ---------------------------
    # [CORE] Build messages
    # ---------------------------
    messages: List[Dict[str, str]] = []

    # SYSTEM + Core Philosophy
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # Thread Brain prompt
    if tb_prompt:
        messages.append({"role": "system", "content": tb_prompt})

    # TB Scoring
    if tb_scoring:
        messages.append({"role": "system", "content": tb_scoring})

    # State Hint
    if state_hint:
        state_text = "[STATE_HINT]\n" + json.dumps(state_hint, ensure_ascii=False)
        messages.append({"role": "system", "content": state_text})

    # Runtime Memory
    for m in runtime_mem:
        role = m.get("role", "user")
        content = m.get("content", "")
        if content:
            messages.append({"role": role, "content": content})

    # Current user message
    messages.append({"role": "user", "content": user_text})

    # ---------------------------
    # [CORE] Call OpenAI
    # ---------------------------
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
        )

        raw_message_obj = resp.choices[0].message
        raw_content = _normalize_openai_content(raw_message_obj.content)

        # ---------------------------
        # [PERSIST] append_runtime_memory
        # ---------------------------
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw_content,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] warning: append_runtime_memory failed:", repr(e))

        # ---------------------------
        # [CORE] return raw (Stabilizer が FINAL を抽出)
        # ---------------------------
        return raw_content[:1900]

    except Exception as e:
        print("[call_ovv fatal]", repr(e))

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。時間をおいて再度お試しください。"