# database/pg.py
import json
from typing import Optional
import psycopg2
import psycopg2.extras
from config import POSTGRES_URL

PG_CONN = None
AUDIT_READY = False


def pg_connect():
    """
    PostgreSQL への接続を確立し、PG_CONN をセットする。
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
    ovv スキーマの初期化。
    スキーマが無い状態だと CREATE TABLE が必ず失敗するため
    必ず最初に CREATE SCHEMA IF NOT EXISTS を実行する。
    """
    global AUDIT_READY
    print("=== [PG] init_db() ===")

    if conn is None:
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # ★ 必須：まずスキーマを作成
        cur.execute("CREATE SCHEMA IF NOT EXISTS ovv;")

        # runtime_memory
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # audit_log
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


def log_audit(event_type: str, details: Optional[dict] = None):
    """
    audit_log への書き込み。
    PG が死んでいる場合は print のみにフォールバック。
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
