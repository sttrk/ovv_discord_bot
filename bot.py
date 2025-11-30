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
# 2. Load Core / External
# ============================================================

def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"{path} が存在しません。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_text("ovv_core.txt")
OVV_EXTERNAL = load_text("ovv_external_contract.txt")

# ============================================================
# 3. Ovv Soft-Core v0.2（統合済 System Prompt）
# ============================================================

SYSTEM_PROMPT_BASE = """
あなたは Discord 上で動作するアシスタントであり、ユーザー体験を最優先する。
通常は柔らかく自然に応答し、不要な厳格化でユーザーを阻害してはならない。

以下の Ovv Soft-Core（Regulation Layer / Operational Layer）は絶対遵守とする。

========================================
[Regulation Layer: MUST / MUST NOT]

1. 曖昧を放置して進めてはならない（MUST NOT）
   Clarify は必要条件を満たす場合のみ実施する（MUST）

2. 誤補完・思い込み・一般論押しつけは禁止（MUST NOT）

3. スコープ越境禁止（MUST NOT）
   ユーザー要求外のルール追加は禁止

4. Phase Mixing 禁止（MUST NOT）
   Clarify・推論・回答を混在させない

5. 構造破綻を検知した場合は即停止し指摘する（MUST）

6. CDC は必要条件を満たす場合のみ発火（MUST）
   Clarify: 不確実性が回答品質を損なう場合
   Diverge: 選択肢が複数成立する場合
   Converge: 最適解が一つに絞れるまで収束

========================================
[Operational Layer: SHOULD / SHOULD NOT]

1. 軽いタスクは軽く処理する（SHOULD）
2. 通常回答では Ovv-Core を露骨に前面へ出さない（SHOULD NOT）
3. 不必要な Clarify を行わない（SHOULD NOT）
4. ユーザー意図を最優先し自然で扱いやすい回答にする（SHOULD）
5. CDC の内部推論過程を回答に混ぜない（MUST）
========================================
""".strip()

# ============================================================
# 4. Notion CRUD
# ============================================================

async def create_task(name: str, goal: str, discord_channel_id: int) -> Optional[str]:
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
# 5. Notion Query Utilities（完全保証）
# ============================================================

def get_task_id_by_channel(discord_channel_id: int) -> Optional[str]:
    try:
        target = str(discord_channel_id).strip()
        cursor = None

        while True:
            resp = notion.databases.query(
                database_id=NOTION_TASKS_DB_ID,
                start_cursor=cursor
            ) if cursor else notion.databases.query(
                database_id=NOTION_TASKS_DB_ID
            )

            for page in resp.get("results", []):
                blocks = page["properties"]["ChannelId"].get("rich_text", [])
                merged = "".join(
                    (b.get("plain_text") or b.get("text", {}).get("content", "") or "")
                    for b in blocks
                ).strip()
                if merged == target:
                    return page["id"]

            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        return None

    except Exception as e:
        print("[ERROR get_task_id_by_channel]", e)
        return None


def get_active_session_id_by_thread(discord_thread_id: int) -> Optional[str]:
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
# 6. Runtime Memory
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
# 7. Ovv Call
# ============================================================

def extract_final(text: str) -> str:
    if "[FINAL]" in text:
        return text.split("[FINAL]", 1)[1].strip()
    return text


def call_ovv(context_key: int, user_msg: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT_BASE},
        {"role": "system", "content": "上記 Soft-Core は Ovv の絶対規範であり、過剰適用によりユーザー体験を損なうことも禁止する。"},
        {"role": "assistant", "content": OVV_CORE},
        {"role": "assistant", "content": OVV_EXTERNAL},
    ]

    msgs.extend(OVV_MEMORY.get(context_key, []))
    msgs.append({"role": "user", "content": user_msg})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.4,
    )

    full = res.choices[0].message.content.strip()
    push_ovv_memory(context_key, "assistant", full)
    return extract_final(full)

# ============================================================
# 8. Discord Bot Setup
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
# 9. on_message
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

    # コマンドは先に処理
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Memory積む
    context_key = get_context_key(message)
    push_ovv_memory(context_key, "user", message.content)

    # スレッド中ならログ蓄積
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
            await message.channel.send("OVV との通信中にエラーが発生しました。")
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
# 10. !o
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
            print("[ERROR !o]", e)
            await ctx.send("OVV との通信中にエラーが発生しました。")
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
# 11. !Task
# ============================================================

@bot.command(name="Task")
async def task_info(ctx: commands.Context):

    channel = ctx.channel

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if not parent.name.lower().startswith("ovv-"):
            await ctx.send("このコマンドは ovv-* チャンネル専用です。")
            return
        channel_id = parent.id
    else:
        if not channel.name.lower().startswith("ovv-"):
            await ctx.send("このコマンドは ovv-* チャンネル専用です。")
            return
        channel_id = channel.id

    task_id = THREAD_TASK_CACHE.get(channel_id) or get_task_id_by_channel(channel_id)
    if not task_id:
        await ctx.send("このチャンネルに紐づくタスクが Notion に存在しません。")
        return

    THREAD_TASK_CACHE[channel_id] = task_id

    try:
        page = notion.pages.retrieve(task_id)
        props = page["properties"]

        name = props["Name"]["title"][0]["plain_text"] if props["Name"]["title"] else "(名称未設定)"
        status = props["Status"]["select"]["name"] if props["Status"]["select"] else "(不明)"
        goal = "".join(rt.get("plain_text", "") for rt in props["Goal"]["rich_text"]) or "(未設定)"

        await ctx.send(f"【タスク情報】\nタスク名: {name}\n状態: {status}\n目標: {goal}")

    except Exception as e:
        print("[ERROR task_info]", e)
        await ctx.send("タスク情報の取得に失敗しました。")

# ============================================================
# 12. !Task_s
# ============================================================

@bot.command(name="Task_s")
async def task_start(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_s はスレッド内専用です。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* スレッド専用です。")
        return

    thread_id = channel.id
    channel_id = parent.id

    existing = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if existing:
        await ctx.send("すでに active セッションがあります。（!Task_e で終了）")
        return

    task_id = THREAD_TASK_CACHE.get(channel_id) or get_task_id_by_channel(channel_id)
    if not task_id:
        await ctx.send("このチャンネルのタスクが Notion に存在しません。")
        return

    THREAD_TASK_CACHE[channel_id] = task_id

    started_at = datetime.now(timezone.utc)
    session_name = channel.name

    session_id = await start_session(task_id, session_name, thread_id, started_at)
    if not session_id:
        await ctx.send("セッション作成に失敗しました。")
        return

    THREAD_SESSION_MAP[thread_id] = session_id
    THREAD_LOG_BUFFER[thread_id] = []

    await ctx.send("セッションを開始しました。（ログ収集を開始）")

# ============================================================
# 13. !Task_e
# ============================================================

@bot.command(name="Task_e")
async def task_end(ctx: commands.Context):

    channel = ctx.channel

    if not isinstance(channel, discord.Thread):
        await ctx.send("!Task_e はスレッド内専用です。")
        return

    parent = channel.parent
    if not parent.name.lower().startswith("ovv-"):
        await ctx.send("このコマンドは ovv-* スレッド専用です。")
        return

    thread_id = channel.id
    session_id = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)

    if not session_id:
        await ctx.send("active セッションがありません。（!Task_s で開始）")
        return

    logs = THREAD_LOG_BUFFER.get(thread_id, [])
    joined = "\n".join(f"{l['author']}: {l['content']}" for l in logs) if logs else "ログなし"

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "以下は Discord スレッド内のログです。重要点、学習内容、次 steps を簡潔にまとめてください。",
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.3,
        )
        summary = completion.choices[0].message.content.strip()
    except Exception as e:
        print("[ERROR summary]", e)
        summary = "要約生成に失敗しましたが、ログは保存されました。"

    ok_logs = await append_logs(session_id, logs)
    ok_session = await end_session(session_id, datetime.now(timezone.utc), summary)

    THREAD_SESSION_MAP.pop(thread_id, None)
    THREAD_LOG_BUFFER.pop(thread_id, None)

    if not ok_logs or not ok_session:
        await ctx.send("セッション終了処理の一部でエラーが発生しました。")
    else:
        await ctx.send(f"セッションを終了しました。\nログ件数: {len(logs)}")

# ============================================================
# 14. Run
# ============================================================

def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
