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
# 2. Notion CRUD (Notion_DB_Spec_v1 準拠)
# ============================================================

async def create_task(name: str, goal: str, discord_channel_id: int) -> Optional[str]:
    """
    Tasks.DB にタスクを作成 (Notion_DB_Spec_v1準拠)
    """
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": name}}]},
                "Goal": {"rich_text": [{"text": {"content": goal}}]} if goal else {"rich_text": []},
                "Status": {"select": {"name": "active"}},
                "ChannelId": {"rich_text": [{"text": {"content": str(discord_channel_id)}}]},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR create_task]", e)
        return None


async def start_session(task_id: str, name: str, discord_thread_id: int, started_at: datetime) -> Optional[str]:
    """
    Sessions.DB に active セッションを作成
    """
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": name}}]},
                "Task": {"relation": [{"id": task_id}]},
                "Status": {"select": {"name": "active"}},
                "ThreadId": {"number": int(discord_thread_id)},
                "StartTime": {"date": {"start": started_at.astimezone(timezone.utc).isoformat()}},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR start_session]", e)
        return None


async def end_session(session_id: str, ended_at: datetime, summary: str) -> bool:
    """
    セッション終了処理 (Sessions.DB → completed)
    """
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "Status": {"select": {"name": "completed"}},
                "EndTime": {"date": {"start": ended_at.astimezone(timezone.utc).isoformat()}},
                "Summary": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            },
        )
        return True
    except Exception as e:
        print("[ERROR end_session]", e)
        return False


async def append_logs(session_id: str, logs: List[Dict[str, str]]) -> bool:
    """
    Logs.DB にログをバッチ追加
    """
    try:
        for log in logs:
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "Session": {"relation": [{"id": session_id}]},
                    "AuthorName": {"rich_text": [{"text": {"content": log["author"]}}]},
                    "Content": {"rich_text": [{"text": {"content": log["content"][:2000]}}]},
                    "CreatedAt": {"date": {"start": log["created_at"]}},
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", e)
        return False

# ============================================================
# 3. Notion Query Utilities（完全保証版）
# ============================================================

def get_task_id_by_channel(discord_channel_id: int) -> Optional[str]:
    """
    Tasks.DB から ChannelId に紐づく task_id を完全保証で取得する。
    plain_text が null の Notion スマホ仕様にも確実対応。
    """
    try:
        target = str(discord_channel_id).strip()
        cursor = None

        while True:
            if cursor:
                resp = notion.databases.query(database_id=NOTION_TASKS_DB_ID, start_cursor=cursor)
            else:
                resp = notion.databases.query(database_id=NOTION_TASKS_DB_ID)

            results = resp.get("results", [])
            for page in results:
                props = page.get("properties", {})
                channel_prop = props.get("ChannelId", {})

                blocks = channel_prop.get("rich_text", [])

                merged = "".join(
                    (
                        b.get("plain_text") or
                        b.get("text", {}).get("content", "") or
                        ""
                    )
                    for b in blocks
                ).strip()

                if merged == target:
                    return page["id"]

            if resp.get("has_more"):
                cursor = resp.get("next_cursor")
            else:
                break

        return None

    except Exception as e:
        print("[ERROR get_task_id_by_channel]", e)
        return None


def get_active_session_id_by_thread(discord_thread_id: int) -> Optional[str]:
    """ active セッション取得 """
    try:
        resp = notion.databases.query(
            database_id=NOTION_SESSIONS_DB_ID,
            filter={
                "and": [
                    {"property": "ThreadId", "number": {"equals": int(discord_thread_id)}},
                    {"property": "Status", "select": {"equals": "active"}},
                ]
            },
            page_size=1,
        )
        rs = resp.get("results", [])
        return rs[0]["id"] if rs else None
    except Exception as e:
        print("[ERROR get_active_session_id_by_thread]", e)
        return None

# ============================================================
# 4. Runtime Memory
# ============================================================

OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 20

THREAD_TASK_CACHE: Dict[int, str] = {}
THREAD_SESSION_MAP: Dict[int, str] = {}
THREAD_LOG_BUFFER: Dict[int, List[Dict[str, str]]] = {}

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
SYSTEM_PROMPT = OVV_CORE + "\n\n" + OVV_EXTERNAL

# ============================================================
# 6. Ovv Call
# ============================================================

def extract_final(text: str) -> str:
    if "[FINAL]" in text:
        return text.split("[FINAL]", 1)[1].strip()
    return text

def call_ovv(context_key: int, user_msg: str) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": user_msg})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.2,
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

def get_context_key(msg: discord.Message) -> int:
    return msg.channel.id

def get_thread_and_channel(message: discord.Message):
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

    # ovv-チャンネルのみ
    if isinstance(message.channel, discord.Thread):
        if not message.channel.parent.name.lower().startswith("ovv-"):
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            return

    # コマンド時は Ovv を呼ばない
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Ovv メモリ
    context_key = get_context_key(message)
    push_ovv_memory(context_key, "user", message.content)

    # スレッド中ならログバッファへ
    thread_id, _ = get_thread_and_channel(message)
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

    # Ovv 呼び出し
    async with message.channel.typing():
        try:
            ans = call_ovv(context_key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

    sent = await message.channel.send(ans[:1900])

    # bot応答もログに積む
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

    # セッション中ならログへ
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
# 10. !Task
# ============================================================

@bot.command(name="Task")
async def task_info(ctx: commands.Context):

    channel = ctx.channel

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if not parent.name.lower().startswith("ovv-"):
            await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
            return
        channel_id = parent.id
    else:
        if not channel.name.lower().startswith("ovv-"):
            await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
            return
        channel_id = channel.id

    task_id = THREAD_TASK_CACHE.get(channel_id) or get_task_id_by_channel(channel_id)
    if not task_id:
        await ctx.send("このチャンネルに対応するタスクが Notion に存在しません。")
        return

    THREAD_TASK_CACHE[channel_id] = task_id

    try:
        page = notion.pages.retrieve(task_id)
        props = page["properties"]

        name = props["Name"]["title"][0]["plain_text"] if props["Name"]["title"] else "(名称未設定)"
        status = props["Status"]["select"]["name"] if props["Status"]["select"] else "(不明)"
        goal = "".join(rt.get("plain_text", "") for rt in props["Goal"]["rich_text"]) or "(未設定)"

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
# 11. !Task_s
# ============================================================

@bot.command(name="Task_s")
async def task_start(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_s はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id
    channel_id = parent.id

    existing_session = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if existing_session:
        await ctx.send("すでに active セッションがあります。（!Task_e で終了してください）")
        return

    task_id = THREAD_TASK_CACHE.get(channel_id) or get_task_id_by_channel(channel_id)
    if not task_id:
        await ctx.send("このチャンネルに対応するタスクが Notion に存在しません。")
        return

    THREAD_TASK_CACHE[channel_id] = task_id

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
# 12. !Task_e
# ============================================================

@bot.command(name="Task_e")
async def task_end(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_e はスレッド内でのみ使用できます。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* チャンネル内でのみ使用できます。")
        return

    thread_id = channel.id

    session_id = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if not session_id:
        await ctx.send("active なセッションが見つかりません。（!Task_s で開始してください）")
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
                    "content": "以下は Discord スレッド内の学習ログです。学習内容・ポイント・次にやるべきことを日本語で簡潔にまとめてください。",
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.2,
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
            "要約は Notion の Sessions.DB の Summary で確認してください。"
        )

# ============================================================
# 13. Run
# ============================================================

def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
