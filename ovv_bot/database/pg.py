# database/pg.py
# PostgreSQL Integration Layer - Final Stable Edition (A-3, patched)

import json
from typing import Optional, List, Dict
from datetime import datetime, timezone
import psycopg2
import psycopg2.extras

from config import POSTGRES_URL

# ============================================================
# GLOBAL STATE
# ============================================================
PG_CONN = None
AUDIT_READY = False

# ============================================================
# CONNECT
# ============================================================
def pg_connect():
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

# ============================================================
# INIT DB
# ============================================================
def init_db(conn):
    global AUDIT_READY
    print("=== [PG] init_db() ===")

    if conn is None:
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # runtime memory
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # audit log
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # thread_brain
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
# AUDIT LOG
# ============================================================
def log_audit(event_type: str, details: Optional[dict] = None):
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
# RUNTIME MEMORY I/O
# ============================================================
def load_runtime_memory(session_id: str) -> List[dict]:
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT memory_json FROM ovv.runtime_memory WHERE session_id=%s", (session_id,))
            row = cur.fetchone()
            return row["memory_json"] if row else []
    except Exception as e:
        print("[runtime_memory load error]", repr(e))
        return []

def save_runtime_memory(session_id: str, mem: List[dict]):
    if PG_CONN is None:
        return
    try:
        with PG_CONN.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT(session_id)
                DO UPDATE SET memory_json=EXCLUDED.memory_json, updated_at=NOW();
            """, (session_id, json.dumps(mem, ensure_ascii=False)))
    except Exception as e:
        print("[runtime_memory save error]", repr(e))

def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    mem = load_runtime_memory(session_id)
    mem.append({"role": role, "content": content, "ts": datetime.now(timezone.utc).isoformat()})
    if len(mem) > limit:
        mem = mem[-limit:]
    save_runtime_memory(session_id, mem)

# ============================================================
# THREAD BRAIN
# ============================================================
def load_thread_brain(context_key: int) -> Optional[dict]:
    if PG_CONN is None:
        return None
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT summary FROM ovv.thread_brain WHERE context_key=%s", (context_key,))
            row = cur.fetchone()
            return row["summary"] if row else None
    except Exception as e:
        print("[thread_brain load error]", repr(e))
        return None


def save_thread_brain(context_key: int, summary: dict) -> bool:
    if PG_CONN is None:
        return False
    try:
        with PG_CONN.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT(context_key)
                DO UPDATE SET summary=EXCLUDED.summary, updated_at=NOW();
            """, (context_key, json.dumps(summary, ensure_ascii=False)))
        return True
    except Exception as e:
        print("[thread_brain save error]", repr(e))
        return False

# ============================================================
# BUILD THREAD BRAIN PROMPT
# ============================================================
def _build_thread_brain_prompt(context_key: int, recent_mem: List[dict]) -> str:
    lines = []
    for m in recent_mem[-30:]:
        role = "USER" if m["role"] == "user" else "ASSISTANT"
        short = m["content"].replace("\n", " ")
        if len(short) > 500:
            short = short[:500] + " ..."
        lines.append(f"{role}: {short}")

    history_block = "\n".join(lines) if lines else "(no logs)"
    prev = load_thread_brain(context_key)
    prev_json = json.dumps(prev, ensure_ascii=False) if prev else "null"

    return f"""
あなたは「thread_brain」を生成するAIです。
必ず JSON のみを返してください。

[前回 summary]
{prev_json}

[recent logs]
{history_block}
""".strip()

# ============================================================
# GENERATE THREAD BRAIN (patched)
# ============================================================
from openai import OpenAI
from config import OPENAI_API_KEY
_openai_client = OpenAI(api_key=OPENAI_API_KEY)

def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = _openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "必ず JSON のみで返す"},
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content.strip()
    except Exception as e:
        print("[thread_brain LLM error]", repr(e))
        return None

    # JSON 抽出
    txt = raw
    if "```" in txt:
        parts = txt.split("```")
        cands = [p for p in parts if "{" in p and "}" in p]
        if cands:
            txt = max(cands, key=len)

    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end == -1:
        return None

    try:
        summary = json.loads(txt[start:end+1])
    except Exception as e:
        print("[thread_brain JSON error]", repr(e))
        return None

    # ============================================================
    # PATCH: 必ず meta を補完する（KeyError 防止）
    # ============================================================
    if "meta" not in summary or not isinstance(summary["meta"], dict):
        summary["meta"] = {}

    summary["meta"].setdefault("version", "1.0")
    summary["meta"]["context_key"] = context_key
    summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()

    return summary