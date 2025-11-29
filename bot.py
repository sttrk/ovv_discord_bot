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
    Tasks.DB にタスクを作成する。
    Notion_DB_Spec_v1.1 §1 準拠:

      - Name      : title
      - Goal      : rich_text
      - Status    : select (active / paused / completed)
      - ChannelId : rich_text（Discord チャンネル ID 文字列）
    """
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": name}}]},
                "Goal": {"rich_text": [{"text": {"content": goal}}]} if goal else {"rich_text": []},
                "Status": {"select": {"name": "active"}},
                "ChannelId": {
                    "rich_text": [
                        {"text": {"content": str(discord_channel_id)}}
                    ]
                },
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
    Sessions.DB に active セッションを作成する。
    Notion_DB_Spec_v1 §2 準拠:

      - Name      : title
      - Task      : relation → Tasks.DB
      - ThreadId  : number
      - Status    : select
      - StartTime : date
    """
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "Name": {"title": [{"text": {"content": name}}]},
                "Task": {"relation": [{"id": task_id}]},
                "Status": {"select": {"name": "active"}},
                "ThreadId": {"number": int(discord_thread_id)},
                "StartTime": {
                    "date": {"start": started_at.astimezone(timezone.utc).isoformat()}
                },
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
    Sessions.DB のセッションを終了扱いにする。
    Notion_DB_Spec_v1 §2 準拠:

      - Status   : completed
      - EndTime  : date
      - Summary  : rich_text（ここでは 'Summary' 固定）
    """
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "Status": {"select": {"name": "completed"}},
                "EndTime": {
                    "date": {"start": ended_at.astimezone(timezone.utc).isoformat()}
                },
                "Summary": {
                    "rich_text": [{"text": {"content": summary[:2000]}}]
                },
            },
        )
        return True
    except Exception as e:
        print("[ERROR end_session]", e)
        return False


async def append_logs(session_id: str, logs: List[Dict[str, str]]) -> bool:
    """
    Logs.DB にログをバッチ追加する。
    Notion_DB_Spec_v1 §3 準拠:

      - Session   : relation → Sessions.DB
      - AuthorName: rich_text
      - Content   : rich_text
      - CreatedAt : date
    """
    try:
        for log in logs:
            created_iso = log["created_at"]
            notion.pages.create(
                parent={"database_id": NOTION_LOGS_DB_ID},
                properties={
                    "Session": {"relation": [{"id": session_id}]},
                    "AuthorName": {
                        "rich_text": [{"text": {"content": log["author"]}}]
                    },
                    "Content": {
                        "rich_text": [{"text": {"content": log["content"][:2000]}}]
                    },
                    "CreatedAt": {"date": {"start": created_iso}},
                },
            )
        return True
    except Exception as e:
        print("[ERROR append_logs]", e)
        return False

# ============================================================
# 3. Notion Query Utilities
# ============================================================

def get_task_id_by_channel(discord_channel_id: int) -> Optional[str]:
    """
    Tasks.DB から ChannelId に紐づく task_id を 1 件取得。
    Notion_DB_Spec_v1.1 §1 準拠:

      - ChannelId : rich_text（Discord チャンネル ID 文字列）
    """
    try:
        resp = notion.databases.query(
            database_id=NOTION_TASKS_DB_ID,
            filter={
                "property": "ChannelId",
                "rich_text": {"equals": str(discord_channel_id)},
            },
            page_size=1,
        )
        rs = resp.get("results", [])
        if not rs:
            return None
        return rs[0]["id"]
    except Exception as e:
        print("[ERROR get_task_id_by_channel]", e)
        return None

def get_active_session_id_by_thread(discord_thread_id: int) -> Optional[str]:
    """
    Sessions.DB から ThreadId に紐づく active な session_id を 1 件取得。
    Notion_DB_Spec_v1 §2 準拠:

      - ThreadId : number
      - Status   : select = active
    """
    try:
        resp = notion.databases.query(
            database_id=NOTION_SESSIONS_DB_ID,
            filter={
                "and": [
                    {
                        "property": "ThreadId",
                        "number": {"equals": int(discord_thread_id)},
                    },
                    {
                        "property": "Status",
                        "select": {"equals": "active"},
                    },
                ]
            },
            page_size=1,
        )
        rs = resp.get("results", [])
        if not rs:
            return None
        return rs[0]["id"]
    except Exception as e:
        print("[ERROR get_active_session_id_by_thread]", e)
        return None

# ============================================================
# 4. Runtime Memory（内部ステート）
# ============================================================

# Ovv 用コンテキストメモリ（スレッド/チャンネル単位）
OVV_MEMORY: Dict[int, List[Dict[str, str]]] = {}
OVV_MEMORY_LIMIT = 20

# タスク・セッション・ログ用ランタイムメモリ
THREAD_TASK_CACHE: Dict[int, str] = {}          # channel_id → task_id
THREAD_SESSION_MAP: Dict[int, str] = {}         # thread_id → session_id
THREAD_LOG_BUFFER: Dict[int, List[Dict[str, str]]] = {}  # thread_id → logs


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
# 6. Ovv Call（[FINAL] 抽出）
# ============================================================

def extract_final(text: str) -> str:
    marker = "[FINAL]"
    if marker in text:
        return text.split(marker, 1)[1].strip()
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
    # スレッドなら thread_id、そうでなければ channel_id
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return msg.channel.id

def get_thread_and_channel(message: discord.Message):
    """
    戻り値:
      thread_id: int または None
      channel_id: int
    """
    if isinstance(message.channel, discord.Thread):
        thread_id = message.channel.id
        channel_id = message.channel.parent.id
    else:
        thread_id = None
        channel_id = message.channel.id
    return thread_id, channel_id

# ============================================================
# 8. on_message（自然言語応答 + ログ収集）
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    # 1. Bot の発言は無視
    if message.author.bot:
        return

    # 2. Discord の thread_created 等（システムメッセージ）は無視
    if message.type == MessageType.thread_created:
        return

    # 3. チャンネル名チェック（ovv-◯◯ のみ対象）
    if isinstance(message.channel, discord.Thread):
        parent = message.channel.parent
        if not parent.name.lower().startswith("ovv-"):
            # ovv-以外のスレッドは完全無視
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            # ovv-以外の通常チャンネルは完全無視
            return

    # 4. ***** コマンドなら Ovv を呼ばず、コマンド処理だけする *****
    # これが今回の最大の修正点
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # 5. Ovv 用メモリ登録
    context_key = get_context_key(message)
    push_ovv_memory(context_key, "user", message.content)

    # 6. セッション中であればログバッファに追加（ユーザ発話）
    thread_id, _ = get_thread_and_channel(message)
    if thread_id is not None and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER.setdefault(thread_id, [])
        THREAD_LOG_BUFFER[thread_id].append(
            {
                "discord_message_id": str(message.id),
                "author": message.author.display_name,
                "content": message.content,
                "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
            }
        )

    # 7. Ovv 呼び出し（通常メッセージのみ）
    async with message.channel.typing():
        try:
            ans = call_ovv(context_key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

    # 8. Ovv の返答を送信（2000字対策）
    if len(ans) <= 1900:
        sent = await message.channel.send(ans)
    else:
        sent = await message.channel.send(ans[:1900])

    # 9. セッション中なら bot 応答もログバッファへ追加
    if thread_id is not None and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER.setdefault(thread_id, [])
        THREAD_LOG_BUFFER[thread_id].append(
            {
                "discord_message_id": str(sent.id),
                "author": "ovv-bot",
                "content": ans,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

# ============================================================
# 9. !o — 明示コマンド（任意）
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

    # セッション中なら応答もログに積む
    thread_id, _ = get_thread_and_channel(msg)
    if thread_id is not None and thread_id in THREAD_SESSION_MAP:
        THREAD_LOG_BUFFER.setdefault(thread_id, [])
        THREAD_LOG_BUFFER[thread_id].append(
            {
                "discord_message_id": str(sent.id),
                "author": "ovv-bot",
                "content": ans,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

# ============================================================
# 10. !Task — タスク情報の参照
# ============================================================

@bot.command(name="Task")
async def task_info(ctx: commands.Context):
    """
    現在のチャンネル（ovv-◯◯）に紐づく Tasks.DB の情報を表示。
    （タスク = チャンネル単位）
    """
    channel = ctx.channel

    # ovv-◯◯ のみ許可
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

    task_id = THREAD_TASK_CACHE.get(channel_id)
    if not task_id:
        task_id = get_task_id_by_channel(channel_id)
        if task_id:
            THREAD_TASK_CACHE[channel_id] = task_id

    if not task_id:
        await ctx.send("このチャンネルに対応するタスクが Notion に存在しません。")
        return

    try:
        page = notion.pages.retrieve(task_id)
        props = page["properties"]

        try:
            name = props["Name"]["title"][0]["plain_text"]
        except Exception:
            name = "(名称未設定)"

        try:
            status = props["Status"]["select"]["name"]
        except Exception:
            status = "(不明)"

        try:
            goal = "".join(rt["plain_text"] for rt in props["Goal"]["rich_text"])
        except Exception:
            goal = "(未設定)"

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
# 11. !Task_s — セッション開始
# ============================================================

@bot.command(name="Task_s")
async def task_start(ctx: commands.Context):
    """
    現在のスレッドを 1 セッションとして開始する。
    - 1スレッド = 1セッション前提
    """
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

    # 既に active セッションがないか
    existing_session_id = THREAD_SESSION_MAP.get(thread_id) or get_active_session_id_by_thread(thread_id)
    if existing_session_id:
        await ctx.send("すでに active セッションがあります。（!Task_e で終了してください）")
        return

    # タスク取得（チャンネル単位）
    task_id = THREAD_TASK_CACHE.get(channel_id) or get_task_id_by_channel(channel_id)
    if not task_id:
        await ctx.send("このチャンネルに対応するタスクが Notion に存在しません。Tasks.DB を確認してください。")
        return
    THREAD_TASK_CACHE[channel_id] = task_id

    started_at = datetime.now(timezone.utc)
    session_name = channel.name or f"Session-{thread_id}"

    session_id = await start_session(
        task_id=task_id,
        name=session_name,
        discord_thread_id=thread_id,
        started_at=started_at,
    )
    if not session_id:
        await ctx.send("Notion セッションの作成に失敗しました。")
        return

    THREAD_SESSION_MAP[thread_id] = session_id
    THREAD_LOG_BUFFER[thread_id] = []

    await ctx.send("セッションを開始しました。（このスレッド内の会話をログ収集します）")

# ============================================================
# 12. !Task_e — セッション終了
# ============================================================

@bot.command(name="Task_e")
async def task_end(ctx: commands.Context):
    """
    現在のスレッドに紐づく active セッションを終了し、
    - Logs.DB にバッチ書き込み
    - 要約生成
    - Sessions.DB を completed へ
    まで実施する。
    """
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

    # 要約用テキストを生成
    if logs:
        joined = "\n".join(f"{l['author']}: {l['content']}" for l in logs)
    else:
        joined = "このセッションではログが記録されていません。"

    # 要約生成（Ovv コアとは別枠の簡易サマライザー）
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "以下は Discord スレッド内の学習ログです。学習内容・ポイント・次にやるべきことを日本語で簡潔に要約してください。",
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.2,
        )
        summary = completion.choices[0].message.content.strip()
    except Exception as e:
        print("[ERROR summary_generation]", e)
        summary = "要約生成に失敗しましたが、ログは保存されました。"

    # Logs.DB へ書き込み
    try:
        ok_logs = await append_logs(session_id, logs)
    except Exception as e:
        print("[ERROR append_logs]", e)
        await ctx.send("ログ保存中にエラーが発生しました。")
        return

    # Sessions.DB を completed へ
    ended_at = datetime.now(timezone.utc)
    ok_session = await end_session(session_id, ended_at, summary)

    # ランタイムメモリをクリア
    THREAD_SESSION_MAP.pop(thread_id, None)
    THREAD_LOG_BUFFER.pop(thread_id, None)

    if not ok_logs or not ok_session:
        await ctx.send("セッション終了処理の一部でエラーが発生しました。（Notion を確認してください）")
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
