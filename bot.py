import os
import discord
from discord import MessageType
from discord.ext import commands
from openai import OpenAI
from typing import Dict, List

# ============================================================
# Environment
# ============================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN が未設定です。")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が未設定です。")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
# ============================================================
# Notion Client Setup（追記）
# ============================================================
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

from notion_client import Client
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# Notion CRUD Functions（追記）
# ============================================================

# タスク作成
async def notion_create_task(title: str, thread_id: int):
    """タスク(Tasks.DB)を作成し、task_id を返す。"""
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB_ID},
            properties={
                "title": [{"text": {"content": title}}],
                "status": {"status": {"name": "active"}},
                "discord_thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR notion_create_task]", e)
        return None


# セッション開始
async def notion_start_session(task_id: str, thread_id: int):
    """Sessions.DB に新規セッションを作成し session_id を返す。"""
    try:
        res = notion.pages.create(
            parent={"database_id": NOTION_SESSIONS_DB_ID},
            properties={
                "title": [{"text": {"content": f"Session {thread_id}"}}],
                "task": {"relation": [{"id": task_id}]},
                "discord_thread_id": {"rich_text": [{"text": {"content": str(thread_id)}}]},
                "status": {"status": {"name": "active"}},
                "started_at": {"date": {"start": datetime.utcnow().isoformat()}},
            },
        )
        return res["id"]
    except Exception as e:
        print("[ERROR notion_start_session]", e)
        return None


# セッション終了（end_session）
async def notion_end_session(session_id: str, duration_minutes: int, summary: str):
    """セッションを終了し、Duration と Summary を更新する。"""
    try:
        notion.pages.update(
            page_id=session_id,
            properties={
                "status": {"status": {"name": "completed"}},
                "ended_at": {"date": {"start": datetime.utcnow().isoformat()}},
                "duration": {"number": duration_minutes},
                "summary": {"rich_text": [{"text": {"content": summary}}]},
            },
        )
        return True
    except Exception as e:
        print("[ERROR notion_end_session]", e)
        return False


# 会話ログ登録
async def notion_add_log(session_id: str, content: str, timestamp: str):
    """ログを Logs.DB に追加する。"""
    try:
        notion.pages.create(
            parent={"database_id": NOTION_LOGS_DB_ID},
            properties={
                "session": {"relation": [{"id": session_id}]},
                "content": {"rich_text": [{"text": {"content": content}}]},
                "timestamp": {"date": {"start": timestamp}},
            },
        )
        return True
    except Exception as e:
        print("[ERROR notion_add_log]", e)
        return False
        
# ============================================================
# Load Core + External
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
# Discord Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ============================================================
# Memory store
# ============================================================
memory: Dict[int, List[Dict[str, str]]] = {}
MEMORY_LIMIT = 20

def key_from(msg: discord.Message) -> int:
    if isinstance(msg.channel, discord.Thread):
        return msg.channel.id
    return msg.channel.id

def push(key: int, role: str, content: str):
    memory.setdefault(key, [])
    memory[key].append({"role": role, "content": content})
    if len(memory[key]) > MEMORY_LIMIT:
        memory[key] = memory[key][-MEMORY_LIMIT:]

# ============================================================
# Parse FINAL section
# ============================================================
def extract_final_section(text: str) -> str:
    marker = "[FINAL]"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text  # fallback（Ovv が FINAL を書き忘れても安全）

# ============================================================
# Ovv Call
# ============================================================
def call_ovv(key: int, user_msg: str) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(memory.get(key, []))
    msgs.append({"role": "user", "content": user_msg})

    res = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.3,
    )

    full_reply = res.choices[0].message.content.strip()
    push(key, "assistant", full_reply)

    return extract_final_section(full_reply)

# ============================================================
# Natural conversation mode（!o不要）
# ovv-◯◯ チャンネル & そのスレッドのみ
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # スレッド作成時の自動メッセージは無視
    if message.type == MessageType.thread_created:
        return

    # ovv-◯◯ チャンネル以外は応答しない
    if isinstance(message.channel, discord.Thread):
        if not message.channel.parent.name.lower().startswith("ovv-"):
            return
    else:
        if not message.channel.name.lower().startswith("ovv-"):
            return

    key = key_from(message)
    push(key, "user", message.content)

    async with message.channel.typing():
        try:
            answer = call_ovv(key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

    # send FINAL only
    if len(answer) <= 1900:
        await message.channel.send(answer)
    else:
        buf = ""
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                await message.channel.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await message.channel.send(buf)

    await bot.process_commands(message)

# ============================================================
# !o — 明示コマンド（任意）
# ============================================================
@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    key = key_from(ctx.message)
    push(key, "user", question)

    async with ctx.channel.typing():
        answer = call_ovv(key, question)

    await ctx.send(answer)

# ============================================================
# Run
# ============================================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
