# database/pg.py
# PostgreSQL Integration Layer - A5 / BIS 対応版
#
# - runtime_memory / audit_log / thread_brain を一括管理
# - ThreadBrain v1.1 形式で summary を生成（TB-Scoring / Interface_Box と整合）
# - Ovv コアとは「ストレージ＋TB生成」のみで接続（責務分離）

import json
from typing import Optional, List, Dict
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from config import POSTGRES_URL, OPENAI_API_KEY
from openai import OpenAI

# ============================================================
# GLOBAL STATE
# ============================================================
PG_CONN: Optional[psycopg2.extensions.connection] = None
AUDIT_READY: bool = False

# OpenAI client for ThreadBrain generation
_openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# CONNECT
# ============================================================
def pg_connect():
    """
    POSTGRES_URL からコネクションを張り、グローバル PG_CONN に保持する。
    失敗時は PG_CONN=None。
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


# ============================================================
# INIT DB
# ============================================================
def init_db(conn):
    """
    ovv スキーマ配下に必要なテーブルを作成する。
    - ovv.runtime_memory
    - ovv.audit_log
    - ovv.thread_brain
    """
    global AUDIT_READY
    print("=== [PG] init_db() ===")

    if conn is None:
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # runtime_memory
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS ovv;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )

        # audit_log
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )

        # thread_brain
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ovv.thread_brain (
                context_key BIGINT PRIMARY KEY,
                summary JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )

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
    """
    シンプルな監査ログ。
    PG 未接続時は print のみ。
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
# RUNTIME MEMORY I/O
# ============================================================
def load_runtime_memory(session_id: str) -> List[dict]:
    """
    1 セッション分の runtime_memory を取得。
    見つからなければ空配列。
    """
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT memory_json FROM ovv.runtime_memory WHERE session_id=%s",
                (session_id,),
            )
            row = cur.fetchone()
            return row["memory_json"] if row else []
    except Exception as e:
        print("[runtime_memory load error]", repr(e))
        return []


def save_runtime_memory(session_id: str, mem: List[dict]):
    """
    runtime_memory を upsert。
    """
    if PG_CONN is None:
        return
    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT(session_id)
                DO UPDATE SET
                    memory_json = EXCLUDED.memory_json,
                    updated_at  = NOW();
                """,
                (session_id, json.dumps(mem, ensure_ascii=False)),
            )
    except Exception as e:
        print("[runtime_memory save error]", repr(e))


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    """
    runtime_memory に 1 メッセージ追加（ローテ付き）。
    """
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
# THREAD BRAIN: I/O
# ============================================================
def load_thread_brain(context_key: int) -> Optional[dict]:
    """
    context_key 単位で thread_brain summary(JSON) を取得。
    """
    if PG_CONN is None:
        return None
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT summary FROM ovv.thread_brain WHERE context_key=%s",
                (context_key,),
            )
            row = cur.fetchone()
            return row["summary"] if row else None
    except Exception as e:
        print("[thread_brain load error]", repr(e))
        return None


def save_thread_brain(context_key: int, summary: dict) -> bool:
    """
    thread_brain summary(JSON) を upsert。
    """
    if PG_CONN is None:
        return False
    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT(context_key)
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


# ============================================================
# THREAD BRAIN: PROMPT BUILDER (v1.1)
# ============================================================
def _build_thread_brain_prompt(context_key: int, recent_mem: List[dict]) -> str:
    """
    ThreadBrain v1.1 用のプロンプトを構築する。
    - TB-Scoring / threadbrain_adapter と整合するキー構造を前提とする。
    """

    # 直近ログ（user / assistant）を 30 件まで整形
    lines: List[str] = []
    for m in recent_mem[-30:]:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        short = (m.get("content") or "").replace("\n", " ")
        if len(short) > 500:
            short = short[:500] + " ...[truncated]"
        lines.append(f"{role}: {short}")

    history_block = "\n".join(lines) if lines else "(no logs)"

    # 既存 summary（あれば）も渡す
    prev = load_thread_brain(context_key)
    prev_json = json.dumps(prev, ensure_ascii=False) if prev else "null"

    # ThreadBrain v1.1 のターゲット構造
    template = f"""
あなたは「thread_brain」を生成する専門AIです。
会話ログと前回の summary を入力として受け取り、
必ず「JSON オブジェクトのみ」を返してください。
マークダウンコードブロックやコメントは絶対に含めないでください。

出力フォーマット（必ずこのキー構造で返すこと）:

{{
  "meta": {{
    "version": "1.1",
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

- "decisions" には、今後覆さない合意事項・方針を列挙してください。
- "unresolved" には、未解決の重要な論点や TODO を列挙してください。
- "constraints" には、このスレッドに特有の制約条件（ルール・禁止事項など）を列挙してください。
- "next_actions" には、Ovv が次に取るべき具体的な行動を箇条書きで書いてください。
- "history_digest" には、これまでの会話の要点を 3〜5 行で日本語要約してください。
- "recent_messages" には、直近の重要メッセージを 5〜10 個程度、短いテキストで格納してください。

[前回 summary]
{prev_json}

[recent logs]
{history_block}
""".strip()

    return template


# ============================================================
# THREAD BRAIN: GENERATION
# ============================================================
def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    """
    OpenAI を叩いて ThreadBrain v1.1 summary を生成する。
    - TB-Scoring / Interface_Box / ovv_call からは、この JSON 構造を前提として参照される。
    """
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = _openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "必ず JSON オブジェクトのみを返してください。マークダウンや説明文は禁止です。"},
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content.strip()
    except Exception as e:
        print("[thread_brain LLM error]", repr(e))
        return None

    # --------------------------------------------------------
    # JSON 抽出（``` が混じった場合にも対応）
    # --------------------------------------------------------
    txt = raw
    if "```" in txt:
        parts = txt.split("```")
        cands = [p for p in parts if "{" in p and "}" in p]
        if cands:
            txt = max(cands, key=len)

    txt = txt.strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end == -1:
        print("[thread_brain] JSON braces not found")
        return None

    try:
        summary = json.loads(txt[start : end + 1])
    except Exception as e:
        print("[thread_brain JSON error]", repr(e))
        return None

    if not isinstance(summary, dict):
        print("[thread_brain] summary is not dict")
        return None

    # --------------------------------------------------------
    # 安定構造の付与（欠損キーをデフォルト補完）
    # --------------------------------------------------------
    meta: Dict = summary.setdefault("meta", {})
    meta.setdefault("version", "1.1")
    meta["context_key"] = context_key
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta.setdefault("total_tokens_estimate", 0)

    summary.setdefault("status", {})
    summary.setdefault("decisions", [])
    summary.setdefault("unresolved", [])
    summary.setdefault("constraints", [])
    summary.setdefault("next_actions", [])
    summary.setdefault("history_digest", "")
    summary.setdefault("high_level_goal", "")
    summary.setdefault("recent_messages", [])
    summary.setdefault("current_position", "")

    return summary