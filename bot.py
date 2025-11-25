import os
import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional

# =======================================
# 環境変数からトークンを取得
# =======================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# （将来用）GitHub / Notion 連携用
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if DISCORD_BOT_TOKEN is None:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")

if OPENAI_API_KEY is None:
    raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません。")

# =======================================
# OpenAI クライアント（chatGPT API）
# =======================================
from openai import OpenAI

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Ovv ブートストラップ（簡易版）
# 本格運用時は、あなたが作った Ovv v1.3.x ブートストラップ全文をここに貼り替えてよいです。
OVV_SYSTEM_PROMPT = """
You are Ovv (“Universal Product Engineer”).
You design learning plans, development roadmaps, and perform light architecture thinking
for Python learning + Discord bot + GitHub + Notion integration.

日本語ユーザを前提に、回答は日本語で行う。
Proposal / Audit / Final の3フェーズを意識しつつ、Discord で読める長さにまとめること。
"""

# =======================================
# Discord Bot のセットアップ
# =======================================
intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容を読めるようにする

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# =======================================
# OpenAI / Ovv 呼び出しヘルパ
# =======================================
def call_ovv(prompt: str, mode: str = "general") -> str:
    """
    Ovv として ChatGPT API を叩くヘルパー。
    将来的に mode（'plan', 'log', 'architecture' など）で
    プロンプトの前処理を変えることもできる。
    """
    messages = [
        {"role": "system", "content": OVV_SYSTEM_PROMPT},
        {"role": "user", "content": f"[MODE={mode}]\n{prompt}"},
    ]

    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",  # コストと速度のバランス用。必要なら gpt-4.1 に
        messages=messages,
        temperature=0.3,
    )

    return completion.choices[0].message.content.strip()


# =======================================
# 学習ログ保存の“土台”関数群
# =======================================
LOG_FILE_PATH = "learning_logs.txt"  # 当面はローカルファイル。あとで GitHub/Notion に切り替え。


def save_log_local(user_id: int, content: str) -> None:
    """
    当面の暫定ストレージ：Render / ローカル環境用。
    実運用では GitHub or Notion に差し替える前提。
    """
    now = datetime.utcnow().isoformat()
    line = f"{now}\tuser={user_id}\t{content}\n"
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # ログ失敗は致命的ではないので raise せず黙殺
        print(f"[WARN] Failed to write local log: {e}")


def save_log_to_github(user_id: int, content: str) -> None:
    """
    将来の GitHub 連携用のフック。
    - GITHUB_TOKEN で private repo に push する
    - issue や discussion として投稿する
    など、好きな形で実装してよい。
    """
    if not GITHUB_TOKEN:
        # まだ未設定の場合は何もしない
        return
    # TODO: PyGithub や GitHub API を使って実装
    # 例: 今日の日付ごとの Markdown に追記するなど
    pass


def save_log_to_notion(user_id: int, content: str) -> None:
    """
    将来の Notion 連携用のフック。
    - NOTION_TOKEN / NOTION_DATABASE_ID を使って
      日次ログ DB に 1レコード追加するイメージ。
    """
    if not (NOTION_TOKEN and NOTION_DATABASE_ID):
        return
    # TODO: notion-sdk-py などを利用して実装
    pass


def register_learning_log(user_id: int, content: str) -> None:
    """
    ログ登録の統合窓口。
    今はローカルファイル + 将来の GitHub/Notion をまとめて呼ぶ。
    """
    save_log_local(user_id, content)
    save_log_to_github(user_id, content)
    save_log_to_notion(user_id, content)


# =======================================
# Bot イベント
# =======================================@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    
    # これがないとコマンドが無視されることがある
    await bot.process_commands(message)

# =======================================
# コマンド: !ovv  – Ovv に質問する
# =======================================
@bot.command(name="ovv")
async def ovv_command(ctx: commands.Context, *, question: str):
    """
    使い方:
    !ovv Pythonの学習ロードマップを作って
    """
    await ctx.trigger_typing()

    try:
        answer = call_ovv(question, mode="general")
    except Exception as e:
        print(f"[ERROR] call_ovv failed: {e}")
        await ctx.send("OVV との通信中にエラーが発生しました。少し待ってから再度お試しください。")
        return

    # Discord の1メッセージ上限に収まるように分割（2000文字制限）
    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        # 長すぎる場合は複数メッセージに分割
        chunks = []
        buf = ""
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
# コマンド: !log – 学習ログを残す
# =======================================
@bot.command(name="log")
async def log_command(ctx: commands.Context, *, content: str):
    """
    使い方:
    !log 今日 for 文と if 文の基礎を学んだ。FizzBuzz を途中まで実装。
    """
    user_id = ctx.author.id
    register_learning_log(user_id, content)
    await ctx.send("学習ログを記録しました。（将来 GitHub / Notion にも反映させる予定です）")


# =======================================
# コマンド: !plan – Python 学習プランを作る
# =======================================
@bot.command(name="plan")
async def plan_command(ctx: commands.Context, *, goal: Optional[str] = None):
    """
    使い方:
    !plan
    !plan Discord Bot を自作できるレベルまで
    """
    await ctx.trigger_typing()

    if goal is None:
        goal = "Python を実務レベルで使えるようになること"

    prompt = (
        "次のゴールに向けた学習ロードマップを作ってください。\n"
        "・対象者: Python 初心者〜入門レベル\n"
        f"・ゴール: {goal}\n"
        "・週あたりの学習時間: 5〜7時間くらいを想定\n"
        "・フェーズ単位で分割し、各フェーズの到達目標とステップを整理してください。"
    )

    try:
        answer = call_ovv(prompt, mode="plan")
    except Exception as e:
        print(f"[ERROR] plan call_ovv failed: {e}")
        await ctx.send("学習プラン生成中にエラーが発生しました。")
        return

    if len(answer) <= 1900:
        await ctx.send(answer)
    else:
        chunks = []
        buf = ""
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
# （任意）!help コマンド
# =======================================
@bot.command(name="help")
async def help_command(ctx: commands.Context):
    msg = (
        "OVV Discord Bot コマンド一覧：\n"
        "```text\n"
        "!ovv <質問内容>   : Ovv に相談 / 質問する\n"
        "!log <内容>       : 学習ログを記録する（あとで GitHub / Notion 連携予定）\n"
        "!plan [ゴール]    : Python 学習ロードマップを提案してもらう\n"
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
