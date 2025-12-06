# ovv/ovv_call.py
# Ovv Call Layer - BIS / A5 Integrated Edition
#
# 役割:
#   - Boundary_Gate → Interface_Box で整形された InputPacket を受け取り、
#     Ovv Core（LLM）を呼び出して生テキストを返す。
#   - ThreadBrain（長期文脈）と TB-Scoring（優先ルール）を統合して
#     messages を構成する。
#
# 前提:
#   - bot.py からは以下の形で呼ばれる:
#       raw_ans = call_ovv(context_key, input_packet)
#
#   - input_packet の構造（interface_box.py で定義）:
#       {
#         "user_text": str,
#         "runtime_memory": List[dict],
#         "thread_brain_text": str,
#         "scoring_hint": str,
#         "state_hint": Optional[dict],
#       }

from typing import List, Dict, Any, Optional
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# Core / External ローダ
from ovv.core_loader import load_core, load_external

# runtime_memory への追記・監査ログ用
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
6. MUST NOT phase-mix（設計・推論・出力を混ぜない）。
7. MAY trigger CDC（自己監査） but sparingly and構造化して扱う。
""".strip()


# ============================================================
# SYSTEM_PROMPT（A5 + BIS + ThreadBrain/Scoring 統合）
# ============================================================
SYSTEM_PROMPT = f"""
あなたは Discord 上で動作する「Ovv」です。

役割:
- ユーザーの継続タスクを支援し、必要に応じて過去文脈・Thread Brain を参照しながら回答します。
- UI 版 Ovv と同じ哲学 (Ovv Soft-Core) を保持しつつ、
  Discord 版として外部システムとの連携を前提とします。

Ovv Soft-Core:
{OVV_SOFT_CORE}

この環境では、あなたは次の情報を System メッセージ経由で受け取ることがあります:

1. [ThreadBrain]
   - 高レベル目標 (high_level_goal)
   - 制約 (constraints)
   - 未解決項目 (unresolved)
   - 重要な決定事項 (decisions)
   - 次のアクション (next_actions)
   - 履歴要約など
   → これは「長期的な文脈 / 合意事項 / タスクの流れ」として扱うこと。

2. [TB-Scoring] （またはそれに準じるヒントメッセージ）
   - 「次の発話で守るべき優先ルール」を列挙したテキスト
   → ここに書かれた制約・優先順位を、通常の文脈よりも優先して尊重すること。

3. [StateHint]
   - mode（例: task / simple_sequence / idle など）
   - 必要に応じた簡易ヒント
   → これは「今の会話の状態」を教える情報であり、
      game 専用のロジックではなく汎用的な状態ヒントとして扱う。

挙動上の重要ルール:
- あなたは「思考の構造化」は内部で行い、ユーザーには最終結論だけを返します。
- 内部思考や検討メモ、ドラフト、候補案をそのまま出力してはなりません。
- 出力は必ず次の形式に従ってください:

[FINAL]
<ユーザーが読むための最終回答だけを書く。日本語で、簡潔かつ具体的に。>

制約:
- ユーザーの業務・開発に直接役立つことを最優先とし、
  不要な雑談や冗長な前置きは避ける。
- Thread Brain に記録された high_level_goal / next_actions / history_digest /
  決定事項(decisions) / 制約(constraints) は、
  「長期的な方針」や「これまでの合意事項」として尊重する。
  ただし矛盾があれば最新のユーザー発言を優先する。
- 未解決項目(unresolved) がある場合、それを解消する方向の提案・質問を優先する。
- 実装コードに関する回答では、なるべく具体的な関数名・ファイル名・責務境界を明示する。
""".strip()


# ============================================================
# OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# StateHint の簡易整形
# ============================================================
def _format_state_hint(state_hint: Optional[Dict[str, Any]]) -> str:
    """
    state_hint(dict) を System メッセージで扱いやすいテキストに整形する。
    例:
      {
        "mode": "task",
        "context_key": 12345,
        "reason": "task_channel",
      }
    """
    if not state_hint:
        return ""

    mode = state_hint.get("mode", "unknown")
    reason = state_hint.get("reason")
    base_number = state_hint.get("base_number")

    lines = [f"[StateHint]", f"mode: {mode}"]
    if reason:
        lines.append(f"reason: {reason}")
    if base_number is not None:
        lines.append(f"base_number: {base_number}")

    return "\n".join(lines)


# ============================================================
# call_ovv: BIS / A5 強化版
# ============================================================
def call_ovv(context_key: int, input_packet: Dict[str, Any]) -> str:
    """
    Ovv 推論呼び出し（BIS 対応版）

    Parameters
    ----------
    context_key : int
        Discord 側で一意になるコンテキストキー（guild/channel/thread 由来）
    input_packet : dict
        Interface_Box が構築した InputPacket
        {
          "user_text": str,
          "runtime_memory": List[dict],
          "thread_brain_text": str,
          "scoring_hint": str,
          "state_hint": Optional[dict],
        }

    Returns
    -------
    str
        LLM からの生テキスト（[FINAL] を含むことを期待）。
        [FINAL] 抽出は Stabilizer 側（stabilizer.extract_final_answer）で行う。
    """

    user_text: str = input_packet.get("user_text") or ""
    runtime_memory: List[dict] = input_packet.get("runtime_memory") or []
    thread_brain_text: str = input_packet.get("thread_brain_text") or ""
    scoring_hint: str = input_packet.get("scoring_hint") or ""
    state_hint: Optional[Dict[str, Any]] = input_packet.get("state_hint")

    messages: List[Dict[str, str]] = []

    # 1) System + Soft-Core + Core / External
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    # Core / External は assistant ロールで、そのまま「前提仕様」として渡す
    if OVV_CORE:
        messages.append({"role": "assistant", "content": OVV_CORE})
    if OVV_EXTERNAL:
        messages.append({"role": "assistant", "content": OVV_EXTERNAL})

    # 2) ThreadBrain テキスト（あれば）
    if thread_brain_text:
        messages.append({
            "role": "system",
            "content": f"[ThreadBrain]\n{thread_brain_text}",
        })

    # 3) TB-Scoring ヒント（あれば）
    if scoring_hint:
        messages.append({
            "role": "system",
            "content": scoring_hint,
        })

    # 4) StateHint（あれば）
    state_text = _format_state_hint(state_hint)
    if state_text:
        messages.append({
            "role": "system",
            "content": state_text,
        })

    # 5) 直近メモリ（runtime_memory）から最大 20 件を injection
    #    形式: {"role": "user"/"assistant", "content": "..."}
    for m in runtime_memory[-20:]:
        role = m.get("role", "user")
        content = m.get("content") or ""
        if not content:
            continue
        # Discord ログには [FINAL] 付きが混じることもあるが、そのまま渡して問題ない
        messages.append({"role": role, "content": content})

    # 6) 今回ユーザー入力
    final_user_text = user_text.strip()
    if not final_user_text:
        # 空メッセージ対策（ThreadBrain / runtime_memory だけで回答するケース）
        final_user_text = "（ユーザーからの明示的なメッセージは空ですが、直近の文脈と ThreadBrain/TB-Scoring に基づいて、今適切な応答を返してください。）"

    messages.append({"role": "user", "content": final_user_text})

    # 7) OpenAI 呼び出し
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.5,
        )
        raw = res.choices[0].message.content.strip()

        # runtime_memory に assistant 発話を保存（生テキスト）
        try:
            db_pg.append_runtime_memory(
                str(context_key),
                "assistant",
                raw,
                limit=40,
            )
        except Exception as e:
            print("[call_ovv] append_runtime_memory error:", repr(e))

        # Discord の 2000 文字制限を考慮し、ざっくり切って返す
        return raw[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        try:
            db_pg.log_audit(
                "openai_error",
                {
                    "context_key": context_key,
                    "user_text": final_user_text[:500],
                    "error": repr(e),
                },
            )
        except Exception:
            pass

        # Stabilizer 側が [FINAL] を抽出できるように形式は維持
        return "[FINAL]\nOvv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"