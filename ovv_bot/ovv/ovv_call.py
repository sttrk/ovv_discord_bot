# ovv/ovv_call.py
# Ovv Call Layer - A5 Reasoning Upgrade v1 (with Interface_Box v0.3)

from typing import List

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# Thread Brain / Memory 参照用（循環依存なし）
import database.pg as db_pg

# Interface_Box（BIS: I 層）
from ovv.interface_box import build_messages


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
# call_ovv: A5 強化版（Interface_Box 統合）
# ============================================================
def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    """
    A5: Ovv 推論呼び出し
    - Soft-Core / Core / External / Thread Brain / Runtime Memory を統合して LLM に渡す。
    - messages 構築は Interface_Box(build_messages) に一元化。
    - 返却値は生テキストだが、[FINAL] 付きで返すことを期待。
    """

    # 1) Thread Brain 読み出し（あれば）
    try:
        tb = db_pg.load_thread_brain(context_key)
    except Exception as e:
        print("[call_ovv] thread_brain load error:", repr(e))
        tb = None

    # 2) Interface_Box で messages 構築
    messages = build_messages(
        context_key=context_key,
        user_text=text,
        recent_mem=recent_mem,
        system_prompt=SYSTEM_PROMPT,
        ovv_core=OVV_CORE,
        ovv_external=OVV_EXTERNAL,
        thread_brain=tb,
    )

    # 3) OpenAI 呼び出し
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = res.choices[0].message.content.strip()

        # 4) runtime_memory に assistant 発話を保存
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # そのまま返す（最終フィルタは bot.py 側で [FINAL] 抽出）
        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        try:
            db_pg.log_audit(
                "openai_error",
                {
                    "context_key": context_key,
                    "user_text": text[:500],
                    "error": repr(e),
                },
            )
        except Exception:
            pass

        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"
