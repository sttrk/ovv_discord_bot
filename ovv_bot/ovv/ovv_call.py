# [MODULE CONTRACT]
# NAME: ovv_call
# ROLE: OvvCoreCallLayer
#
# INPUT:
#   context_key: int
#   input_packet: dict
#
# OUTPUT:
#   raw_answer: str
#
# MUST:
#   - inject(SYSTEM_PROMPT)
#   - inject(OVV_CORE)
#   - inject(OVV_EXTERNAL)
#   - include(ThreadBrainPrompt)
#   - include(ThreadBrainScoring)
#   - include(StateHint)
#   - include(RuntimeMemory)
#   - append_runtime_memory
#   - preserve_message_order
#
# MUST_NOT:
#   - return(JSON_struct)
#   - return(non_FINAL_format)
#   - alter(input_packet)
#   - mutate(ThreadBrain)

from typing import List, Dict, Any
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Runtime Memory append（循環依存のない最小限 I/O）
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
6. MUST NOT phase-mix (設計・推論・出力を混ぜない)。
7. MAY trigger CDC (自己監査) but sparingly and構造化して扱う。
""".strip()

# ============================================================
# SYSTEM_PROMPT（A5: Final Only / Thread-Brain 対応）
# ============================================================
SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- ユーザーの継続タスクを支援し、必要に応じて過去文脈・Thread Brain を参照しながら回答します。
- UI 版 Ovv と同じ哲学 (Ovv Soft-Core) を保持しつつ、Discord 版として外部システムとの連携を前提とします。

Ovv Soft-Core:
{OVV_SOFT_CORE}

挙動上の重要ルール:
- あなたは「思考の構造化」は内部で行い、ユーザーには最終結論だけを返します。
- 内部思考や検討メモ、ドラフト、候補案をそのまま出力してはなりません。

出力形式ルール（最重要）:
- あなたの出力は、必ず次の形式【のみ】に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

禁止事項:
- 上記 [FINAL] ブロック以外のテキストを出力してはなりません。
- JSON / 配列 / Python 辞書 / YAML / XML など「機械可読な構造」を出力してはなりません。
- 出力全体を {{...}} で囲んだ JSON 形式や、
  "message": "...", "response": "..." などのキー付きオブジェクトとして返してはなりません。
- 入力や過去メッセージに JSON や {{ }}、"message": などが含まれていても、
  それらはあくまで文脈情報であり、
  あなた自身の出力は「自然文の [FINAL] ブロックのみ」でなければなりません。
- ユーザーから明示的に「JSON で出して」と指示されない限り、
  構造化データでの応答は禁止です。
  明示的に JSON が要求されたとしても、可能であれば
  「[FINAL] 内で JSON について説明する自然文」を優先してください。

制約:
- ユーザーの業務・開発に直接役立つことを最優先とし、不要な雑談や冗長な前置きは避けてください。
- Thread Brain に記録された high_level_goal / next_actions / history_digest は、
  「長期的な方針」や「これまでの合意事項」として尊重しつつ、
  矛盾があれば最新のユーザー発言を優先してください。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示してください。
""".strip()

# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv: InputPacket ベース
# ============================================================
def call_ovv(context_key: int, input_packet: Dict[str, Any]) -> str:
    """
    A5 + BIS: Ovv 推論呼び出し
    - InputPacket（interface_box.build_input_packet の返り値）を受け取る。
    - Soft-Core / Core / External / Thread Brain / State / Runtime Memory を統合して LLM に渡す。
    - 返却値は生テキストだが、[FINAL] セクション付きで返すことを期待。
    """

    user_text: str = input_packet.get("user_text", "") or ""
    runtime_mem: List[Dict[str, Any]] = input_packet.get("runtime_memory", []) or []
    tb_prompt: str = input_packet.get("tb_prompt", "") or ""
    tb_scoring: str = input_packet.get("tb_scoring", "") or ""
    # Interface_Box 側で "state" に統一済み
    state_hint: Dict[str, Any] = input_packet.get("state", {}) or {}

    messages: List[Dict[str, str]] = []

    # 1) System + Soft-Core / Core / External
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 2) Thread Brain（テキスト化されたもの）
    if tb_prompt:
        messages.append({
            "role": "system",
            "content": tb_prompt,
        })

    # 3) TB-Scoring（優先ルール・フォーカス）
    if tb_scoring:
        messages.append({
            "role": "system",
            "content": tb_scoring,
        })

    # 4) State Hint（会話状態の軽量メタ情報）
    if state_hint:
        state_text = "[STATE_HINT]\n" + json.dumps(state_hint, ensure_ascii=False)
        messages.append({
            "role": "system",
            "content": state_text,
        })

    # 5) Runtime Memory（過去会話）
    for m in runtime_mem:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if not content:
            continue
        messages.append({
            "role": role,
            "content": content,
        })

    # 6) 現在の user 発話
    messages.append({
        "role": "user",
        "content": user_text,
    })

    try:
        client = openai_client
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
        )
        raw = resp.choices[0].message.content or ""

        # runtime_memory への append はここでも行う（冗長だが安全側）
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # そのまま返す（最終フィルタは Stabilizer / bot.py 側で [FINAL] 抽出）
        return raw[:1900]

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

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"