# debug/debug_router.py
# Debug Router v3.1 (BIS / OVFS Compliant)
#
# ROLE:
#   - "!dbg_xxx" を Ovv パイプラインに流さず止める「ゲート」
#   - 実際の処理は debug_commands（bot.command）側が担当する
#
# MUST:
#   - "!dbg" prefix のみ検知する
#   - pipeline へ渡さない（True を返す）
#   - handler を import しない（commands.Bot に任せる）
#
# MUST NOT:
#   - debug_commands の関数を直接 import しない
#   - 余計なロジック・状態・DB アクセスを行わない


async def route_debug_message(bot, message):
    content = message.content.strip()

    # Debug prefix を検知
    if not content.startswith("!dbg"):
        return False

    # "!dbg" 系は全て「Discord コマンド」として bot.process_commands が拾うので
    # router は「パイプラインに流さない」という意味で True を返すだけで良い。
    return True