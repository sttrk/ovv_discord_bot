import os
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

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN が未設定です。")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が未設定です。")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_TASKS_DB_ID = os.getenv("NOTION_TASKS_DB_ID")
NOTION_SESSIONS_DB_ID = os.getenv("NOTION_SESSIONS_DB_ID")
NOTION_LOGS_DB_ID = os.getenv("NOTION_LOGS_DB_ID")

if not NOTION_API_KEY:
    raise RuntimeError("NOTION_API_KEY が未設定です。")
if not NOTION_TASKS_DB_ID:
    raise RuntimeError("NOTION_TASKS_DB_ID が未設定です。")
if not NOTION_SESSIONS_DB_ID:
    raise RuntimeError("NOTION_SESSIONS_DB_ID が未設定です。")
if not NOTION_LOGS_DB_ID:
    raise RuntimeError("NOTION_LOGS_DB_ID が未設定です。")

notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1.5 PostgreSQL（Phase 1: 接続＋初期テーブルのみ）
# ============================================================

import psycopg2
from psycopg2.extras import RealDictCursor

POSTGRES_URL = os.getenv("POSTGRES_URL")

pg_conn = None

def pg_connect():
    """
    POSTGRES_URL = "postgresql://user:pw@host:5432/dbname"
    """
    global pg_conn

    if not POSTGRES_URL:
        print("[WARN] POSTGRES_URL が未設定のため PostgreSQL 無効化")
        pg_conn = None
        return

    try:
        pg_conn = psycopg2.connect(
            POSTGRES_URL,
            cursor_factory=RealDictCursor,
            sslmode="require"
        )
        print("[INFO] PostgreSQL connected.")
    except Exception as e:
        print("[ERROR] PostgreSQL connection failed:", e)
        pg_conn = None


def init_db():
    """
    Phase 1：runtime_memory テーブルのみ作成。
    実際の SQL 永続化は Phase 2 で実施する。
    """
    if pg_conn is None:
        print("[WARN] init_db skipped (no PostgreSQL connection).")
        return

    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runtime_memory (
                    id SERIAL PRIMARY KEY,
                    thread_key BIGINT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            pg_conn.commit()
            print("[INFO] init_db: runtime_memory table ensured.")
    except Exception as e:
        print("[ERROR] init_db failed:", e)


# ============================================================
# 2. Notion CRUD（Unified Logging Schema v2.1 準拠）
# ============================================================

async def create_task(
    name: str,
    goal: str,
    discord_thread_id: int,
    discord_channel_id: int,
) -> Optional[str]:

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {"rich_text": [{"text": {"content": goal}}]} if goal else {"rich_text": []},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(discord_thread_id)}}]},
                "channel_id": {"rich_text": [{"text": {"content": str(discord_channel_id)}}]},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR create_task]", e)
        return None


async def start_session(
    task_id: str,
    name: str,
    discord_thread_id: int,
    started_at: datetime,
) -> Optional[str]:

    start_iso = started_at.astimezone(timezone.utc).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {"rich_text": [{"text": {"content": str(discord_thread_id)}}]},
                "start_time": {"date": {"start": start_iso}},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR start_session]", e)
        return None


async def end_session(session_id: str, ended_at: datetime, summary: str) -> bool:

    end_iso = ended_at.astimezone(timezone.utc).isoformat()

    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": end_iso}},
                "summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
                "updated_at": {"date": {"start": end_iso}},
            },
        )
        return True
    except Exception as e:
        print("[ERROR end_session]", e)
        return False


async def append_logs(session_id: str, logs: List[Dict[str, str]]) -> bool:

    try:
        for log in logs:
            created_iso = log["created_at"]

            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {"title": [{"text": {"content": "log"}}]},
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "created_at": {"date": {"start": created_iso}},
                    "discord_message_id": {
                        "rich_text": [{"text": {"content": log["discord_message_id"]}}]
                    },
                },
            )
        return True

    except Exception as e:
        print("[ERROR append_logs]", e)
        return False


# ============================================================
# 3. Notion Query
# ============================================================

def _merge_rich_text(prop: dict) -> str:
    merged = ""
    for b in prop.get("rich_text", []):
        text_block = b.get("text") or {}
        merged += b.get("plain_text") or text_block.get("content") or ""
    return merged.strip()


def get_task_id_by_thread(discord_thread_id: int) -> Optional[str]:
    try:
        target = str(discord_thread_id)
        cursor = None

        while True:
            resp = (
                notion.databases.query(
                    database_id=NOTION_TASKS_DB_ID,
                    start_cursor=cursor
                )
                if cursor else notion.databases.query(database_id=NOTION_TASKS_DB_ID)
            )

            for page in resp.get("results", []):
                merged = _merge_rich_text(page["properties"]["thread_id"])
                if merged == target:
                    return page["id"]

            cursor = resp.get("next_cursor")
            if not resp.get("has_more"):
                break

    except Exception as e:
        print("[ERROR get_task_id_by_thread]", e)

    return None


def get_active_session_id_by_thread(discord_thread_id: int) -> Optional[str]:
    try:
        target = str(discord_thread_id)
        cursor = None

        while True:
            resp = (
                notion.databases.query(
                    database_id=NOTION_SESSIONS_DB_ID,
                    start_cursor=cursor
                )
                if cursor else notion.databases.query(database_id=NOTION_SESSIONS_DB_ID)
            )

            for page in resp.get("results", []):
                props = page["properties"]
                if props["status"]["select"] and props["status"]["select"]["name"] != "active":
                    continue

                merged = _merge_rich_text(props["thread_id"])
                if merged == target:
                    return page["id"]

            cursor = resp.get("next_cursor")
            if not resp.get("has_more"):
                break

    except Exception as e:
        print("[ERROR get_active_session_id_by_thread]", e)

    return None


# ============================================================
# 4. Runtime Memory
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

THREAD_TASK_CACHE: Dict[int, str] = {}
THREAD_SESSION_MAP: Dict[int, str] = {}
THREAD_LOG_BUFFER: Dict[int, List[Dict[str, str]]] = {}
PENDING_TASK_GOAL: Dict[int, bool] = {}


def push_ovv_memory(key: int, role: str, content: str):
    OVV_MEMORY.setdefault(key, [])
    OVV_MEMORY[key].append({"role": role, "content": content})
    if len(OVV_MEMORY[key]) > OVV_MEMORY_LIMIT:
        OVV_MEMORY[key] = OVV_MEMORY[key][-OVV_MEMORY_LIMIT:]


# ============================================================
# 5. Load Core + External
# ============================================================

def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"{path} が存在しません。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

OVV_SOFT_CORE = """
[Ovv Soft-Core v1.1]

1. MUST keep user experience primary; MUST NOT become over-strict.
2. MUST use Clarify only when ambiguity materially affects answer quality.
3. MUST avoid hallucination.
4. MUST respect scope boundaries.
5. SHOULD decompose → reconstruct.
6. MUST NOT phase-mix.
7. MAY use CDC, but avoid overuse.
"""

SYSTEM_PROMPT_BASE = f"""
あなたは Discord 上で動作するアシスタント。
Ovv Soft-Core を最優先し、過剰な厳格化を避ける。
{OVV_SOFT_CORE}
"""

# ============================================================
# 6. Ovv Call
# ============================================================

def extract_final(text: str) -> str:
    return text.split("[FINAL]", 1)[1].strip() if "[FINAL]" in text else text


def call_ovv(context_key: int, user_msg: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": user_msg})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.7,
    )

    full = res.choices[0].message.content.strip()
    push_ovv_memory(context_key, "assistant", full)
    return extract_final(full)


# ============================================================
# 7. Discord
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    else:
        return (msg.guild.id << 32) | msg.channel.id


def get_thread_and_channel(message: discord.Message):
    if isinstance(message.channel, discord.Thread):
        return message.channel.id, message.channel.parent.id
    return None, message.channel.id


# ============================================================
# 8. on_message
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    if message.type == MessageType.thread_created:
        return

    # ovv-* 限定
    if isinstance(message.channel, discord.Thread):
        if not message.channel.parent.name.lower().startswith("ovv-"):
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            return

    # command
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    thread_id, channel_id = get_thread_and_channel(message)

    # goal 待ち
    if thread_id and PENDING_TASK_GOAL.get(thread_id):

        goal_text = message.content.strip()

        if thread_id in THREAD_SESSION_MAP:
            THREAD_LOG_BUFFER.setdefault(thread_id, []).append(
                {
                    "discord_message_id": str(message.id),
                    "author": message.author.display_name,
                    "content": message.content,
                    "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
                }
            )

        if not goal_text:
            warn = "タスク目標が空です。1行で入力してください。"
            sent = await message.channel.send(warn)
            return

        task_name = message.channel.name or f"Task-{thread_id}"

        task_id = await create_task(
            name=task_name,
            goal=goal_text,
            discord_thread_id=thread_id,
            discord_channel_id=channel_id,
        )

        if not task_id:
            await message.channel.send("Notion タスク作成に失敗しました。")
            PENDING_TASK_GOAL.pop(thread_id, None)
            return

        THREAD_TASK_CACHE[thread_id] = task_id
        PENDING_TASK_GOAL.pop(thread_id, None)

        await message.channel.send(
            f"タスクを作成しました。\nタスク名: {task_name}\n目標: {goal_text}"
        )
        return

    # 通常の Ovv 応答
    context_key = get_context_key(message)
    push_ovv_memory(context_key, "user", message.content)

    if thread_id and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER.setdefault(thread_id, []).append(
            {
                "discord_message_id": str(message.id),
                "author": message.author.display_name,
                "content": message.content,
                "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
            }
        )

    async with message.channel.typing():
        try:
            ans = call_ovv(context_key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            ans = "Ovv 通信エラーが発生しました。"

    sent = await message.channel.send(ans[:1900])

    if thread_id and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER[thread_id].append(
            {
                "discord_message_id": str(sent.id),
                "author": "ovv-bot",
                "content": ans,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )


# ============================================================
# 9. !o
# ============================================================

@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):

    context_key = get_context_key(ctx.message)
    push_ovv_memory(context_key, "user", question)

    async with ctx.channel.typing():
        try:
            ans = call_ovv(context_key, question)
        except Exception:
            await ctx.send("Ovv 通信エラーが発生しました。")
            return

    await ctx.send(ans[:1900])


# ============================================================
# 10. !Task / !t
# ============================================================

@bot.command(name="Task", aliases=["t"])
async def task_info(ctx: commands.Context):

    channel = ctx.channel
    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task / !t はスレッド専用です。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("ovv-* チャンネル専用です。")
        return

    thread_id = channel.id

    task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if not task_id:
        await ctx.send("タスクが存在しません。`!tc` で作成できます。")
        return

    THREAD_TASK_CACHE[thread_id] = task_id

    try:
        page = notion.pages.retrieve(task_id)
        props = page["properties"]

        name = props["name"]["title"][0]["plain_text"] if props["name"]["title"] else "(名称未設定)"
        status = props["status"]["select"]["name"] if props["status"]["select"] else "(不明)"
        goal = "".join(rt.get("plain_text", "") for rt in props["goal"]["rich_text"]) or "(未設定)"

        await ctx.send(f"【タスク情報】\nタスク名: {name}\n状態: {status}\n目標: {goal}")

    except Exception as e:
        print("[ERROR task_info]", e)
        await ctx.send("タスク情報の取得中にエラーが発生しました。")


# ============================================================
# 11. !tc
# ============================================================

@bot.command(name="Task_c", aliases=["tc"])
async def task_create(ctx: commands.Context, *, goal: str = None):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!tc はスレッド専用です。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("ovv-* 専用です。")
        return

    thread_id = channel.id
    channel_id = parent.id

    existing_task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if existing_task_id:
        THREAD_TASK_CACHE[thread_id] = existing_task_id
        await ctx.send("すでにタスクが存在します。!t で確認できます。")
        return

    task_name = channel.name or f"Task-{thread_id}"

    goal = (goal or "").strip()

    if goal:
        task_id = await create_task(
            name=task_name, goal=goal,
            discord_thread_id=thread_id,
            discord_channel_id=channel_id
        )

        if not task_id:
            await ctx.send("タスク作成に失敗しました。")
            return

        THREAD_TASK_CACHE[thread_id] = task_id
        await ctx.send(f"タスクを作成しました。\nタスク名: {task_name}\n目標: {goal}")
        return

    PENDING_TASK_GOAL[thread_id] = True
    await ctx.send("このスレッドのタスク目標を1行で入力してください。")


# ============================================================
# 12. !ts
# ============================================================

@bot.command(name="Task_s", aliases=["ts"])
async def task_start(ctx: commands.Context):

    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("!ts はスレッド専用です。")
        return

    parent = ctx.channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("ovv-* 専用です。")
        return

    thread_id = ctx.channel.id

    existing = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if existing:
        await ctx.send("すでに active セッションがあります。!te で終了してください。")
        return

    task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if not task_id:
        await ctx.send("タスクが存在しません。まず !tc で作成してください。")
        return

    THREAD_TASK_CACHE[thread_id] = task_id

    started_at = datetime.now(timezone.utc)
    session_name = ctx.channel.name or f"Session-{thread_id}"

    session_id = await start_session(task_id, session_name, thread_id, started_at)
    if not session_id:
        await ctx.send("Notion セッション作成に失敗しました。")
        return

    THREAD_SESSION_MAP[thread_id] = session_id
    THREAD_LOG_BUFFER[thread_id] = []

    await ctx.send("セッションを開始しました。（ログ収集開始）")


# ============================================================
# 13. !te
# ============================================================

@bot.command(name="Task_e", aliases=["te"])
async def task_end(ctx: commands.Context):

    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send("!te はスレッド専用です。")
        return

    parent = ctx.channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("ovv-* 専用です。")
        return

    thread_id = ctx.channel.id

    session_id = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if not session_id:
        await ctx.send("active セッションがありません。!ts で開始してください。")
        return

    logs = THREAD_LOG_BUFFER.get(thread_id, [])

    if logs:
        joined = "\n".join(f"{l['author']}: {l['content']}" for l in logs)
    else:
        joined = "このセッションではログが記録されていません。"

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system",
                 "content": "以下ログを要約してください。曖昧さを過剰に深掘りしないこと。"},
                {"role": "user", "content": joined},
            ],
            temperature=0.2,
        )
        summary = completion.choices[0].message.content.strip()
    except Exception as e:
        print("[ERROR summary]", e)
        summary = "要約生成に失敗しました。ログは保存済みです。"

    ok_logs = await append_logs(session_id, logs)
    ended_at = datetime.now(timezone.utc)
    ok_session = await end_session(session_id, ended_at, summary)

    THREAD_SESSION_MAP.pop(thread_id, None)
    THREAD_LOG_BUFFER.pop(thread_id, None)

    if not ok_logs or not ok_session:
        await ctx.send("セッション終了処理の一部でエラーが発生しました。")
    else:
        await ctx.send(f"セッションを終了しました。\n保存ログ件数: {len(logs)}")


# ============================================================
# PostgreSQL Connect + init_db
# ============================================================

pg_connect()
init_db()

# ============================================================
# 14. Run
# ============================================================

def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
