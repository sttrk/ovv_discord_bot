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

        # 永続メモリ（今後の拡張用）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.runtime_memory (
                session_id TEXT PRIMARY KEY,
                memory_json JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # 監査ログ
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ovv.audit_log (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                details JSONB,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # thread_brain（スレッド単位サマリ）
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
    PG が死んでいる場合やテーブル未初期化時は print のみ。
    """
    if details is None:
        details = {}

    try:
        print(f"[AUDIT] {event_type} :: {details}")
    except Exception:
        # print 失敗は無視
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
# 2. Notion CRUD（現状は未使用だが仕様として維持）
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
# 3. OVV MEMORY（in-memory コンテキスト）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40


def push_mem(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 4. CORE 読み込み
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
    """
    ovv.thread_brain から context_key の summary(JSONB) を取得。
    PG が使えない場合や行が存在しない場合は None。
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
        print("[thread_brain] load error:", repr(e))
        log_audit(
            "thread_brain_load_error",
            {
                "context_key": context_key,
                "error": repr(e),
            },
        )
        return None


def save_thread_brain(context_key: int, summary: dict) -> bool:
    """
    ovv.thread_brain に summary(JSONB) を UPSERT（挿入 or 更新）。
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
                    summary = EXCLUDED.summary,
                    updated_at = NOW();
                """,
                (context_key, json.dumps(summary, ensure_ascii=False)),
            )

        log_audit(
            "thread_brain_saved",
            {
                "context_key": context_key,
                "summary_keys": list(summary.keys()),
            },
        )
        return True

    except Exception as e:
        print("[thread_brain] save error:", repr(e))
        log_audit(
            "thread_brain_save_error",
            {
                "context_key": context_key,
                "error": repr(e),
            },
        )
        return False


def _build_thread_brain_prompt(context_key: int) -> str:
    """
    memory と既存 summary をまとめ、LLM が JSON サマリを生成できるようにする。
    """
    mem = OVV_MEMORY.get(context_key, [])
    recent = mem[-30:] if len(mem) > 30 else mem

    # ------------------------
    # 履歴（短縮）
    # ------------------------
    lines: List[str] = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").replace("\n", " ")

        prefix = "USER" if role == "user" else "ASSISTANT"

        if len(content) > 500:
            content = content[:500] + " ...[truncated]"

        lines.append(f"{prefix}: {content}")

    history_block = "\n".join(lines) if lines else "(対話履歴がありません)"

    # ------------------------
    # 既存 summary
    # ------------------------
    prev = load_thread_brain(context_key)
    prev_summary_text = json.dumps(prev, ensure_ascii=False) if prev else "null"

    # ------------------------
    # Prompt 本体
    # ------------------------
    body = f"""
あなたは「thread_brain」という名称の、Discord スレッド専用の長期メモリ AI です。

目的：
- 「現在の状況」「これまでの流れ」「決定事項」「未解決事項」「次アクション」を JSON で提供する。
- JSON は Ovv thread_brain schema に完全準拠する。
- キー名・構造を絶対に変更しないこと。

出力 JSON（必ずこの構造のみ）：
{{
  "meta": {{
    "version": "1.0",
    "updated_at": "<ISO8601>",
    "context_key": <int>,
    "total_tokens_estimate": <int>
  }},
  "status": {{
    "phase": "<idle|active|blocked|done>",
    "last_major_event": "<一文>",
    "risk": ["<リスク1>", "<リスク2>"]
  }},
  "decisions": ["<決定1>", "<決定2>"],
  "unresolved": ["<論点1>", "<課題2>"],
  "constraints": ["<制約1>", "<制約2>"],
  "next_actions": ["<次の一手1>", "<次の一手2>"],
  "history_digest": "<28000文字以内>",
  "high_level_goal": "<最終目標>",
  "recent_messages": ["<最近の出来事1>", "<最近の出来事2>"],
  "current_position": "<現在地を簡潔に>"
}}

制約：
- JSON のみ返す。コードブロック禁止。
- 28000 文字制限を守る。
- 前回 summary がある場合は継承しつつアップデートする。

[前回 summary]
{prev_summary_text}

[直近ログ]
{history_block}
"""

    return body


def generate_thread_brain(context_key: int) -> Optional[dict]:
    """
    LLM を呼び出して、thread_brain JSON を生成する。
    """
    body = _build_thread_brain_prompt(context_key)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "あなたは構造化サマリ専用 AI です。JSON のみ返してください。",
                },
                {
                    "role": "user",
                    "content": body,
                },
            ],
        )
        raw = res.choices[0].message.content.strip()

    except Exception as e:
        print("[thread_brain] LLM error:", repr(e))
        log_audit(
            "thread_brain_llm_error",
            {"context_key": context_key, "error": repr(e)},
        )
        return None

    # ------------------------
    # JSON 抽出
    # ------------------------
    text = raw
    if "```" in text:
        parts = text.split("```")
        cands = [p for p in parts if "{" in p and "}" in p]
        if cands:
            text = max(cands, key=len)

    text = text.strip()
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1:
        text = text[s:e+1]

    try:
        summary = json.loads(text)
    except Exception as e:
        print("[thread_brain] JSON parse error:", repr(e))
        log_audit(
            "thread_brain_parse_error",
            {
                "context_key": context_key,
                "raw_preview": raw[:500],
                "error": repr(e),
            },
        )
        return None

    # ------------------------
    # meta 補完
    # ------------------------
    now = datetime.now(timezone.utc).isoformat()
    meta = summary.get("meta", {})
    meta["version"] = "1.0"
    meta["updated_at"] = now
    meta["context_key"] = context_key
    meta.setdefault("total_tokens_estimate", 0)
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
# 6. Ovv Call（OpenAI エラーを audit + graceful fallback）
# ============================================================

def call_ovv(context_key: int, text: str) -> str:
    """
    Ovv Soft-Core + OVV_CORE + OVV_EXTERNAL + メモリを投入して応答を生成する。
    Phase 2：まだ thread_brain の summary は推論に使用しない。
    """
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    # メモリを注入
    msgs.extend(OVV_MEMORY.get(context_key, []))

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        # メモリに保存
        push_mem(context_key, "assistant", ans)

        log_audit(
            "assistant_reply",
            {
                "context_key": context_key,
                "length": len(ans),
            },
        )

        # Discord 制限に配慮して 1900 文字に収める
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
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


def get_context_key(msg: discord.Message) -> int:
    """
    すべてのメッセージを一意に識別する context_key を生成する。
    Thread:           thread.id
    Guild チャンネル: (guild_id << 32) | channel_id
    DM:               channel.id
    """
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id

    if msg.guild is None:
        return msg.channel.id

    return (msg.guild.id << 32) | msg.channel.id


# ============================================================
# 8. on_message（全チャンネル対応 + thread_brain 自動更新）
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    try:
        # -----------------------------
        # コマンド処理
        # -----------------------------
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

        # -----------------------------
        # 通常メッセージ → メモリ保存
        # -----------------------------
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

        # -----------------------------
        # thread_brain summary を毎回更新
        # -----------------------------
        summary = generate_thread_brain(ck)
        if summary:
            save_thread_brain(ck, summary)

        # -----------------------------
        # 通常 Ovv 応答
        # -----------------------------
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
            await message.channel.send("内部エラーが発生しました。再度お試しください。")
        except Exception:
            pass


# ============================================================
# 9. Commands（短縮版コマンド名を正式採用）
# ============================================================

# ----------------------
# !p → ping
# ----------------------
@bot.command(name="p")
async def ping_short(ctx: commands.Context):
    try:
        log_audit("command", {"command": "!p", "author": str(ctx.author)})
        await ctx.send("pong")
    except Exception as e:
        log_audit("discord_error", {"where": "command_p", "error": repr(e)})


# ----------------------
# !br → brain_regen
# ----------------------
@bot.command(name="br")
async def brain_regen_short(ctx: commands.Context):
    try:
        ck = get_context_key(ctx.message)
        summary = generate_thread_brain(ck)

        if summary:
            save_thread_brain(ck, summary)
            await ctx.send("thread_brain を再生成し保存しました。")
        else:
            await ctx.send("thread_brain の再生成に失敗しました。")

    except Exception as e:
        log_audit("discord_error", {"where": "br", "error": repr(e)})
        await ctx.send("内部エラーが発生しました。")


# ----------------------
# !bs → brain_show
# ----------------------
@bot.command(name="bs")
async def brain_show_short(ctx: commands.Context):
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

        await ctx.send(f"thread_brain summary（{ck}）\nupdated_at={updated}\n```json\n{text}\n```")

    except Exception as e:
        log_audit("discord_error", {"where": "bs", "error": repr(e)})
        await ctx.send("内部エラーが発生しました。")


# ----------------------
# !bt → test_thread
# ----------------------
@bot.command(name="bt")
async def test_thread_short(ctx: commands.Context):
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
        log_audit("discord_error", {"where": "bt", "error": repr(e)})
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
