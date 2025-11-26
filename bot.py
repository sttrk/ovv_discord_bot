import os
import discordimport os
import discord
from discord.ext import commands
from openai import OpenAI
from typing import Dict, List, Optional
from datetime import datetime

# ============================================================
# Environment Variables
# ============================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("環境変数 DISCORD_BOT_TOKEN が未設定です。")

if not OPENAI_API_KEY:
    raise RuntimeError("環境変数 OPENAI_API_KEY が未設定です。")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# 外部ファイル読込（Core + External Contract）
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
# Discord Bot Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ============================================================
# Memory store（スレッド / チャンネル単位で文脈分離）
# ============================================================
memory: Dict[int, List[Dict[str, str]]] = {}
MEMORY_LIMIT = 20

def get_context_key(message: discord.Message) -> int:
    if isinstance(message.channel, discord.Thread):
        return message.channel.id
    else:
        return message.channel.id

def append_user_message(context_key: int, content: str):
    if context_key not in memory:
        memory[context_key] = []

    memory[context_key].append({"role": "user", "content": content})

    if len(memory[context_key]) > MEMORY_LIMIT:
        memory[context_key] = memory[context_key][-MEMORY_LIMIT:]

def append_assistant_message(context_key: int, content: str):
    if context_key not in memory:
        memory[context_key] = []

    memory[context_key].append({"role": "assistant", "content": content})

    if len(memory[context_key]) > MEMORY_LIMIT:
        memory[context_key] = memory[context_key][-MEMORY_LIMIT:]

# ============================================================
# Ovv API 呼び出し
# ============================================================
def call_ovv(context_key: int, prompt: str) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 過去文脈を注入
    if context_key in memory:
        msgs.extend(memory[context_key])

    msgs.append({"role": "user", "content": prompt})

    completion = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=msgs,
        temperature=0.3,
    )

    return completion.choices[0].message.content.strip()

# ============================================================
# Main Listener (!o 不要 / ovv-◯◯限定)
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    # 1. bot 自身のメッセージは無視
    if message.author.bot:
        return

    # 2. チャンネル名 or 親チャンネル名が "ovv-" 始まりか確認
    channel = message.channel

    if isinstance(channel, discord.Thread):
        parent_name = channel.parent.name.lower()
        if not parent_name.startswith("ovv-"):
            return
    else:
        if not channel.name.lower().startswith("ovv-"):
            return

    # 3. 文脈キー取得
    context_key = get_context_key(message)

    # 4. ユーザ発話を記憶
    append_user_message(context_key, message.content)

    # 5. typing 表示
    async with message.channel.typing():
        try:
            response = call_ovv(context_key, message.content)
        except Exception as e:
            print("[ERROR call_ovv]", e)
            await message.channel.send("Ovv との通信中にエラーが発生しました。")
            return

    # 6. 文脈に追加
    append_assistant_message(context_key, response)

    # 7. Discord の 2000 字制限対策
    if len(response) <= 1900:
        await message.channel.send(response)
    else:
        buf = ""
        for line in response.splitlines(True):
            if len(buf) + len(line) > 1900:
                await message.channel.send(buf)
                buf = line
            else:
                buf += line
        if buf:
            await message.channel.send(buf)

# ============================================================
# 明示コマンド "!o"（任意で使用可能）
# ============================================================
@bot.command(name="o")
async def o_command(ctx: commands.Context, *, question: str):
    context_key = get_context_key(ctx.message)
    append_user_message(context_key, question)

    async with ctx.channel.typing():
        answer = call_ovv(context_key, question)

    append_assistant_message(context_key, answer)

    await ctx.send(answer)

# ============================================================
# Run
# ============================================================
def main():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
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
