import os
import json
import discord
from discord import MessageType
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import Dict, List, Optional
from datetime import datetime, timezone

# ============================================================
# 1. Environment
# ============================================================

print("=== [BOOT] Loading environment variables ===")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID")
NOTION_SESSIONS_DB_ID = os.getenv("NOTION_SESSIONS_DB_ID")
NOTION_LOGS_DB_ID = os.getenv("NOTION_LOGS_DB_ID")
POSTGRES_URL = os.getenv("POSTGRES_URL")

if not DISCORD_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Discord/OpenAI env missing")

if not NOTION_API_KEY:
    raise RuntimeError("NOTION_API_KEY missing")

print("=== [ENV] Env OK ===")
print("=== [ENV] POSTGRES_URL detected:", str(POSTGRES_URL)[:80], "...")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1.5 PostgreSQL（ovv schema）接続 + init + audit_log API
# ============================================================

import psycopg2
import psycopg2.extras

PG_CONN = None          # psycopg2 connection
AUDIT_READY = False     # audit_log が使えるかどうか

def pg_connect():
    """
    POSTGRES_URL を使って PostgreSQL に接続。
    失敗しても bot 自体は動かす（PG 無効モード）。
    """
    global PG_CONN
    print("=== [PG] pg_connect() ENTERED ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL not set, skip PG")
        PG_CONN = None
        return None

    print("[PG] Connecting via:", POSTGRES_URL[:120], "...")

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        conn.autocommit = True
        PG_CONN = conn
        print("[PG] PostgreSQL connected OK")
        return conn
    except Exception as e:
        print("[PG] Connection failed:", repr(e))
        PG_CONN = None
        return None


def init_db(conn):
    """
    ovv.runtime_memory / ovv.audit_log を保証。
    既存スキーマと完全互換（CREATE TABLE IF NOT EXISTS のみ）。
    """
    global AUDIT_READY

    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        # 永続メモリ（JSONB 1 行ぶん）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # 監査ログ（既存スキーマを維持）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        cur.close()
        AUDIT_READY = True
        print("[PG] init_db OK")

    except Exception as e:
        print("[PG] init_db ERROR:", repr(e))
        AUDIT_READY = False


def log_audit(
    event_type: str,
    details: Optional[dict] = None,
    context_key: Optional[int] = None,
):
    """
    audit_log への書き込み。
    - context_key が渡された場合は details["context_key"] に自動付与（文字列）。
    - PG が使えない場合は print のみ。
    """
    if details is None:
        details = {}

    if context_key is not None:
        # str に揃えておく（SQL の比較を楽にするため）
        details.setdefault("context_key", str(context_key))

    try:
        print(f"[AUDIT] {event_type} :: {details}")
    except Exception:
        pass

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
# A-1. audit_log 抽出ユーティリティ
# ============================================================

def get_audit_log(context_key: int, limit: Optional[int] = None) -> List[dict]:
    """
    audit_log から details->>'context_key' で該当スレッドのログを取得。
    id DESC で最大 limit 件を取り、Python 側で古い順に並べ直す。
    """
    if PG_CONN is None:
        print("[PG] get_audit_log skipped (no connection)")
        return []

    try:
        cur = PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        base_sql = """
            SELECT id, event_type, details, created_at
            FROM ovv.audit_log
            WHERE details->>'context_key' = %s
            ORDER BY id DESC
        """
        if limit:
            sql = base_sql + " LIMIT %s;"
            cur.execute(sql, (str(context_key), limit))
        else:
            sql = base_sql + ";"
            cur.execute(sql, (str(context_key),))

        rows = cur.fetchall()
        cur.close()

        # 古い順に変換
        rows.reverse()
        return rows

    except Exception as e:
        print("[PG ERROR get_audit_log]", repr(e))
        return []


def get_max_audit_id(context_key: int) -> int:
    """
    その context の audit_log の最大 id（なければ 0）。
    """
    if PG_CONN is None:
        return 0

    try:
        cur = PG_CONN.cursor()
        cur.execute(
            """
            SELECT COALESCE(MAX(id), 0)
            FROM ovv.audit_log
            WHERE details->>'context_key' = %s;
            """,
            (str(context_key),),
        )
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        print("[PG ERROR get_max_audit_id]", repr(e))
        return 0


# ============================================================
# A-2. runtime_memory（サマリ保存）ユーティリティ
# ============================================================

def load_runtime_state(context_key: int) -> Optional[dict]:
    """
    ovv.runtime_memory から session_id=context_key の memory_json を取得。
    """
    if PG_CONN is None:
        return None

    try:
        cur = PG_CONN.cursor()
        cur.execute(
            """
            SELECT memory_json
            FROM ovv.runtime_memory
            WHERE session_id = %s;
            """,
            (str(context_key),),
        )
        row = cur.fetchone()
        cur.close()

        if not row:
            return None

        data = row[0]
        if isinstance(data, dict):
            return data
        # 万が一文字列で入っていた場合
        return json.loads(data)

    except Exception as e:
        print("[PG ERROR load_runtime_state]", repr(e))
        return None


def save_runtime_state(context_key: int, data: dict):
    """
    ovv.runtime_memory に JSONB を upsert。
    """
    if PG_CONN is None:
        return

    try:
        cur = PG_CONN.cursor()
        cur.execute(
            """
            INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (session_id)
            DO UPDATE SET
                memory_json = EXCLUDED.memory_json,
                updated_at = NOW();
            """,
            (str(context_key), json.dumps(data)),
        )
        cur.close()
    except Exception as e:
        print("[PG ERROR save_runtime_state]", repr(e))


# ============================================================
# A-2. audit_summary ビルド
# ============================================================

SUMMARY_MIN_CHARS = 3000
SUMMARY_MAX_CHARS = 6000
SUMMARY_MAX_EVENTS = 200   # 要約に使う最大イベント数

def build_summary_from_logs(context_key: int, logs: List[dict]) -> Optional[str]:
    """
    audit_log の rows から、スレッド全体のサマリを OpenAI で生成。
    """
    if not logs:
        return None

    # ログをテキスト化（USER / BOT を中心に、他は圧縮）
    lines: List[str] = []
    for row in logs:
        ev = row.get("event_type", "")
        details = row.get("details") or {}
        ts = row.get("created_at")
        if isinstance(ts, datetime):
            ts_str = ts.isoformat()
        else:
            ts_str = str(ts)

        content = details.get("content") or ""
        author = details.get("author") or ""
        length = details.get("length")

        if ev == "user_message":
            msg = content[:300]
            lines.append(f"[{ts_str}] USER({author}): {msg}")
        elif ev == "assistant_reply":
            msg = content[:500]
            lines.append(f"[{ts_str}] BOT: {msg}")
        elif ev == "openai_error":
            lines.append(f"[{ts_str}] OPENAI_ERROR: {details}")
        elif ev == "discord_error":
            lines.append(f"[{ts_str}] DISCORD_ERROR: {details}")
        elif ev == "command":
            cmd = details.get("command") or ""
            lines.append(f"[{ts_str}] COMMAND: {cmd}")
        else:
            # その他は軽く
            lines.append(f"[{ts_str}] {ev}: {details}")

    joined = "\n".join(lines)

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは Ovv の監査用サマリエンジンです。"
                        "与えられたログから、1つの Discord スレッドにおける "
                        "目的・前提・合意した仕様・重要な決定・TODO・懸念点を、"
                        f"{SUMMARY_MIN_CHARS}〜{SUMMARY_MAX_CHARS} 文字程度で日本語要約してください。"
                        "以下のセクション構造を必ず守ってください。\n\n"
                        "1. コンテキスト / 背景\n"
                        "2. 合意された仕様・ルール（箇条書き）\n"
                        "3. 現在の状態（何ができていて、何が未完か）\n"
                        "4. TODO（次にやるべきことを箇条書き）\n"
                        "5. リスク・注意点 / メモ\n\n"
                        "細かいチャットのやりとり全ては書かず、本質的なポイントに絞ってください。"
                    ),
                },
                {
                    "role": "user",
                    "content": joined,
                },
            ],
            temperature=0.2,
        )
        summary = completion.choices[0].message.content.strip()
        return summary

    except Exception as e:
        print("[ERROR build_summary_from_logs]", repr(e))
        log_audit(
            "summary_error",
            {
                "context_key": str(context_key),
                "error": repr(e),
            },
        )
        return None


def get_or_build_summary(context_key: int) -> Optional[str]:
    """
    - runtime_memory に summary があり、最新の audit_log から変化がなければそれを再利用。
    - そうでなければ audit_log を読み出して新たに要約を生成し、runtime_memory に保存。
    """
    if PG_CONN is None:
        return None

    # 1. runtime_state を読む
    state = load_runtime_state(context_key) or {}
    last_audit_id = int(state.get("last_audit_id", 0))

    # 2. 現在の最大 id を取得
    max_id = get_max_audit_id(context_key)
    if max_id == 0:
        # まだログがない
        return None

    # 3. 更新不要なら既存 summary を返す
    existing_summary = state.get("summary")
    if existing_summary and max_id <= last_audit_id:
        return existing_summary

    # 4. audit_log（最大 SUMMARY_MAX_EVENTS 件）取得
    logs = get_audit_log(context_key, limit=SUMMARY_MAX_EVENTS)
    if not logs:
        return existing_summary  # 取れなかった場合は既存を妥協利用

    # 5. 新しい summary を生成
    summary = build_summary_from_logs(context_key, logs)
    if not summary:
        return existing_summary

    # 6. runtime_state を更新・保存
    new_state = dict(state)
    new_state["summary"] = summary
    new_state["last_audit_id"] = max_id
    new_state["summary_updated_at"] = datetime.now(timezone.utc).isoformat()

    save_runtime_state(context_key, new_state)

    log_audit(
        "summary_updated",
        {
            "context_key": str(context_key),
            "last_audit_id": max_id,
            "summary_len": len(summary),
        },
    )

    return summary


# ============================================================
# 2. Notion CRUD（いまは未使用だが audit 付きで残す）
# ============================================================

async def create_task(name, goal, thread_id, channel_id):
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(channel_id)}}]},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return page["id"]
    except Exception as e:
        log_audit(
            "notion_error",
            {
                "op": "create_task",
                "name": name,
                "goal": goal,
                "thread_id": thread_id,
                "channel_id": channel_id,
                "error": repr(e),
            },
        )
        return None


async def start_session(task_id, name, thread_id):
    now = datetime.now(timezone.utc)
    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "start_time": {"date": {"start": now.isoformat()}},
                "created_at": {"date": {"start": now.isoformat()}},
                "updated_at": {"date": {"start": now.isoformat()}},
            },
        )
        return page["id"]
    except Exception as e:
        log_audit(
            "notion_error",
            {
                "op": "start_session",
                "task_id": task_id,
                "thread_id": thread_id,
                "error": repr(e),
            },
        )
        return None


async def end_session(session_id, summary):
    now = datetime.now(timezone.utc).isoformat()
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": now}},
                "summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
                "updated_at": {"date": {"start": now}},
            },
        )
        return True
    except Exception as e:
        log_audit(
            "notion_error",
            {
                "op": "end_session",
                "session_id": session_id,
                "error": repr(e),
            },
        )
        return False


async def append_logs(session_id, logs):
    try:
        for log_row in logs:
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log_row["author"]}}]},
                    "content": {"rich_text": [{"text": {"content": log_row["content"][:2000]}}]},
                    "created_at": {"date": {"start": log_row["created_at"]}},
                    "discord_message_id": {"rich_text": [{"text": {"content": log_row["id"]}}]},
                },
            )
        return True
    except Exception as e:
        log_audit(
            "notion_error",
            {
                "op": "append_logs",
                "session_id": session_id,
                "log_count": len(logs),
                "error": repr(e),
            },
        )
        return False


# ============================================================
# 3. In-memory Ovv Memory（直近 40 件）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 4. Load core / Soft-Core
# ============================================================

def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]

1. MUST keep user experience primary; MUST NOT become over-strict.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination / unjustified assumptions / over-generalization.
4. MUST respect scope boundaries; MUST NOT add requirements user did not ask.
5. SHOULD decompose → reconstruct for stable answers.
6. MUST NOT mix reasoning and answer (phase-mixing).
7. MAY trigger CDC if needed, but MUST NOT overuse it.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタントです。
ユーザー体験を最優先し、過剰な厳格化を避けてください。
次の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. Ovv Call（summary 組み込み）
# ============================================================

def call_ovv(context_key: int, text: str) -> str:
    """
    messages 構成：
      system
      OVV_CORE
      OVV_EXTERNAL
      + audit_summary（あれば）
      + in-memory OVV_MEMORY（直近 40件）
      + user message
    """
    # 必要ならサマリを取得/生成（PG なしなら None）
    summary = get_or_build_summary(context_key)

    msgs: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    if summary:
        msgs.append({
            "role": "assistant",
            "content": "[OVV_THREAD_SUMMARY]\n" + summary,
        })

    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()
        push_mem(context_key, "assistant", ans)

        log_audit(
            "assistant_reply",
            {
                "length": len(ans),
                "content": ans[:500],
            },
            context_key=context_key,
        )

        return ans[:1900]

    except Exception as e:
        print("[ERROR call_ovv]", repr(e))
        log_audit(
            "openai_error",
            {
                "text": text[:500],
                "error": repr(e),
            },
            context_key=context_key,
        )
        return "Ovv との通信中にエラーが発生しました。少し時間をおいて再実行してください。"


# ============================================================
# 6. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id


# ============================================================
# 7. on_message（エラーも audit して握りこむ）
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    # bot 自身や system message は無視
    if message.author.bot:
        return

    try:
        # ovv-* チャンネルのみ対象
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        ck = get_context_key(message)

        # コマンドはそのまま commands へ
        if message.content.startswith("!"):
            log_audit(
                "command",
                {
                    "command": message.content.split()[0],
                    "author": str(message.author),
                },
                context_key=ck,
            )
            await bot.process_commands(message)
            return

        # 通常メッセージ
        push_mem(ck, "user", message.content)

        log_audit(
            "user_message",
            {
                "author": str(message.author),
                "length": len(message.content),
                "content": message.content[:500],
            },
            context_key=ck,
        )

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        print("[ERROR on_message]", repr(e))
        log_audit(
            "discord_error",
            {
                "where": "on_message",
                "error": repr(e),
            },
        )
        try:
            await message.channel.send("内部エラーが発生しました。少し待ってから再度お試しください。")
        except Exception:
            pass


# ============================================================
# 8. Commands（例: ping）
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        log_audit(
            "command",
            {
                "command": "!ping",
                "author": str(ctx.author),
            },
            context_key=ck,
        )
        await ctx.send("pong")
    except Exception as e:
        print("[ERROR command ping]", repr(e))
        log_audit(
            "discord_error",
            {
                "where": "command_ping",
                "error": repr(e),
            },
        )


# ============================================================
# 9. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
