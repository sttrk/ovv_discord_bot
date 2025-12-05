# ovv/ovv_call.py
# Ovv Call Layer - A5 Reasoning Upgrade + TB-Scoring Integration

from typing import List, Optional
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Thread Brain / Memory 参照用（循環依存なし）
import database.pg as db_pg

# ThreadBrain → テキスト変換 / スコアリング層
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
- Thread Brain に記録された high_level_goal / next_actions / history_digest / constraints / unresolved / decisions は、
  「長期的な方針」や「これまでの合意事項」として尊重しつつ、
  矛盾があれば最新のユーザー発言を優先する。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示する。
- TB-Scoring で与えられた優先ルールがあれば、それを強く尊重する。
""".strip()


# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# call_ovv: A5 + ThreadBrainAdapter + TB-Scoring 統合版
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    A5: Ovv 推論呼び出し（BIS 対応）
    - Soft-Core / Core / External / Thread Brain / TB-Scoring / Runtime Memory を統合して LLM に渡す。
    - 返却値は生テキストだが、[FINAL] 付きで返すことを期待。
    """

    messages: List[dict] = []

    # --------------------------------------------------------
    # 1) System + Soft-Core + Core / External 契約
    # --------------------------------------------------------
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    # Core / External は assistant ロールで「事前合意済みコンテキスト」として渡す
    if OVV_CORE:
        messages.append({"role": "assistant", "content": OVV_CORE})
    if OVV_EXTERNAL:
        messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # --------------------------------------------------------
    # 2) Thread Brain + TB-Scoring（あれば）
    # --------------------------------------------------------
    tb_summary: Optional[dict] = None
    try:
        tb_summary = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[call_ovv] thread_brain load error:", repr(e))
        tb_summary = None

    if tb_summary:
        # Long Context Injection（構造化テキスト）
        tb_text = build_tb_prompt(tb_summary)
        if tb_text:
            messages.append({
                "role": "system",
                "content": f"[ThreadBrain]\n{tb_text}"
            })

        # Scoring Layer（優先ルール）
        scoring_text = build_scoring_prompt(tb_summary)
        if scoring_text:
            messages.append({
                "role": "system",
                "content": scoring_text
            })

    # --------------------------------------------------------
    # 3) 直近メモリ（PG runtime_memory）から 20 件まで
    # --------------------------------------------------------
    for m in recent_mem[-20:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        # 念のため空はスキップ
        if not content:
            continue
        # Discord / Ovv 双方で想定される role だけ許可
        if role not in ("user", "assistant", "system"):
            role = "user"
        messages.append({"role": role, "content": content})

    # --------------------------------------------------------
    # 4) 今回ユーザー入力
    # --------------------------------------------------------
    messages.append({"role": "user", "content": text})

    # --------------------------------------------------------
    # 5) OpenAI 呼び出し
    # --------------------------------------------------------
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

        # そのまま返す（最終フィルタは Boundary_Gate / Stabilizer 側で [FINAL] 抽出）
        return raw[:1900]

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