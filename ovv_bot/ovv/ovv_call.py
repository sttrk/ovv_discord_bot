# ovv/ovv_call.py
# Ovv Call Layer - BIS 対応版（InputPacket 統合）

from typing import List, Dict, Any, Optional
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
6. MUST NOT phase-mix (設計・推論・出力を混ぜない)。
7. MAY trigger CDC (自己監査) but sparingly and構造化して扱う。
""".strip()


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
- 出力は必ず次の形式に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

制約:
- ユーザーの業務・開発に直接役立つことを最優先とし、不要な雑談や冗長な前置きは避ける。
- Thread Brain / state_hint に記録された high_level_goal / next_actions / history_digest などは、
  「長期的な方針」や「これまでの合意事項」として尊重しつつ、
  矛盾があれば最新のユーザー発言を優先する。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示する。
""".strip()

# ============================================================
# OpenAI Client
# ============================================================

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv (InputPacket 版)
# ============================================================

def call_ovv(context_key: int, packet: Dict[str, Any]) -> str:
    """
    BIS 対応版 Ovv 呼び出し。
    Boundary_Gate → Interface_Box から渡される InputPacket を前提とする。

    packet 期待フォーマット:
    {
        "user_text": str,
        "runtime_memory": List[dict],
        "runtime_memory_text": str,
        "thread_brain": dict | None,
        "thread_brain_prompt": str,
        "tb_scoring_hint": str,
        "state_hint": dict | None,
    }
    """

    user_text: str = packet.get("user_text") or ""
    runtime_memory: List[dict] = packet.get("runtime_memory") or []
    tb_prompt: str = packet.get("thread_brain_prompt") or ""
    tb_scoring_hint: str = packet.get("tb_scoring_hint") or ""
    state_hint: Optional[dict] = packet.get("state_hint")

    messages: List[Dict[str, str]] = []

    # 1) System + Soft-Core + TB-Scoring
    system_block = SYSTEM_PROMPT
    if tb_scoring_hint:
        system_block = system_block + "\n\n" + tb_scoring_hint

    messages.append({"role": "system", "content": system_block})
    messages.append({"role": "assistant", "content": OVV_CORE})
    messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 2) Thread Brain 要約（あれば）
    if tb_prompt:
        messages.append({
            "role": "system",
            "content": f"[ThreadBrain]\n{tb_prompt}"
        })

    # 3) State Hint（数字カウントなど軽量ステート）
    if state_hint:
        try:
            state_json = json.dumps(state_hint, ensure_ascii=False)
        except Exception:
            state_json = str(state_hint)
        messages.append({
            "role": "system",
            "content": f"[StateHint]\n{state_json}"
        })

    # 4) 直近 runtime_memory（そのまま user/assistant として再注入）
    for m in runtime_memory[-20:]:
        role = m.get("role") or "user"
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role not in ("user", "assistant", "system"):
            role = "user"
        messages.append({"role": role, "content": content})

    # 5) 今回ユーザー入力
    messages.append({"role": "user", "content": user_text})

    # 6) OpenAI 呼び出し
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = (res.choices[0].message.content or "").strip()
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

        raw = "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"

    # 7) runtime_memory に assistant 発話を保存
    if raw:
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

    # Discord 1 メッセージ上限対策（安全側で truncate）
    return raw[:1900]