<ここからそのままコピー>

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
# 2. Notion CRUD（不変）
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
        print("[ERROR start_session]", repr(e))
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
        print("[ERROR end_session]", repr(e))
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
        print("[ERROR append_logs]", repr(e))
        log_audit(
            "notion_error",
            {"op": "append_logs", "session_id": session_id, "log_count": len(logs), "error": repr(e)},
        )
        return False


# ============================================================
# 3. OVV MEMORY
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 4. CORE読み込み
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
# 5. thread_brain（スレッド脳） utilities
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
        print("[thread_brain] load error:", repr(e))
        log_audit("thread_brain_load_error", {"context_key": context_key, "error": repr(e)})
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
        log_audit(
            "thread_brain_saved",
            {"context_key": context_key, "summary_keys": list(summary.keys())},
        )
        return True
    except Exception as e:
        print("[thread_brain] save error:", repr(e))
        log_audit("thread_brain_save_error", {"context_key": context_key, "error": repr(e)})
        return False


def _build_thread_brain_prompt(context_key: int) -> str:
    mem = OVV_MEMORY.get(context_key, [])
    recent = mem[-30:] if len(mem) > 30 else mem

    lines: List[str] = []
    for m in recent:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        prefix = "USER" if role == "user" else "ASSISTANT"
        short = content.replace("\n", " ")
        if len(short) > 500:
            short = short[:500] + " ...[truncated]"
        lines.append(f"{prefix}: {short}")

    history_block = "\n".join(lines) if lines else "(対話履歴がほとんどありません)"

    prev_summary = load_thread_brain(context_key)
    if prev_summary is not None:
        prev_summary_text = json.dumps(prev_summary, ensure_ascii=False)
    else:
        prev_summary_text = "null"

    body = f"""
あなたは「thread_brain」という名称の、Discord スレッド専用の長期メモリを設計するアシスタントです。

目的：
- このスレッドの「現在の状況」「ここまでの流れ」「決定事項」「未解決事項」「次にやるべきこと」を、
  後から見返してもすぐ分かる形で JSON サマリに落とし込むこと。
- JSON は **Ovv thread_brain schema** に完全準拠し、キー名・構造を絶対に変えないこと。

出力フォーマット（必ずこの JSON 構造のみを返すこと）：
{{
  "meta": {{
    "version": "1.0",
    "updated_at": "<ISO8601 UTC>",
    "context_key": <int>,
    "total_tokens_estimate": <int>
  }},
  "status": {{
    "phase": "<idle|active|blocked|done など簡潔な1語>",
    "last_major_event": "<直近で重要だった出来事を一文で>",
    "risk": [
      "<このスレッド固有のリスク1>",
      "<リスク2>"
    ]
  }},
  "decisions": [
    "<今のところ確定している決定事項1>",
    "<決定事項2>"
  ],
  "unresolved": [
    "<まだ決まっていない論点1>",
    "<未解決の課題2>"
  ],
  "constraints": [
    "<前提条件や制約1>",
    "<制約2>"
  ],
  "next_actions": [
    "<このスレッドで次にやるべき具体的な一手1>",
    "<次のアクション2>"
  ],
  "history_digest": "<ここまでの流れを 28000 文字以内で要約する。重要な経緯と転換点を優先し、重複は避ける。>",
  "high_level_goal": "<このスレッドが最終的に目指している状態を一文で>",
  "recent_messages": [
    "<直近の出来事や発言1>",
    "<直近の出来事や発言2>"
  ],
  "current_position": "<今このスレッドが全体のどの辺りにいるかを、一段高い視点から説明した一文>"
}}

重要な制約：
- 出力は **上記 JSON 1 個のみ**。説明文やマークダウン、コードブロックは一切付けないこと。
- キー名は絶対に変更しないこと（例: "meta", "status", "decisions" など）。
- "history_digest" は最大でも 28000 文字以内に収めること。
- 対話ログが少ない場合でも、構造は同じにし、内容が無い項目は短く「特記事項なし」などで埋めること。
- 「前回の summary」が与えられている場合は、それを尊重しつつ差分をアップデートする形で再構成すること。

[前回の summary（無ければ null）]
{prev_summary_text}

[直近の対話ログのダイジェスト]
{history_block}
"""
    return body


def generate_thread_brain(context_key: int) -> Optional[dict]:
    prompt_body = _build_thread_brain_prompt(context_key)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは構造化サマリ専用 AI です。必ず JSON のみを返してください。"
                    ),
                },
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.2,
        )
        raw = res.choices[0].message.content.strip()
    except Exception as e:
        print("[thread_brain] LLM call error:", repr(e))
        log_audit("thread_brain_llm_error", {"context_key": context_key, "error": repr(e)})
        return None

    text = raw
    if "```" in text:
        parts = text.split("```")
        candidates = [p for p in parts if "{" in p and "}" in p]
        if candidates:
            text = max(candidates, key=len)

    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    summary: Optional[dict] = None
    try:
        summary = json.loads(text)
    except Exception as e:
        print("[thread_brain] JSON parse error:", repr(e))
        log_audit(
            "thread_brain_parse_error",
            {"context_key": context_key, "raw_preview": raw[:500], "error": repr(e)},
        )
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    meta = summary.get("meta", {}) if isinstance(summary, dict) else {}
    meta["version"] = "1.0"
    meta["updated_at"] = now_iso
    meta["context_key"] = context_key
    if "total_tokens_estimate" not in meta:
        meta["total_tokens_estimate"] = 0
    summary["meta"] = meta

    log_audit(
        "thread_brain_generated",
        {
            "context_key": context_key,
            "has_history_digest": bool(summary.get("history_digest")),
        },
    )

    return summary


# ============================================================
# 6. Ovv Call
# ============================================================

def call_ovv(context_key: int, text: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

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

        log_audit("assistant_reply", {"context_key": context_key, "length": len(ans)})
        return ans[:1900]

    except Exception as e:
        print("[ERROR call_ovv]", repr(e))
        log_audit(
            "openai_error",
            {"context_key": context_key, "user_text": text[:500], "error": repr(e)},
        )
        return "Ovv との通信中にエラーが発生しました。少し時間をおいて再実行してください。"


# ============================================================
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return (msg.guild.id << 32) | msg.channel.id


# ============================================================
# 8. on_message（thread_brain 自動生成フック付き）
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

        new_summary = generate_thread_brain(ck)
        if new_summary:
            save_thread_brain(ck, new_summary)

        ans = call_ovv(ck, message.content)
        await message.channel.send(ans)

    except Exception as e:
        print("[ERROR on_message]", repr(e))
        log_audit("discord_error", {"where": "on_message", "error": repr(e)})
        try:
            await message.channel.send("内部エラーが発生しました。再度お試しください。")
        except Exception:
            pass


# ============================================================
# 9. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx: commands.Context):
    try:
        log_audit("command", {"command": "!ping", "author": str(ctx.author)})
        await ctx.send("pong")
    except Exception as e:
        log_audit("discord_error", {"where": "command_ping", "error": repr(e)})


@bot.command(name="brain_regen")
async def brain_regen(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        summary = generate_thread_brain(ck)
        if summary:
            save_thread_brain(ck, summary)
            await ctx.send("thread_brain を再生成し保存しました。")
        else:
            await ctx.send("thread_brain の再生成に失敗しました。")
    except Exception as e:
        log_audit("discord_error", {"where": "brain_regen", "error": repr(e)})
        await ctx.send("内部エラーが発生しました。")


@bot.command(name="brain_show")
async def brain_show(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        summary = load_thread_brain(ck)
        if not summary:
            await ctx.send("thread_brain はまだ存在しません。")
            return

        updated = summary.get("meta", {}).get("updated_at", "?")
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if len(text) > 1800:
            text = text[:1800] + "\n...[truncated]"

        await ctx.send(
            f"thread_brain summary（{ck}）\nupdated_at={updated}\n```json\n{text}\n```"
        )

    except Exception as e:
        log_audit("discord_error", {"where": "brain_show", "error": repr(e)})
        await ctx.send("内部エラーが発生しました。")


@bot.command(name="test_thread")
async def test_thread(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        summary = generate_thread_brain(ck)
        if not summary:
            await ctx.send("thread_brain 生成失敗")
            return

        save_thread_brain(ck, summary)
        reloaded = load_thread_brain(ck)

        await ctx.send(f"thread_brain test OK\nsummary keys={list(reloaded.keys())}")

    except Exception as e:
        log_audit("discord_error", {"where": "test_thread", "error": repr(e)})
        await ctx.send("内部エラーが発生しました。")


# ============================================================
# 10. Bootstrap PG + Run
# ============================================================

print("=== [BOOT] Preparing PostgreSQL connect ===")
conn = pg_connect()
init_db(conn)
print("=== [BOOT] Database setup finished ===")

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)

<ここまで>
