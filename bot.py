import os
import discord
from discord.ext import commands
from openai import OpenAI
from datetime import datetime
from typing import Optional

# ============================================================
# 1. Environment Variables
# ============================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if DISCORD_BOT_TOKEN is None:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が設定されていません。")

if OPENAI_API_KEY is None:
    raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません。")

# ============================================================
# 2. File Loader（Ovvコア＋外部契約 読み込み）
# ============================================================
def load_file(path: str) -> str:
    if not os.path.exists(path):
        raise RuntimeError(f"ブートストラップファイル {path} が存在しません。")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

OVV_CORE = load_file("ovv_core.txt")
OVV_EXTERNAL = load_file("ovv_external.txt")

# コア＋外部契約の合成（人格→運用ルール）
OVV_SYSTEM_PROMPT = OVV_CORE + "\n\n" + OVV_EXTERNAL


# ============================================================
# 3. OpenAI Client
# ============================================================
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# 4. Discord Bot Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ============================================================
# 5. Channel-based Memory（外部契約 v1.0）
# ============================================================
CHANNEL_MEMORY = {}       # {channel_id: [ {"role": "user", ...}, ... ]}
MAX_MEMORY = 20           # メモリ保持件数（20件）


def push_memory(channel_id: int, role: str, content: str):
    if channel_id not in CHANNEL_MEMORY:
        CHANNEL_MEMORY[channel_id] = []
    CHANNEL_MEMORY[channel_id].append({"role": role, "content": content})

    # メモリ上限（20件）
    if len(CHANNEL_MEMORY[channel_id]) > MAX_MEMORY:
        CHANNEL_MEMORY[channel_id].pop(0)


def get_memory(channel_id: int):
    return CHANNEL_MEMORY.get(channel_id, [])


# ============================================================
# 6. Call Ovv（文脈＋システムプロンプトを合成）
# ============================================================
def call_ovv_with_context(channel_id: int, user_message: str):
    # 1. ユーザメッセージをメモリへ
    push_memory(channel_id, "user", user_message)

    # 2. 文脈取得
    ctx_history = get_memory(channel_id)

    # 3. プロンプト生成
    messages = [{"role": "system", "content": OVV_SYSTEM_PROMPT}]
    messages.extend(ctx_history)

    # 4. OpenAI 呼び出し
    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.2,
    )

    answer = completion.choices[0].message.content.strip()

    # 5. メモリへ保存
    push_memory(channel_id, "assistant", answer)

    return answer


# ============================================================
# 7. Events
# ============================================================
@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[INFO] Ovv Discord Bot is ready.")


# ============================================================
# 8. !o コマンド（Ovv に話しかける）
# ============================================================
@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    channel_id = ctx.channel.id

    async with ctx.channel.typing():
        try:
            answer = call_ovv_with_context(channel_id, question)
        except Exception as e:
            print(f"[ERROR] call_ovv failed: {e}")
            await ctx.send("通信中にエラーが発生しました。")
            return

    # Discord 文字数制限（2000字）
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


# ============================================================
# 9. Help
# ============================================================
@bot.command(name="help")
async def help_command(ctx: commands.Context):
    msg = (
        "OVV Discord Bot コマンド一覧：\n"
        "```text\n"
        "!o <質問内容>     : Ovv（Universal Product Engineer）に相談\n"
        "（チャンネル単位で文脈を保持し、外部契約に基づき安全処理）\n"
        "```"
    )
    await ctx.send(msg)


# ============================================================
# 10. Run
# ============================================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
