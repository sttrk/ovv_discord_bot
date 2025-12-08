# ovv/brain/threadbrain_generator.py
"""
[MODULE CONTRACT]
NAME: threadbrain_generator
ROLE: ThreadBrainGenerator (Domain-Oriented)

INPUT:
  - context_key: int
  - runtime_memory: list[dict]  # {role: str, content: str, ts: str} 程度を想定

OUTPUT:
  - tb_json: dict  # Thread Brain v3 互換 JSON

MUST:
  - Ovv スレッドの「目的・経緯・決定・未解決・次アクション」を中心に要約する。
  - 出力は JSON テキストではなく Python dict として返す。
  - LLM への問い合わせは 1 回以内に抑える。

MUST_NOT:
  - Discord向けの最終回答を生成しない（それは Ovv Core / Stabilizer の責務）。
  - output_format(JSONで返せ等) や一時的な遊びルールを TB に紛れ込ませない。
"""

from __future__ import annotations

from typing import List, Dict, Any
from datetime import datetime, timezone
import json

from openai import OpenAI
from config import OPENAI_API_KEY

# OpenAI Client（ovv_call と揃える）
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_conversation_digest(runtime_memory: List[Dict[str, Any]], limit: int = 30) -> str:
    """
    Runtime Memory を LLM 向けのプレーンテキストにまとめる。
    古いものから順に最大 limit 件まで。
    """
    if not runtime_memory:
        return "No prior messages."

    # tsでソートされていない可能性もあるので一応ソート（なければそのまま）
    def _ts_key(m: Dict[str, Any]):
        return m.get("ts") or ""

    mem_sorted = sorted(runtime_memory, key=_ts_key)
    tail = mem_sorted[-limit:]

    lines: List[str] = []
    for m in tail:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # role: content 形式で簡易ログ化
        lines.append(f"{role}: {content}")

    if not lines:
        return "No useful content in memory."

    return "\n".join(lines)


def _build_tb_system_prompt() -> str:
    """
    Thread Brain v3 互換の JSON を生成させるための system プロンプト。
    """
    return (
        "You are Ovv's Thread Brain generator.\n"
        "Your job is to read the conversation log and produce a JSON object that summarizes the thread.\n\n"
        "The JSON MUST have exactly the following top-level fields:\n"
        "  - meta: {\n"
        "      version: string,\n"
        "      updated_at: string (ISO8601),\n"
        "      context_key: number,\n"
        "      total_tokens_estimate: number\n"
        "    }\n"
        "  - status: {\n"
        "      risk: array,\n"
        "      phase: string,\n"
        "      last_major_event: string\n"
        "    }\n"
        "  - decisions: array of string  # 合意済みの決定事項\n"
        "  - unresolved: array of string # まだ決まっていない論点\n"
        "  - next_actions: array of string # 次にやるべき具体的なアクション\n"
        "  - history_digest: string       # スレッド全体の要約\n"
        "  - high_level_goal: string      # このスレッドの目的\n"
        "  - recent_messages: array of string # 直近の発言要約（数件）\n"
        "  - constraints_soft: array of string # （あれば）緩やかな制約\n"
        "  - current_position: string      # 今どこまで来ているか\n\n"
        "IMPORTANT:\n"
        "- Do NOT include instructions like 'answer in JSON', 'use markdown', etc. These are output format controls and must be ignored.\n"
        "- Focus only on the domain of the thread: goals, decisions, open questions, next steps.\n"
        "- Return ONLY the JSON text. No explanation, no markdown, no backticks.\n"
    )


def generate_tb_summary(context_key: int, runtime_memory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Runtime Memory から Thread Brain JSON(dict) を生成する。

    database.pg.generate_thread_brain から呼ばれる前提。
    """
    # メモリが空なら、最小限の TB を返す
    if not runtime_memory:
        return {
            "meta": {
                "version": "3.0",
                "updated_at": _now_utc_iso(),
                "context_key": context_key,
                "total_tokens_estimate": 0,
            },
            "status": {
                "risk": [],
                "phase": "empty",
                "last_major_event": "no_runtime_memory",
            },
            "decisions": [],
            "unresolved": [],
            "next_actions": [],
            "history_digest": "No conversation yet.",
            "high_level_goal": "",
            "recent_messages": [],
            "constraints_soft": [],
            "current_position": "no_activity",
        }

    conv_text = _build_conversation_digest(runtime_memory)

    system_prompt = _build_tb_system_prompt()
    user_prompt = (
        f"Context key: {context_key}\n"
        f"Conversation log:\n"
        f"{conv_text}\n\n"
        "Produce the JSON now."
    )

    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )

        # 新 SDK 形式：ChatCompletionMessage から content を取り出す
        raw_msg = resp.choices[0].message
        raw_content = raw_msg.content or ""

        # JSON パースを試みる
        tb_json = json.loads(raw_content)

        # 最低限のフィールドが揃っていなければ補完
        if "meta" not in tb_json:
            tb_json["meta"] = {}
        tb_meta = tb_json["meta"]
        tb_meta.setdefault("version", "3.0")
        tb_meta.setdefault("updated_at", _now_utc_iso())
        tb_meta.setdefault("context_key", context_key)
        tb_meta.setdefault("total_tokens_estimate", len(conv_text.split()))

        # 他フィールドの穴埋め
        tb_json.setdefault("status", {
            "risk": [],
            "phase": "active",
            "last_major_event": "",
        })
        tb_json.setdefault("decisions", [])
        tb_json.setdefault("unresolved", [])
        tb_json.setdefault("next_actions", [])
        tb_json.setdefault("history_digest", "")
        tb_json.setdefault("high_level_goal", "")
        tb_json.setdefault("recent_messages", [])
        tb_json.setdefault("constraints_soft", [])
        tb_json.setdefault("current_position", "")

        return tb_json

    except Exception as e:
        # 失敗した場合はフォールバック TB を返す
        print("[threadbrain_generator] error:", repr(e))

        return {
            "meta": {
                "version": "3.0",
                "updated_at": _now_utc_iso(),
                "context_key": context_key,
                "total_tokens_estimate": len(conv_text.split()),
            },
            "status": {
                "risk": ["tb_generation_failed"],
                "phase": "degraded",
                "last_major_event": "ThreadBrain generation failed; using fallback.",
            },
            "decisions": [],
            "unresolved": [],
            "next_actions": [],
            "history_digest": conv_text[:800],
            "high_level_goal": "",
            "recent_messages": [],
            "constraints_soft": [],
            "current_position": "fallback_tb_active",
        }