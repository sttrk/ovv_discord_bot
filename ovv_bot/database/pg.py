# database/pg.py
# PostgreSQL / Runtime Memory / Thread Brain - September Stable Edition

import json
from typing import Optional, List
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from openai import OpenAI

from config import POSTGRES_URL, OPENAI_API_KEY

PG_CONN = None
AUDIT_READY = False

# Thread Brain 用 OpenAI クライアント
_tb_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# PG Connect / Init
# ============================================================

def pg_connect():
    """
    PostgreSQL への接続を確立し、PG_CONN をセットする。
    bot 起動時に 1 回呼ぶ想定。
    """
    global PG_CONN
    print("=== [PG] Connecting ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL missing")
        PG_CONN = None
        return None

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        PG_CONN = conn
        print("[PG] Connected OK")
        return conn
    except Exception as e:
        print("[PG] Connection failed:", repr(e))
        PG_CONN = None
        return None


def init_db(conn):
    """
    ovv スキーマ配下のテーブルを作成。
    """
    global AUDIT_READY
    print("=== [PG] init_db() ===")

    if conn is None:
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.thread_brain (
                context_key BIGINT PRIMARY KEY,
                summary JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.close()
        AUDIT_READY = True
        print("[PG] init_db OK")

    except Exception as e:
        print("[PG] init_db ERROR:", repr(e))
        AUDIT_READY = False


# ============================================================
# Audit Log
# ============================================================

def log_audit(event_type: str, details: Optional[dict] = None):
    """
    全システム共通の audit_log 出力。
    PG が使えない場合は print のみ。
    """
    if details is None:
        details = {}

    print(f"[AUDIT] {event_type} :: {details}")

    if not AUDIT_READY or PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.audit_log (event_type, details)
                VALUES (%s, %s::jsonb)
                """,
                (event_type, json.dumps(details)),
            )
    except Exception as e:
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# Runtime Memory
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT memory_json
                FROM ovv.runtime_memory
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return []
            return row["memory_json"]
    except Exception as e:
        print("[runtime_memory load error]", repr(e))
        return []


def save_runtime_memory(session_id: str, mem: List[dict]):
    if PG_CONN is None:
        return
    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    memory_json = EXCLUDED.memory_json,
                    updated_at  = NOW();
                """,
                (session_id, json.dumps(mem, ensure_ascii=False)),
            )
    except Exception as e:
        print("[runtime_memory save error]", repr(e))


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    mem = load_runtime_memory(session_id)
    mem.append(
        {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    if len(mem) > limit:
        mem = mem[-limit:]
    save_runtime_memory(session_id, mem)


# ============================================================
# Thread Brain
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
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
    lines = []
    for m in recent_mem[-30:]:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        short = m.get("content", "").replace("\n", " ")
        if len(short) > 500:
            short = short[:500] + " ...[truncated]"
        lines.append(f"{role}: {short}")

    history_block = "\n".join(lines) if lines else "(no logs)"

    prev_summary = load_thread_brain(context_key)
    prev_summary_text = json.dumps(prev_summary, ensure_ascii=False) if prev_summary else "null"

    return f"""
あなたは「thread_brain」を生成するAIです。
必ず JSON のみを返してください。

出力フォーマット:
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

重要: JSON 以外の文字を返してはならない。

[前回 summary]
{prev_summary_text}

[recent logs]
{history_block}
""".strip()


def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = _tb_client.chat.completions.create(
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

    if "meta" not in summary:
        summary["meta"] = {}
    summary["meta"]["context_key"] = context_key
    summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()

    return summary
