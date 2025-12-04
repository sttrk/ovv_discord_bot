import os
import json
import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client
from typing import List, Optional
from datetime import datetime, timezone

# ============================================================
# [DEBUG HOOK] imports
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# 0. PostgreSQL Module (module import only)
# ============================================================
import database.pg as db_pg


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

print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

def log_audit(event_type: str, details: Optional[dict] = None):
    return db_pg.log_audit(event_type, details)


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
        log_audit("notion_error", {"op": "create_task", "error": repr(e)})
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
        log_audit("notion_error", {"op": "start_session", "error": repr(e)})
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
        log_audit("notion_error", {"op": "end_session", "error": repr(e)})
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
        log_audit("notion_error", {"op": "append_logs", "error": repr(e)})
        return False


# ============================================================
# 3. Runtime Memory (proxy to db_pg)
# ============================================================

def load_runtime_memory(session_id: str) -> List[dict]:
    return db_pg.load_runtime_memory(session_id)


def save_runtime_memory(session_id: str, mem: List[dict]):
    return db_pg.save_runtime_memory(session_id, mem)


def append_runtime_memory(session_id: str, role: str, content: str, limit: int = 40):
    return db_pg.append_runtime_memory(session_id, role, content, limit)


# ============================================================
# 4. Soft-Core / Core / External
# ============================================================

from ovv.core_loader import load_core, load_external
OVV_CORE = load_core()
OVV_EXTERNAL = load_external()

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]
1. MUST keep user experience primary
2. MUST use Clarify only when needed
3. MUST avoid hallucination
4. MUST respect boundaries
5. SHOULD decompose → reconstruct
6. MUST NOT phase-mix
7. MAY trigger CDC sparingly
""".strip()

SYSTEM_PROMPT = f"""
あなたは Discord 上で動作するアシスタントです。
次の Ovv Soft-Core を保持してください。

{OVV_SOFT_CORE}
""".strip()


# ============================================================
# 5. thread_brain utilities (DB I/O は db_pg、LLM 呼び出しはここ)
# ============================================================

def load_thread_brain(context_key: int) -> Optional[dict]:
    return db_pg.load_thread_brain(context_key)


def save_thread_brain(context_key: int, summary: dict) -> bool:
    return db_pg.save_thread_brain(context_key, summary)


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
必ず JSON のみで返答。

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

[前回 summary]
{prev_summary_text}

[recent logs]
{history_block}
""".strip()


def generate_thread_brain(context_key: int, recent_mem: List[dict]) -> Optional[dict]:
    prompt_body = _build_thread_brain_prompt(context_key, recent_mem)

    try:
        res = openai_client.chat.completions.create(
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
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1 or end == -1:
        return None

    try:
        summary = json.loads(txt[start:end+1])
    except Exception as e:
        print("[thread_brain JSON error]", repr(e))
        return None

    summary.setdefault("meta", {})
    summary["meta"]["context_key"] = context_key
    summary["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
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

    for m in recent_mem[-20:]:
        msgs.append({"role": m.get("role", "user"), "content": m.get("content", "")})

    msgs.append({"role": "user", "content": text})

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=msgs,
            temperature=0.7,
        )
        ans = res.choices[0].message.content.strip()

        append_runtime_memory(str(context_key), "assistant", ans)
        log_audit("assistant_reply", {"context_key": context_key, "length": len(ans)})

        return ans[:1900]

    except Exception as e:
        print("[call_ovv error]", repr(e))
        log_audit("openai_error", {"context_key": context_key, "error": repr(e)})
        return "Ovv との通信中にエラーが発生しました。"


# ============================================================
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


def is_task_channel(message: discord.Message) -> bool:
    ch = message.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return parent.name.lower().startswith("task_") if parent else False
    return ch.name.lower().startswith("task_")


# ============================================================
# 8. on_message + DEBUG HOOK
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    handled = await route_debug_message(bot, message)
    if handled:
        return

    try:
        if message.content.startswith("!"):
            log_audit("command", {"cmd": message.content})
            await bot.process_commands(message)
            return

        ck = get_context_key(message)
        session_id = str(ck)

        append_runtime_memory(
            session_id,
            "user",
            message.content,
            limit=40 if is_task_channel(message) else 12,
        )

        mem = load_runtime_memory(session_id)

        if is_task_channel(message):
            summary = generate_thread_brain(ck, mem)
            if summary:
                save_thread_brain(ck, summary)

        ans = call_ovv(ck, message.content, mem)
        await message.channel.send(ans)

    except Exception as e:
        print("[on_message error]", repr(e))
        log_audit("discord_error", {"error": repr(e)})
        try:
            await message.channel.send("内部エラーが発生しました。")
        except Exception:
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
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

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
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if not summary:
        await ctx.send("thread_brain 生成失敗")
        return

    save_thread_brain(ck, summary)
    await ctx.send("test OK: summary saved")


# ============================================================
# 10. Debug Context Injection
# ============================================================

from debug.debug_context import debug_context

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = openai_client

debug_context.load_mem = load_runtime_memory
debug_context.save_mem = save_runtime_memory
debug_context.append_mem = append_runtime_memory

debug_context.brain_gen = generate_thread_brain
debug_context.brain_load = load_thread_brain
debug_context.brain_save = save_thread_brain

debug_context.ovv_core = OVV_CORE
debug_context.ovv_external = OVV_EXTERNAL
debug_context.system_prompt = SYSTEM_PROMPT

print("[DEBUG] context injected OK")
print("[DEBUG] Boot complete. PG connected? =", bool(db_pg.PG_CONN))


# ============================================================
# Run Bot
# ============================================================

print("=== [RUN] Starting Discord bot ===")
bot.run(DISCORD_BOT_TOKEN)
