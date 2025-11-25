import os
import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional

# =======================================
# 環境変数
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
# OpenAI クライアント
# =======================================
from openai import OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

OVV_SYSTEM_PROMPT = """
You are Ovv (“Universal Product Engineer”).
You design learning plans, development roadmaps, and perform light architecture thinking
for Python learning + Discord bot + GitHub + Notion integration.

日本語ユーザを前提に、回答は日本語で行う。
Proposal / Audit / Final の3フェーズを意識しつつ、Discord で読める長さにまとめること。
"""

# =======================================
# Discord Bot 設定
# =======================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# =======================================
# OpenAI 呼び出し
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
# ログ保存（暫定ローカル）
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

def save_log_to_github(user_id: int, content: str):
    if not GITHUB_TOKEN:
        return
    pass

def save_log_to_notion(user_id: int, content: str):
    if not (NOTION_TOKEN and NOTION_DATABASE_ID):
        return
    pass

def register_learning_log(user_id: int, content: str):
    save_log_local(user_id, content)
    save_log_to_github(user_id, content)
    save_log_to_notion(user_id, content)


# =======================================
# Bot イベント
# =======================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[INFO] Ovv Discord Bot is ready.")

# ★★★ 追加した最重要イベント ★★★
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    # コマンドが無視される問題を防ぐ
    await bot.process_commands(message)


# =======================================
# !ovv — Ovv に質問
# =======================================
@bot.command(name="ovv")
async def ovv_command(ctx: commands.Context, *, question: str):
    await ctx.trigger_typing()
    try:
        answer = call_ovv(question, mode="general")
    except Exception as e:
        print(f"[ERROR] call_ovv failed: {e}")
        await ctx.send("OVV との通信中にエラーが発生しました。")
        return

    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        buf = ""
        chunks = []
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                chunks.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            chunks.append(buf)
        for chunk in chunks:
            await ctx.send(chunk)

# =======================================
# !log — 学習ログ
# =======================================
@bot.command(name="log")
async def log_command(ctx: commands.Context, *, content: str):
    register_learning_log(ctx.author.id, content)
    await ctx.send("学習ログを記録しました。")

# =======================================
# !plan — 学習計画
# =======================================
@bot.command(name="plan")
async def plan_command(ctx: commands.Context, *, goal: Optional[str] = None):
    await ctx.trigger_typing()
    if goal is None:
        goal = "Python を実務レベルで使えるようになること"

    prompt = (
        "次のゴールに向けた学習ロードマップを作ってください。\n"
        f"・ゴール: {goal}\n"
        "・週5〜7時間想定\n"
        "・フェーズごとに分割\n"
    )

    try:
        answer = call_ovv(prompt, mode="plan")
    except Exception as e:
        print(f"[ERROR] plan_command failed: {e}")
        await ctx.send("学習プラン生成中にエラーが発生しました。")
        return

    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        buf = ""
        chunks = []
        for line in answer.splitlines(True):
            if len(buf) + len(line) > 1900:
                chunks.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            chunks.append(buf)
        for chunk in chunks:
            await ctx.send(chunk)


# =======================================
# !help
# =======================================
@bot.command(name="help")
async def help_command(ctx: commands.Context):
    msg = (
        "OVV Discord Bot コマンド一覧：\n"
        "```text\n"
        "!ovv <質問>     : Ovv AI に相談\n"
        "!log <内容>     : 学習ログを記録\n"
        "!plan [ゴール]  : Python 学習計画生成\n"
        "```"
    )
    await ctx.send(msg)


# =======================================
# エントリポイント
# =======================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
