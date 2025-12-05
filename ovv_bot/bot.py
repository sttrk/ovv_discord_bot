# bot.py - Ovv Discord Bot (BIS Applied / Stable Full Edition A5-Entry)

import os
import json
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
from openai import OpenAI
from notion_client import Client

# ============================================================
# 0. Environment / Config
# ============================================================
from config import (
    DISCORD_BOT_TOKEN,
    OPENAI_API_KEY,
    NOTION_API_KEY,
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
notion = Client(auth=NOTION_API_KEY)

# ============================================================
# 1. PostgreSQL Layer（絶対に from-import しない）
# ============================================================
import database.pg as db_pg

print("=== [BOOT] Connecting PostgreSQL ===")
conn = db_pg.pg_connect()
db_pg.init_db(conn)

log_audit = db_pg.log_audit

# Runtime Memory proxy
load_runtime_memory = db_pg.load_runtime_memory
save_runtime_memory = db_pg.save_runtime_memory
append_runtime_memory = db_pg.append_runtime_memory

# Thread Brain proxy
load_thread_brain = db_pg.load_thread_brain
save_thread_brain = db_pg.save_thread_brain
generate_thread_brain = db_pg.generate_thread_brain

# ============================================================
# 2. Ovv Core Call Layer
# ============================================================
from ovv.ovv_call import (
    call_ovv,
    OVV_CORE,
    OVV_EXTERNAL,
    SYSTEM_PROMPT,
)

# ============================================================
# 3. Debug Context Injection（debug_commands 用）
# ============================================================
from debug.debug_context import debug_context

debug_context.pg_conn = db_pg.PG_CONN
debug_context.notion = notion
debug_context.openai_client = openai_client

debug_context.load_mem = load_runtime_memory
debug_context.save_mem = save_runtime_memory
debug_context.append_mem = append_runtime_memory

debug_context.brain_gen = generate_thread_brain
debug_context.brain_load = load_thread_brain
debug_context.brain_save = save_thread_brain

debug_context.ovv_core = OVV_CORE
debug_context.ovv_external = OVV_EXTERNAL
debug_context.system_prompt = SYSTEM_PROMPT

# dbg_raw などから直接 Ovv を叩くため
debug_context.ovv_call = call_ovv

print("[DEBUG] debug_context injection complete.")

# ============================================================
# 4. Debug Router（必ず context injection 後にロード）
# ============================================================
from debug.debug_router import route_debug_message

# ============================================================
# 5. Notion CRUD（循環依存なし）
# ============================================================
from notion.notion_api import (
    create_task,
    start_session,
    end_session,
    append_logs,
)

# ============================================================
# 6. Boot Log（起動時ステータス）
# ============================================================
from debug.debug_boot import send_boot_message

# ============================================================
# 7. Discord Bot Setup
# ============================================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# ============================================================
# 8. BIS: Boundary Gate Utilities
# ============================================================
def get_context_key(msg: discord.Message) -> int:
    """
    チャンネル or スレッド単位の一意キー。
    DM はチャンネル ID をそのまま使用。
    Guild 内テキストは (guild_id << 32) | channel_id。
    """
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        return ch.id
    if msg.guild is None:
        return ch.id
    return (msg.guild.id << 32) | ch.id


def is_task_channel(msg: discord.Message) -> bool:
    """
    task_ プレフィックス付きチャンネル / スレッドをタスクチャネルとみなす。
    """
    ch = msg.channel
    if isinstance(ch, discord.Thread):
        parent = ch.parent
        return bool(parent and parent.name.lower().startswith("task_"))
    return ch.name.lower().startswith("task_")


# ============================================================
# 9. BIS: Interface Box（Discord → Ovv 入力整形）
# ============================================================
def build_ovv_input(message: discord.Message):
    """
    Discord メッセージから Ovv 呼び出しに必要な情報をまとめる。
    - runtime_memory の更新
    - task_ch なら thread_brain の更新
    戻り値: (context_key, user_text, recent_mem)
    """
    ck = get_context_key(message)
    session_id = str(ck)
    user_text = message.content

    # Runtime memory に user 発話を追記
    append_runtime_memory(
        session_id,
        "user",
        user_text,
        limit=40 if is_task_channel(message) else 12,
    )

    # 直近メモリを取得
    mem = load_runtime_memory(session_id)

    # Task チャンネルでは Thread Brain を更新（要約）
    if is_task_channel(message):
        try:
            summary = generate_thread_brain(ck, mem)
            if summary:
                save_thread_brain(ck, summary)
        except Exception as e:
            print("[InterfaceBox] thread_brain update error:", repr(e))

    return ck, user_text, mem


# ============================================================
# 10. BIS: Stabilizer（Ovv 出力 → Discord 返信用整形）
# ============================================================
def stabilize_output(raw: str) -> str:
    """
    Ovv からの生テキストを Discord に安全に返せる形に整形。
    ルール:
      - [FINAL] があればその後ろだけを返す
      - 無ければ全文を返す
      - 空になった場合はフォールバックメッセージ
      - 2000 文字制限を考慮して 1900 文字付近で truncate
    """
    if raw is None:
        return "Ovv コア処理中にエラーが発生しました。少し時間をおいて再度お試しください。"

    text = raw.strip()
    if "[FINAL]" in text:
        # 最初の [FINAL] 以降だけ採用
        text = text.split("[FINAL]", 1)[1].strip()

    if not text:
        # Thread Brain 上は動いているのに空になるケースの安全弁
        return "了解しました。続きがあれば、もう一度具体的に指示をください。"

    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    return text


# ============================================================
# 11. Events
# ============================================================

@bot.event
async def on_ready():
    print("[READY] Bot connected as", bot.user)
    # 起動時ステータスを boot_log チャンネルへ
    await send_boot_message(bot)


@bot.event
async def on_message(message: discord.Message):

    # --------------------------------------------------------
    # Boundary Gate: 入口フィルタ
    # --------------------------------------------------------
    # Bot 自身・他 Bot には反応しない
    if message.author.bot:
        return

    # スレッド作成通知などのシステムメッセージには反応しない
    if message.type is not discord.MessageType.default:
        return

    # --------------------------------------------------------
    # Debug Router
    # --------------------------------------------------------
    handled = await route_debug_message(bot, message)
    if handled:
        return

    # --------------------------------------------------------
    # Commands（! で始まるもの）
    # --------------------------------------------------------
    if message.content.startswith("!"):
        log_audit("command", {"cmd": message.content})
        await bot.process_commands(message)
        return

    # --------------------------------------------------------
    # 通常メッセージ → BIS: I → Ovv → S
    # --------------------------------------------------------
    try:
        # I: Interface Box
        ck, user_text, recent_mem = build_ovv_input(message)

        # Ovv Core 呼び出し
        raw_ans = call_ovv(ck, user_text, recent_mem)

        # S: Stabilizer
        final_ans = stabilize_output(raw_ans)

        await message.channel.send(final_ans)

    except Exception as e:
        print("[on_message error]", repr(e))
        try:
            log_audit("on_message_error", {
                "error": repr(e),
                "content": message.content[:500],
            })
        except Exception:
            pass
        await message.channel.send(
            "Ovv 処理中に予期しないエラーが発生しました。少し時間をおいて再度お試しください。"
        )


# ============================================================
# 12. Commands
# ============================================================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")


@bot.command(name="br")
async def brain_regen(ctx):
    """
    現在コンテキストの runtime_memory から thread_brain を再生成。
    """
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if summary:
        save_thread_brain(ck, summary)
        await ctx.send("thread_brain を再生成しました。")
    else:
        await ctx.send("生成に失敗しました。")


@bot.command(name="bs")
async def brain_show(ctx):
    """
    現在コンテキストの thread_brain を JSON で表示。
    """
    ck = get_context_key(ctx.message)
    summary = load_thread_brain(ck)

    if not summary:
        await ctx.send("thread_brain はまだありません。")
        return

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if len(text) > 1900:
        text = text[:1900] + "\n...[truncated]"

    await ctx.send(f"```json\n{text}\n```")


@bot.command(name="tt")
async def test_thread(ctx):
    """
    runtime_memory → thread_brain → 保存までをテスト。
    """
    ck = get_context_key(ctx.message)
    mem = load_runtime_memory(str(ck))
    summary = generate_thread_brain(ck, mem)

    if not summary:
        await ctx.send("thread_brain 生成失敗")
        return

    save_thread_brain(ck, summary)
    await ctx.send("test OK: summary saved")


# ============================================================
# 13. Run
# ============================================================
print("[BOOT] Starting Discord Bot")
bot.run(DISCORD_BOT_TOKEN)