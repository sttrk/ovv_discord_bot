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

PG_CONN = None          
AUDIT_READY = False     

def pg_connect():
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
    global AUDIT_READY

    print("=== [PG] init_db() CALLED ===")

    if conn is None:
        print("[PG] init_db skipped (no connection)")
        AUDIT_READY = False
        return

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS ovv;
        """)

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


def log_audit(event_type: str, details: Optional[dict] = None):

    if details is None:
        details = {}
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
# 1.6 thread_brain 読み込み
# ============================================================

def load_thread_brain(context_key: int):
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
                (context_key,)
            )
            row = cur.fetchone()
            if row and row["summary"]:
                return row["summary"]
            return None
    except Exception as e:
        print("[PG load_thread_brain ERROR]", repr(e))
        return None


def load_recent_audit(context_key: int, limit: int = 10):
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT event_type,
                       details,
                       created_at
                FROM ovv.audit_log
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,)
            )
            rows = cur.fetchall()
            result = []

            for r in rows:
                result.append({
                    "event": r["event_type"],
                    "at": r["created_at"].isoformat(),
                    "details": r["details"]
                })

            return result

    except Exception as e:
        print("[PG load_recent_audit ERROR]", repr(e))
        return []


# ============================================================
# 2. Notion CRUD（現在は固定的だが audit 保持）
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
        print("[ERROR create_task]", repr(e))
        log_audit(
            "notion_error",
            {
                "op": "create_task",
                "name": name,
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
        print("[ERROR start_session]", repr(e))
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
        print("[ERROR end_session]", repr(e))
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
        for log in logs:
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": log["created_at"]}},
                    "discord_message_id": {"rich_text": [{"text": {"content": log["id"]}}]},
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", repr(e))
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
# 3. Ovv Memory（in-memory）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 4. Load core
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
# 5. call_ovv（thread_brain + audit + volatile memory を統合）
# ============================================================

def call_ovv(context_key: int, text: str) -> str:

    brain_summary = load_thread_brain(context_key)
    recent_audit = load_recent_audit(context_key, limit=10)

    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    if brain_summary:
        msgs.append({
            "role": "assistant",
            "content": json.dumps({"thread_brain": brain_summary}, ensure_ascii=False)
        })

    if recent_audit:
        msgs.append({
            "role": "assistant",
            "content": json.dumps({"recent_audit": recent_audit}, ensure_ascii=False)
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
                "context_key": context_key,
                "length": len(ans),
            },
        )

        return ans[:1900]

    except Exception as e:
        print("[ERROR call_ovv]", repr(e))
        log_audit(
            "openai_error",
            {
                "context_key": context_key,
                "user_text": text[:500],
                "error": repr(e),
            },
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
# 7. thread_brain commands
# ============================================================

@bot.command(name="brain_show")
async def brain_show(ctx: commands.Context):
    if PG_CONN is None:
        await ctx.send("PG 未接続")
        return

    ck = get_context_key(ctx.message)
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT summary, updated_at FROM ovv.thread_brain WHERE context_key=%s",
                (ck,)
            )
            row = cur.fetchone()

        if not row:
            await ctx.send("thread_brain 未生成")
        else:
            await ctx.send(
                f"thread_brain summary（{ck}）\nupdated_at={row['updated_at']}\n```json\n{json.dumps(row['summary'], ensure_ascii=False, indent=2)}\n```"
            )

    except Exception as e:
        await ctx.send("brain_show でエラー")
        print("[ERROR brain_show]", repr(e))


@bot.command(name="brain_maintain")
async def brain_maintain(ctx: commands.Context):
    if PG_CONN is None:
        await ctx.send("PG 未接続")
        return

    ck = get_context_key(ctx.message)

    summary_obj = {
        "status": {"phase": "active"},
        "meta": {"updated_at": datetime.now(timezone.utc).isoformat()},
        "content": {"audit_events": [...]},
    }

    try:
        with PG_CONN.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (context_key)
                DO UPDATE SET summary = EXCLUDED.summary,
                              updated_at = NOW();
                """,
                (ck, json.dumps(summary_obj))
            )

        await ctx.send("thread_brain を更新しました")

    except Exception as e:
        await ctx.send("brain_maintain でエラー")
        print("[ERROR brain_maintain]", repr(e))


# ============================================================
# 8. on_message（すべての user メッセージの起点）
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    try:
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            if not parent or not parent.name.lower().startswith("ovv-"):
                return
        else:
            if not message.channel.name.lower().startswith("ovv-"):
                return

        if message.content.startswith("!"):
            log_audit(
                "command",
                {
                    "command": message.content.split()[0],
                    "author": str(message.author),
                    "channel_id": message.channel.id,
                    "guild_id": message.guild.id if message.guild else None,
                },
            )
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        push_mem(ck, "user", message.content)

        log_audit(
            "user_message",
            {
                "context_key": ck,
                "author": str(message.author),
                "channel_id": message.channel.id,
                "guild_id": message.guild.id if message.guild else None,
                "length": len(message.content),
            },
        )

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        print("[ERROR on_message]", repr(e))
        log_audit(
            "discord_error",
            {
                "where": "on_message",
                "message_id": message.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id if message.guild else None,
                "error": repr(e),
            },
        )
        try:
            await message.channel.send("内部エラーが発生しました。少し待ってから再度お試しください。")
        except Exception:
            pass


# ============================================================
# 9. Commands（例: ping）
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    try:
        log_audit(
            "command",
            {
                "command": "!ping",
                "author": str(ctx.author),
                "channel_id": ctx.channel.id,
                "guild_id": ctx.guild.id if ctx.guild else None,
            },
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
# 10. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
