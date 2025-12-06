# ovv/ovv_call.py
# Ovv Call Layer - BIS + A5 統合版
#
# 目的:
# - InputPacket（Interface_Box で構築）を受け取り、Ovv Core（LLM）を呼び出す。
# - Soft-Core / External Contract / Thread Brain / State を統合した messages を組み立てる。
#
# 責務:
# - OpenAI API 呼び出しと messages 構築のみ。
# - DB I/O は database.pg の関数に委譲（直接 import するが責務は限定）。

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
- 出力は必ず次の形式に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

制約:
- ユーザーの業務・開発に直接役立つことを最優先とし、不要な雑談や冗長な前置きは避ける。
- Thread Brain に記録された high_level_goal / next_actions / history_digest は、
  「長期的な方針」や「これまでの合意事項」として尊重しつつ、
  矛盾があれば最新のユーザー発言を優先する。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示する。
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
            "content": f"[ThreadBrain]\n{tb_prompt}",
        })

    # 3) TB-Scoring（優先ルールヒント）
    if tb_scoring:
        messages.append({
            "role": "system",
            "content": tb_scoring,
        })

    # 4) State Hint
    if state_hint:
        try:
            state_text = json.dumps(state_hint, ensure_ascii=False)
        except Exception:
            state_text = str(state_hint)
        messages.append({
            "role": "system",
            "content": f"[StateHint]\n{state_text}",
        })

    # 5) 直近メモリ（PG runtime_memory）から 20 件まで
    for m in runtime_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        if role not in ("user", "assistant", "system"):
            role = "user"
        messages.append({"role": role, "content": content})

    # 6) 今回ユーザー入力
    messages.append({"role": "user", "content": user_text})

    # 7) OpenAI 呼び出し
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = res.choices[0].message.content.strip()

        # runtime_memory に assistant 発話を保存
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
            db_pg.log_audit("openai_error", {
                "context_key": context_key,
                "user_text": user_text[:500],
                "error": repr(e),
            })
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"