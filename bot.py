import os
import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional
from openai import OpenAI

# =======================================
# Environment Variables
# =======================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if DISCORD_BOT_TOKEN is None:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")

if OPENAI_API_KEY is None:
    raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません。")

# =======================================
# OpenAI Client
# =======================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)

OVV_SYSTEM_PROMPT = """
You are Ovv (“Universal Product Engineer”).
You design learning plans, development roadmaps, and perform light architecture thinking
for Python learning + Discord bot + GitHub + Notion integration.

日本語ユーザを前提に、回答は日本語で行う。
Proposal / Audit / Final の3フェーズを意識しつつ、Discord で読める長さにまとめること。
"""

# =======================================
# Discord Bot
# =======================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# =======================================
# OpenAI Call Helper
# =======================================
def call_ovv(prompt: str, mode: str = "general") -> str:
    messages = [
        {"role": "system", "content": OVV_SYSTEM_PROMPT},
        {"role": "user", "content": f"[MODE={mode}]\n{prompt}"},
    ]

    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.3,
    )

    return completion.choices[0].message.content.strip()

# =======================================
# Local Logging (暫定)
# =======================================
LOG_FILE_PATH = "learning_logs.txt"

def save_log_local(user_id: int, content: str):
    now = datetime.utcnow().isoformat()
    line = f"{now}\tuser={user_id}\t{content}\n"
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[WARN] Failed to write local log: {e}")

def register_learning_log(user_id: int, content: str):
    save_log_local(user_id, content)
    # GitHub / Notion は将来追加

# =======================================
# Events
# =======================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[INFO] Ovv Discord Bot is ready.")

# =======================================
# Commands
# =======================================
@bot.command(name="ovv")
async def ovv_command(ctx: commands.Context, *, question: str):
    async with ctx.channel.typing():   # ← 修正：trigger_typing() は廃止
        try:
            answer = call_ovv(question)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await ctx.send("OVV との通信中にエラーが発生しました。")
            return

    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        buf = ""
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                await ctx.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await ctx.send(buf)

@bot.command(name="log")
async def log_command(ctx: commands.Context, *, content: str):
    user_id = ctx.author.id
    register_learning_log(user_id, content)
    await ctx.send("学習ログを記録しました。")

@bot.command(name="plan")
async def plan_command(ctx: commands.Context, *, goal: Optional[str] = None):
    if goal is None:
        goal = "Python を実務レベルで使えるようになること"

    prompt = (
        "次のゴールに向けた学習ロードマップを作ってください。\n"
        f"・ゴール: {goal}\n"
    )

    async with ctx.channel.typing():
        try:
            answer = call_ovv(prompt, mode="plan")
        except Exception as e:
            print(f"[ERROR] plan call_ovv failed: {e}")
            await ctx.send("学習プラン生成中にエラーが発生しました。")
            return

    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        buf = ""
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                await ctx.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await ctx.send(buf)

@bot.command(name="help")
async def help_command(ctx: commands.Context):
    msg = (
        "OVV Discord Bot コマンド一覧：\n"
        "```text\n"
        "!ovv <質問内容>\n"
        "!log <内容>\n"
        "!plan [ゴール]\n"
        "```"
    )
    await ctx.send(msg)

# =======================================
# Run
# =======================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
