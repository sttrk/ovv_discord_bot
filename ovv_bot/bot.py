import os
import json
import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import Dict, List, Optional
from datetime import datetime, timezone
import psycopg2
import psycopg2.extras

# ============================================================
# [DEBUG HOOK] imports
# ============================================================
from debug.debug_router import route_debug_message
# debug_commands は debug_router 内で呼ばれるため、ここでは import 不要


# ============================================================
# 1. Environment
# ============================================================

print("=== [BOOT] Loading environment variables ===")

from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
    NOTION_TASKS_DB_ID,
    NOTION_SESSIONS_DB_ID,
    NOTION_LOGS_DB_ID,
    POSTGRES_URL,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1.5 PostgreSQL connect + init + audit_log
# ============================================================

PG_CONN = None
AUDIT_READY = False


def pg_connect():
    global PG_CONN
    print("=== [PG] Connecting ===")

    if not POSTGRES_URL:
        print("[PG] POSTGRES_URL missing, skip.")
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
# 2. Notion CRUD
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
        log_audit("notion_error", {"op": "create_task", "name": name, "error": repr(e)})
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
        log_audit("notion_error", {"op": "start_session", "task_id": task_id, "error": repr(e)})
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
        log_audit("notion_error", {"op": "end_session", "session_id": session_id, "error": repr(e)})
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
        log_audit(
            "notion_error",
            {"op": "append_logs", "session_id": session_id, "log_count": len(logs), "error": repr(e)},
        )
        return False

# ============================================================
# 3. Runtime Memory
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    if PG_CONN is None:
        return []
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT memory_json
                FROM ovv.runtime_memory
                WHERE session_id = %s
            """, (session_id,))
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
            cur.execute("""
                INSERT INTO ovv.runtime_memory (session_id, memory_json, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    memory_json = EXCLUDED.memory_json,
                    updated_at  = NOW();
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
# 4. Soft-Core / Core / External
# ============================================================

def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

from ovv.core_loader import load_core, load_external
OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary; MUST NOT become over-strict.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination.
4. MUST respect scope boundaries.
5. SHOULD decompose → reconstruct for stability.
6. MUST NOT phase-mix.
7. MAY trigger CDC but sparingly.
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタントです。
ユーザー体験を最優先し、過剰な厳格化を避けてください。
次の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. thread_brain utilities
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
    if PG_CONN is None:
        return None
    try:
        with PG_CONN.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT summary
                FROM ovv.thread_brain
                WHERE context_key = %s
            """, (context_key,))
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
            cur.execute("""
                INSERT INTO ovv.thread_brain (context_key, summary, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (context_key)
                DO UPDATE SET
                    summary   = EXCLUDED.summary,
                    updated_at = NOW();
            """, (context_key, json.dumps(summary, ensure_ascii=False)))
        return True
    except Exception as e:
        print("[thread_brain save error]", repr(e))
        return False


def _build_thread_brain_prompt(context_key: int, recent_mem: List[dict]) -> str:

    # short digest
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

重要: JSON 以外の文字を返してはならない。

[前回の summary]
{prev_summary_text}

[直近ログ]
{history_block}
""".strip()


def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "必ず JSON のみを返す。"},
                {"role": "user", "content": prompt_body}
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content.strip()
    except Exception as e:
        print("[thread_brain LLM error]", repr(e))
        return None

    # Extract JSON
    txt = raw
    if "```" in raw:
        parts = raw.split("```")
        cands = [p for p in parts if "{" in p and "}" in p]
        if cands:
            txt = max(cands, key=len)

    txt = txt.strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start == -1 or end == -1:
        return None

    try:
        summary = json.loads(txt[start:end+1])
    except Exception as e:
        print("[thread_brain JSON error]", repr(e))
        return None

    summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    summary["meta"]["context_key"] = context_key

    return summary

# ============================================================
# 6. Ovv Call
# ============================================================

def call_ovv(context_key: int, text: str, recent_mem: List[dict]) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    # Inject runtime memory
    for m in recent_mem[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        append_runtime_memory(str(context_key), "assistant", ans)

        log_audit("assistant_reply", {
            "context_key": context_key,
            "length": len(ans),
        })

        return ans[:1900]
    except Exception as e:
        print("[call_ovv error]", repr(e))
        log_audit("openai_error", {
            "context_key": context_key,
            "user_text": text[:500],
            "error": repr(e),
        })
        return "Ovv との通信中にエラーが発生しました。"


# ============================================================
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    if msg.guild is None:
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id


def is_task_channel(message: discord.Message) -> bool:
    if isinstance(message.channel, discord.Thread):
        parent = message.channel.parent
        if parent:
            return parent.name.lower().startswith("task_")
        return False
    else:
        return message.channel.name.lower().startswith("task_")


# ============================================================
# 8. on_message（ここに DEBUG HOOK を統合）
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # --------------------------------------------------------
    # [DEBUG HOOK] debug_router を最上流に配置
    # --------------------------------------------------------
    handled = await route_debug_message(bot, message)
    if handled:
        return
    # --------------------------------------------------------

    try:
        # 1. コマンド処理
        if message.content.startswith("!"):
            log_audit("command", {
                "command": message.content.split()[0],
                "author": str(message.author),
                "channel_id": message.channel.id,
            })
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        session_id = str(ck)

        append_runtime_memory(session_id, "user", message.content,
                              limit=40 if is_task_channel(message) else 12)

        recent_mem = load_runtime_memory(session_id)

        task_mode = is_task_channel(message)

        if task_mode:
            summary = generate_thread_brain(ck, recent_mem)
            if summary:
                save_thread_brain(ck, summary)

        ans = call_ovv(ck, message.content, recent_mem)

        await message.channel.send(ans)

    except Exception as e:
        print("[on_message error]", repr(e))
        log_audit("discord_error", {
            "where": "on_message",
            "error": repr(e),
        })
        try:
            await message.channel.send("内部エラーが発生しました。")
        except:
            pass


# ============================================================
# 9. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx: commands.Context):
    ck = get_context_key(ctx.message)
    session_id = str(ck)
    recent = load_runtime_memory(session_id)

    summary = generate_thread_brain(ck, recent)
    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx: commands.Context):
    ck = get_context_key(ctx.message)
    summary = load_thread_brain(ck)

    if not summary:
        await ctx.send("thread_brain はまだありません。")
        return

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    await ctx.send(f"```json\n{text}\n```")


@bot.command(name="tt")
async def test_thread(ctx: commands.Context):
    ck = get_context_key(ctx.message)
    session_id = str(ck)
    mem = load_runtime_memory(session_id)

    summary = generate_thread_brain(ck, mem)
    if not summary:
        await ctx.send("thread_brain 生成失敗")
        return

    save_thread_brain(ck, summary)

    await ctx.send("test OK: summary saved")


# ============================================================
# 10. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
