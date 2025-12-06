# debug/debug_static_messages.py

"""
各 debug チャンネル／スレッドに貼る固定メッセージ定義。
実際に送信・ピン留めするロジックは debug_boot / debug_router 側で行う。
"""

DEBUG_STATIC_MESSAGES = {
    "render": (
        "【render デバッグ用スレッド】\n"
        "- Render のログ貼り付け\n"
        "- 起動失敗時のログ共有\n"
        "- 環境変数更新後の挙動メモ\n"
        "\n"
        "ここは『インフラ挙動』に関するメモ専用です。"
    ),
    "core": (
        "【core デバッグ用スレッド】\n"
        "- SYSTEM_PROMPT 変更の影響確認\n"
        "- Core / External 変更後の挙動検証\n"
        "- LLM 応答の一貫性テスト\n"
        "\n"
        "ここは『Ovv 本体の思考挙動』に関する検証用です。"
    ),
    "notion": (
        "【notion デバッグ用スレッド】\n"
        "- create_task / start_session / end_session のテスト\n"
        "- プロパティ名のズレ検証\n"
        "- Notion 側での実 DB 値のスクショ共有\n"
        "\n"
        "ここは『Notion 連携』専用のデバッグ場所です。"
    ),
    "thread_brain": (
        "【thread_brain デバッグ用スレッド】\n"
        "- !bs / !br / !tt などで JSON 構造を確認\n"
        "- history_digest / decisions / unresolved の中身監査\n"
        "\n"
        "ここは『スレッド脳（長期メモリ）』の挙動を確認・監査する場所です。"
    ),
    "logs": (
        "【logs デバッグ用スレッド】\n"
        "- audit_log 抜粋の貼り付け\n"
        "- openai_error / discord_error / notion_error / thread_brain_* などの追跡\n"
        "\n"
        "ここは『監査ログ』を読む場所です。"
    ),
    "psql": (
        "【psql デバッグ用スレッド】\n"
        "- !sql で SELECT 文を実行し、runtime_memory / audit_log / thread_brain を確認\n"
        "- それ以外の SQL（UPDATE/DELETE/DDL）は原則禁止\n"
        "\n"
        "ここは『PostgreSQL の中身を読むための診断専用スレッド』です。"
    ),
    "boot_log": (
        "【boot_log】\n"
        "Ovv Bot の起動・再起動情報がここに自動投稿されます。\n"
        "- 起動時刻\n"
        "- バージョン\n"
        "- PG/Notion 接続結果（簡易）\n"
        "\n"
        "異常があればここを最初に確認してください。"
    ),
}