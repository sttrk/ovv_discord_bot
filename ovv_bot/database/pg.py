# database/pg.py
import json
from typing import Optional, List
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from config import POSTGRES_URL
from ovv.ovv_call import SYSTEM_PROMPT  # ← OK（呼び出される側が軽量なので循環しない）


PG_CONN = None
AUDIT_READY = False


# ============================================================
# PostgreSQL Connection
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
# Init DB
# ============================================================
def init_db(conn):
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
    if details is None:
        details = {}

    print(f"[AUDIT] {event_type} :: {details}")

    if not AUDIT_READY or PG_CONN is None:
        return

    try:
        with PG_CONN.cursor() as cur:
            cur.execute("""
                INSERT INTO ovv.audit_log (event_type, details)
                VALUES (%s, %s::jsonb)
                """,
                (event_type, json.dumps(details))
            )
    except Exception as e:
        print("[AUDIT] write failed:", repr(e))


# ============================================================
# runtime_memory (load / save / append)
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
                DO UPDATE SET memory_json = EXCLUDED.memory_json,
                              updated_at = NOW();
            """, (session_id, json.dumps(mem, ensure_ascii=False)))
    except Exception as e:
        print("[runtime_memory save error]", repr(e))


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    mem = load_runtime_memory(session_id)
    mem.append({
        "role": role,
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat()
    })

    if len(mem) > limit:
        mem = mem[-limit:]

    save_runtime_memory(session_id, mem)


# ============================================================
# thread_brain (load / save / generate)
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
                DO UPDATE SET summary = EXCLUDED.summary,
                              updated_at = NOW();
            """, (context_key, json.dumps(summary, ensure_ascii=False)))
        return True
    except Exception as e:
        print("[thread_brain save error]", repr(e))
        return False


# ============================================================
# generate_thread_brain（LLM生成）
# ============================================================
def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    from openai import OpenAI
    openai_client = OpenAI()

    # 短縮（以前あなたが使っていた prompt 形式を流用可）
    history_text = ""
    for m in recent_mem[-20:]:
        history_text += f"{m['role'].upper()}: {m['content']}\n"

    prompt = f"""
あなたは thread_brain を生成する AI です。
必ず JSON のみで返答。

[history]
{history_text}
""".strip()

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "必ず JSON のみで返しなさい"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        txt = res.choices[0].message.content.strip()

        # JSON抽出
        import json
        start, end = txt.find("{"), txt.rfind("}")
        summary = json.loads(txt[start:end+1])

        summary["meta"]["context_key"] = context_key
        summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()

        return summary

    except Exception as e:
        print("[thread_brain generate error]", repr(e))
        return None
