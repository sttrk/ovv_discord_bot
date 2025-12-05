# ovv/ovv_call.py
# Ovv Call Layer - September Stable Edition + State Manager v1 対応

from typing import List, Optional, Dict
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# ============================================================
# Load Core / External
# ============================================================
from ovv.core_loader import load_core, load_external

OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

# ============================================================
# Soft-Core
# ============================================================
OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary
2. MUST use Clarify only when needed
3. MUST avoid hallucination
4. MUST respect boundaries
5. SHOULD decompose → reconstruct
6. MUST NOT phase-mix
7. MAY trigger CDC sparingly
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上の Ovv です。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv: Ovv Main Logic
#   - state_hint を受け取り、プロンプトに埋め込む
# ============================================================
def call_ovv(
    context_key: int,
    text: str,
    recent_mem: List[dict],
    state_hint: Optional[Dict] = None,
) -> str:
    """
    context_key / recent_mem / state_hint を元に Ovv コアを呼び出す。
    state_hint があれば、SYSTEM レベルで LLM に共有する。
    """

    msgs: List[Dict] = []

    # Soft-Core を含むメイン SYSTEM
    msgs.append({"role": "system", "content": SYSTEM_PROMPT})

    # State hint（あれば JSON で渡す）
    if state_hint:
        hint_json = json.dumps(state_hint, ensure_ascii=False)
        msgs.append({
            "role": "system",
            "content": f"[STATE_HINT] {hint_json}"
        })

    # Ovv Core / External
    msgs.append({"role": "assistant", "content": OVV_CORE})
    msgs.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 過去メモリ
    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    # 現在のユーザ入力
    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        return res.choices[0].message.content.strip()[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        return "Ovv コア処理中にエラーが発生しました。"
