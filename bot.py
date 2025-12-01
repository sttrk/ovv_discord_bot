import os
import discord
import psycopg2
from psycopg2.extras import RealDictCursor
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
# Postgres (Phase 1: 接続 + init_db のみ)
# ============================================================

import psycopg2
from psycopg2.extras import RealDictCursor

POSTGRES_URL = os.getenv("POSTGRES_URL")

pg_conn = None

def pg_connect():
    global pg_conn
    try:
        pg_conn = psycopg2.connect(POSTGRES_URL, cursor_factory=RealDictCursor)
        print("[INFO] Postgres connected.")
    except Exception as e:
        print("[ERROR] Postgres connection failed:", e)
        pg_conn = None

def init_db():
    """
    Phase 1 では最低限のテーブルのみ作成する。
    runtime_memory は Phase 2 以降で使用。
    """
    if pg_conn is None:
        print("[WARN] init_db skipped (no Postgres connection).")
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
# 2. Notion CRUD  (Unified Logging Schema v2.1 準拠)
# ============================================================

async def create_task(
    name: str,
    goal: str,
    discord_thread_id: int,
    discord_channel_id: int,
) -> Optional[str]:
    """
    Tasks.DB にタスクを作成する。
    - name        : title
    - goal        : rich_text
    - status      : select ("active" 初期)
    - thread_id   : rich_text (Discord thread.id を文字列)
    - channel_id  : rich_text (Discord channel.id を文字列)
    - created_at  : date (UTC)
    - updated_at  : date (UTC)
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "goal": {
                    "rich_text": [{"text": {"content": goal}}]
                } if goal else {"rich_text": []},
                "status": {"select": {"name": "active"}},
                "thread_id": {
                    "rich_text": [{"text": {"content": str(discord_thread_id)}}]
                },
                "channel_id": {
                    "rich_text": [{"text": {"content": str(discord_channel_id)}}]
                },
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
    """
    Sessions.DB に active セッションを作成。
    - name       : title
    - task_id    : relation → Tasks.DB
    - status     : select ("active")
    - thread_id  : rich_text
    - start_time : date
    - created_at : date
    - updated_at : date
    """
    start_iso = started_at.astimezone(timezone.utc).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "name": {"title": [{"text": {"content": name}}]},
                "task_id": {"relation": [{"id": task_id}]},
                "status": {"select": {"name": "active"}},
                "thread_id": {
                    "rich_text": [{"text": {"content": str(discord_thread_id)}}]
                },
                "start_time": {"date": {"start": start_iso}},
                "created_at": {"date": {"start": now_iso}},
                "updated_at": {"date": {"start": now_iso}},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR start_session]", e)
        return None


async def end_session(
    session_id: str,
    ended_at: datetime,
    summary: str,
) -> bool:
    """
    セッション終了処理 (Sessions.DB → completed)。
    - status    : "completed"
    - end_time  : date
    - summary   : rich_text
    - updated_at: date
    """
    end_iso = ended_at.astimezone(timezone.utc).isoformat()
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"select": {"name": "completed"}},
                "end_time": {"date": {"start": end_iso}},
                "summary": {
                    "rich_text": [{"text": {"content": summary[:2000]}}]
                },
                "updated_at": {"date": {"start": end_iso}},
            },
        )
        return True
    except Exception as e:
        print("[ERROR end_session]", e)
        return False


async def append_logs(session_id: str, logs: List[Dict[str, str]]) -> bool:
    """
    Logs.DB にログをバッチ追加。
    - _ignore             : title（ダミー）
    - session_id          : relation → Sessions.DB
    - author              : rich_text
    - content             : rich_text
    - created_at          : date
    - discord_message_id  : rich_text
    """
    try:
        for log in logs:
            created_iso = log["created_at"]
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "_ignore": {
                        "title": [{"text": {"content": "log"}}]
                    },
                    "session_id": {"relation": [{"id": session_id}]},
                    "author": {
                        "rich_text": [{"text": {"content": log["author"]}}]
                    },
                    "content": {
                        "rich_text": [{"text": {"content": log["content"][:2000]}}]
                    },
                    "created_at": {"date": {"start": created_iso}},
                    "discord_message_id": {
                        "rich_text": [
                            {"text": {"content": log["discord_message_id"]}}
                        ]
                    },
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", e)
        return False

# ============================================================
# 3. Notion Query Utilities（完全保証版）
# ============================================================

def _merge_rich_text(prop: dict) -> str:
    """
    Notion rich_text から plain_text / text.content を吸い出してマージ。
    スマホ入力で text=null になるケースも吸収。
    """
    merged = ""
    blocks = prop.get("rich_text", [])

    for b in blocks:
        text_block = b.get("text") or {}
        candidate = (
            b.get("plain_text")
            or text_block.get("content")
            or ""
        )
        merged += candidate

    return merged.strip()


def get_task_id_by_thread(discord_thread_id: int) -> Optional[str]:
    """
    Tasks.DB から thread_id に紐づく task_id を取得（pull 完全保証版）。
    """
    try:
        target = str(discord_thread_id).strip()
        cursor = None

        while True:
            resp = (
                notion.databases.query(
                    database_id=NOTION_TASKS_DB_ID,
                    start_cursor=cursor,
                )
                if cursor
                else notion.databases.query(database_id=NOTION_TASKS_DB_ID)
            )

            for page in resp.get("results", []):
                props = page.get("properties", {})
                thread_prop = props.get("thread_id", {})
                merged = _merge_rich_text(thread_prop)
                if merged == target:
                    return page["id"]

            cursor = resp.get("next_cursor")
            if not resp.get("has_more"):
                break

        return None
    except Exception as e:
        print("[ERROR get_task_id_by_thread]", e)
        return None


def get_active_session_id_by_thread(discord_thread_id: int) -> Optional[str]:
    """
    Sessions.DB から thread_id に紐づく active な session_id を取得（pull 完全保証版）。
    """
    try:
        target = str(discord_thread_id).strip()
        cursor = None

        while True:
            resp = (
                notion.databases.query(
                    database_id=NOTION_SESSIONS_DB_ID,
                    start_cursor=cursor,
                )
                if cursor
                else notion.databases.query(database_id=NOTION_SESSIONS_DB_ID)
            )

            for page in resp.get("results", []):
                props = page.get("properties", {})

                status_name = (
                    props.get("status", {})
                    .get("select", {})
                    .get("name")
                )
                if status_name != "active":
                    continue

                merged = _merge_rich_text(props.get("thread_id", {}))
                if merged == target:
                    return page["id"]

            cursor = resp.get("next_cursor")
            if not resp.get("has_more"):
                break

        return None
    except Exception as e:
        print("[ERROR get_active_session_id_by_thread]", e)
        return None

# ============================================================
# 4. Runtime Memory（Ovv コンテキスト）
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 40

THREAD_TASK_CACHE: Dict[int, str] = {}                 # thread_id → task_id
THREAD_SESSION_MAP: Dict[int, str] = {}                # thread_id → session_id
THREAD_LOG_BUFFER: Dict[int, List[Dict[str, str]]] = {}  # thread_id → logs

# !tc で「goal 入力待ち」のスレッド
PENDING_TASK_GOAL: Dict[int, bool] = {}                # thread_id → bool


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
3. MUST avoid hallucination / unjustified assumptions / over-generalization.
4. MUST respect scope boundaries; MUST NOT add requirements user did not ask.
5. SHOULD decompose → reconstruct for stable answers.
6. MUST NOT mix reasoning and answer (phase-mixing).
7. MAY trigger CDC if needed, but MUST NOT overuse it.
""".strip()

SYSTEM_PROMPT_BASE = f"""
あなたは Discord 上で動作するアシスタントです。
ユーザー体験を最優先し、過剰な厳格化を避けてください。
次の Ovv Soft-Core を常に保持します。

{OVV_SOFT_CORE}
""".strip()

# ============================================================
# 6. Ovv Call
# ============================================================

def extract_final(text: str) -> str:
    return text.split("[FINAL]", 1)[1].strip() if "[FINAL]" in text else text


def call_ovv(context_key: int, user_msg: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {
            "role": "system",
            "content": "Ovv Soft-Core は絶対ルール。ただし過剰適用でユーザー体験を損なってはならない。"
        },
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
# 7. Discord Setup
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# スレッドと通常チャンネルのメモリ衝突を完全回避
def get_context_key(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id  # スレッドはそのまま
    else:
        # guild 単位でチャンネル ID をネームスペース化
        return (msg.guild.id << 32) | msg.channel.id

def get_thread_and_channel(message: discord.Message):
    """
    戻り値:
      thread_id: int | None
      channel_id: int
    """
    if isinstance(message.channel, discord.Thread):
        return message.channel.id, message.channel.parent.id
    else:
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

    # ovv-* 配下のみ対象
    if isinstance(message.channel, discord.Thread):
        if not message.channel.parent.name.lower().startswith("ovv-"):
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            return

    # コマンドはそのまま commands へ
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    thread_id, channel_id = get_thread_and_channel(message)

    # --------------------------------------------------------
    # 8-1. !tc 後の「goal 入力待ち」モード
    # --------------------------------------------------------
    if thread_id and PENDING_TASK_GOAL.get(thread_id):
        goal_text = message.content.strip()

        # セッション中なら、この発話もログとして保存
        if thread_id in THREAD_SESSION_MAP:
            THREAD_LOG_BUFFER.setdefault(thread_id, [])
            THREAD_LOG_BUFFER[thread_id].append(
                {
                    "discord_message_id": str(message.id),
                    "author": message.author.display_name,
                    "content": message.content,
                    "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
                }
            )

        if not goal_text:
            warn_text = "タスク目標が空です。1行で目標を入力してください。"
            sent = await message.channel.send(warn_text)
            if thread_id in THREAD_SESSION_MAP:
                THREAD_LOG_BUFFER[thread_id].append(
                    {
                        "discord_message_id": str(sent.id),
                        "author": "ovv-bot",
                        "content": warn_text,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            return

        task_name = message.channel.name or f"Task-{thread_id}"

        task_id = await create_task(
            name=task_name,
            goal=goal_text,
            discord_thread_id=thread_id,
            discord_channel_id=channel_id,
        )

        if not task_id:
            err_text = "Notion タスクの作成に失敗しました。"
            sent = await message.channel.send(err_text)
            if thread_id in THREAD_SESSION_MAP:
                THREAD_LOG_BUFFER[thread_id].append(
                    {
                        "discord_message_id": str(sent.id),
                        "author": "ovv-bot",
                        "content": err_text,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            # モードは解除しておく
            PENDING_TASK_GOAL.pop(thread_id, None)
            return

        THREAD_TASK_CACHE[thread_id] = task_id
        PENDING_TASK_GOAL.pop(thread_id, None)

        ok_text = (
            "タスクを作成しました。\n"
            f"タスク名: {task_name}\n"
            f"目標: {goal_text}"
        )
        sent = await message.channel.send(ok_text)

        if thread_id in THREAD_SESSION_MAP:
            THREAD_LOG_BUFFER[thread_id].append(
                {
                    "discord_message_id": str(sent.id),
                    "author": "ovv-bot",
                    "content": ok_text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        # このメッセージは「設定操作」とみなし、Ovv には渡さない
        return

    # --------------------------------------------------------
    # 8-2. 通常の Ovv 応答フロー
    # --------------------------------------------------------
    context_key = get_context_key(message)
    push_ovv_memory(context_key, "user", message.content)

    if thread_id and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER.setdefault(thread_id, [])
        THREAD_LOG_BUFFER[thread_id].append(
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
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

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
# 9. !o  — 明示 Ovv 呼び出し
# ============================================================

@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    msg = ctx.message
    context_key = get_context_key(msg)
    push_ovv_memory(context_key, "user", question)

    async with ctx.channel.typing():
        try:
            ans = call_ovv(context_key, question)
        except Exception as e:
            print("[ERROR !o call_ovv]", e)
            await ctx.send("Ovv との通信中にエラーが発生しました。")
            return

    sent = await ctx.send(ans[:1900])

    thread_id, _ = get_thread_and_channel(msg)
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
# 10. !Task / !t — タスク情報参照
# ============================================================

@bot.command(name="Task", aliases=["t"])
async def task_info(ctx: commands.Context):
    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task / !t はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id

    task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if not task_id:
        await ctx.send(
            "このスレッドに対応するタスクが Notion に存在しません。\n"
            "新規作成する場合は `!Task_c` または `!tc` を使用してください。"
        )
        return

    THREAD_TASK_CACHE[thread_id] = task_id

    try:
        page = notion.pages.retrieve(task_id)
        props = page["properties"]

        name = props["name"]["title"][0]["plain_text"] if props["name"]["title"] else "(名称未設定)"
        status = props["status"]["select"]["name"] if props["status"]["select"] else "(不明)"
        goal = "".join(
            rt.get("plain_text", "") for rt in props["goal"]["rich_text"]
        ) or "(未設定)"

        await ctx.send(
            f"【タスク情報】\n"
            f"タスク名: {name}\n"
            f"状態: {status}\n"
            f"目標: {goal}"
        )

    except Exception as e:
        print("[ERROR task_info]", e)
        await ctx.send("タスク情報の取得中にエラーが発生しました。")

# ============================================================
# 11. !Task_c / !tc — タスク作成
# ============================================================

@bot.command(name="Task_c", aliases=["tc"])
async def task_create_command(ctx: commands.Context, *, goal: str = None):
    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_c / !tc はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id
    channel_id = parent.id

    # すでにタスクが存在する場合は情報表示だけ
    existing_task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if existing_task_id:
        THREAD_TASK_CACHE[thread_id] = existing_task_id
        try:
            page = notion.pages.retrieve(existing_task_id)
            props = page["properties"]

            name = props["name"]["title"][0]["plain_text"] if props["name"]["title"] else "(名称未設定)"
            status = props["status"]["select"]["name"] if props["status"]["select"] else "(不明)"
            goal_text = "".join(
                rt.get("plain_text", "") for rt in props["goal"]["rich_text"]
            ) or "(未設定)"

            await ctx.send(
                "このスレッドには既にタスクが存在します。\n"
                f"【タスク情報】\n"
                f"タスク名: {name}\n"
                f"状態: {status}\n"
                f"目標: {goal_text}"
            )
        except Exception as e:
            print("[ERROR task_create_command: retrieve existing]", e)
            await ctx.send("既存タスク情報の取得中にエラーが発生しました。")
        return

    # ここから新規作成フロー
    task_name = channel.name or f"Task-{thread_id}"

    goal = (goal or "").strip()
    if goal:
        # 引数で goal が渡された場合は即作成
        task_id = await create_task(
            name=task_name,
            goal=goal,
            discord_thread_id=thread_id,
            discord_channel_id=channel_id,
        )
        if not task_id:
            await ctx.send("Notion タスクの作成に失敗しました。")
            return

        THREAD_TASK_CACHE[thread_id] = task_id
        await ctx.send(
            "タスクを作成しました。\n"
            f"タスク名: {task_name}\n"
            f"目標: {goal}"
        )
        return

    # goal 未指定の場合は「次の 1 メッセージを goal として扱う」モードへ
    PENDING_TASK_GOAL[thread_id] = True
    await ctx.send(
        "このスレッドのタスク目標（goal）を 1 行で入力してください。\n"
        "※次の通常メッセージを目標として Notion に保存します。"
    )

# ============================================================
# 12. !Task_s / !ts — セッション開始
# ============================================================

@bot.command(name="Task_s", aliases=["ts"])
async def task_start(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_s / !ts はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id

    existing_session = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if existing_session:
        await ctx.send("すでに active セッションがあります。（!Task_e / !te で終了してください）")
        return

    task_id = THREAD_TASK_CACHE.get(thread_id) or get_task_id_by_thread(thread_id)
    if not task_id:
        await ctx.send(
            "このスレッドに対応するタスクが Notion に存在しません。\n"
            "まず `!Task_c` または `!tc` でタスクを作成してください。"
        )
        return

    THREAD_TASK_CACHE[thread_id] = task_id

    started_at = datetime.now(timezone.utc)
    session_name = channel.name or f"Session-{thread_id}"

    session_id = await start_session(task_id, session_name, thread_id, started_at)
    if not session_id:
        await ctx.send("Notion セッションの作成に失敗しました。")
        return

    THREAD_SESSION_MAP[thread_id] = session_id
    THREAD_LOG_BUFFER[thread_id] = []

    await ctx.send("セッションを開始しました。（このスレッド内の会話をログ収集します）")

# ============================================================
# 13. !Task_e / !te — セッション終了
# ============================================================

@bot.command(name="Task_e", aliases=["te"])
async def task_end(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_e / !te はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id

    session_id = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if not session_id:
        await ctx.send("active なセッションが見つかりません。（!Task_s / !ts で開始してください）")
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
                {
                    "role": "system",
                    "content": (
                        "以下は Discord スレッド内のログです。"
                        "学習内容・ポイント・次にやるべきことを簡潔にまとめてください。"
                        "曖昧さがあっても必要以上に深掘りしないでください。"
                    ),
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.3,
        )
        summary = completion.choices[0].message.content.strip()

    except Exception as e:
        print("[ERROR summary_generation]", e)
        summary = "要約生成に失敗しましたが、ログは保存されました。"

    ok_logs = await append_logs(session_id, logs)
    ended_at = datetime.now(timezone.utc)
    ok_session = await end_session(session_id, ended_at, summary)

    THREAD_SESSION_MAP.pop(thread_id, None)
    THREAD_LOG_BUFFER.pop(thread_id, None)

    if not ok_logs or not ok_session:
        await ctx.send("セッション終了処理の一部でエラーが発生しました。")
    else:
        await ctx.send(
            "セッションを終了しました。\n"
            f"保存ログ件数: {len(logs)}\n"
            "要約は Notion の summary で確認できます。"
        )
# ============================================================
# Postgres Connect + init_db 起動時実行
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
