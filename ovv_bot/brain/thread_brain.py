# brain/thread_brain.py
import json
from typing import List, Optional
from datetime import datetime, timezone

import psycopg2.extras

from database.pg import PG_CONN


def load_thread_brain(context_key: int) -> Optional[dict]:
    """
    thread_brain 1 件読み出し。
    """
    if PG_CONN is None:
        return None
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT summary
                FROM ovv.thread_brain
                WHERE context_key = %s
                """,
                (context_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row["summary"]
    except Exception as e:
        print("[thread_brain load error]", repr(e))
        return None


def save_thread_brain(context_key: int, summary: dict) -> bool:
    """
    thread_brain upsert。
    """
    if PG_CONN is None:
        return False
    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (context_key)
                DO UPDATE SET
                    summary   = EXCLUDED.summary,
                    updated_at = NOW();
                """,
                (context_key, json.dumps(summary, ensure_ascii=False)),
            )
        return True
    except Exception as e:
        print("[thread_brain save error]", repr(e))
        return False


def _build_thread_brain_prompt(context_key: int, recent_mem: List[dict]) -> str:
    """
    LLM に渡す prompt を構築。
    """
    lines = []
    for m in recent_mem[-30:]:
        role = "USER" if m["role"] == "user" else "ASSISTANT"
        short = m["content"].replace("\n", " ")
        if len(short) > 500:
            short = short[:500] + " ...[truncated]"
        lines.append(f"{role}: {short}")

    history_block = "\n".join(lines) if lines else "(no logs)"

    prev_summary = load_thread_brain(context_key)
    prev_summary_text = json.dumps(prev_summary, ensure_ascii=False) if prev_summary else "null"

    return f"""
あなたは「thread_brain」を生成するAIです。
必ず JSON のみを返すこと。

出力フォーマット：
{{
  "meta": {{
    "version": "1.0",
    "updated_at": "<ISO8601>",
    "context_key": {context_key},
    "total_tokens_estimate": 0
  }},
  "status": {{
    "phase": "<idle|active|blocked|done>",
    "last_major_event": "",
    "risk": []
  }},
  "decisions": [],
  "unresolved": [],
  "constraints": [],
  "next_actions": [],
  "history_digest": "",
  "high_level_goal": "",
  "recent_messages": [],
  "current_position": ""
}}

[前回 summary]
{prev_summary_text}

[recent logs]
{history_block}
""".strip()


def generate_thread_brain(context_key: int, recent_mem: List[dict], openai_client) -> Optional[dict]:
    """
    LLM を叩いて thread_brain JSON を生成。
    openai_client は呼び出し側（bot.py）から渡す。
    """
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "必ず JSON のみを返す。"},
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content.strip()
    except Exception as e:
        print("[thread_brain LLM error]", repr(e))
        return None

    txt = raw
    if "```" in txt:
        parts = txt.split("```")
        cands = [p for p in parts if "{" in p and "}" in p]
        if cands:
            txt = max(cands, key=len)

    txt = txt.strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start == -1 or end == -1:
        return None

    try:
        summary = json.loads(txt[start : end + 1])
    except Exception as e:
        print("[thread_brain JSON error]", repr(e))
        return None

    summary.setdefault("meta", {})
    summary["meta"]["context_key"] = context_key
    summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    return summary
