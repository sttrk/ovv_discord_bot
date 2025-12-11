# ============================================================
# MODULE CONTRACT: BIS / context_splitter v1.2
#
# ROLE:
#   - LLM が生成した自然文（TaskSummary / ThreadBrain summary）から、
#     「LLM向け指示文（control語）」を分離し、
#     Domain（タスク内容・因果）部分だけを安全に残す。
#
# USE CASES:
#   - Stabilizer → Notion Task Summary 同期前
#   - ThreadBrain summary（LLM出力）sanitizing 前処理
#
# RESPONSIBILITY TAGS:
#   [SCAN]     テキストの行単位スキャン
#   [DETECT]   control 語（JSONで返せ / マークダウン禁止 等）の検出
#   [SPLIT]    domain_text / control_text の二分割
#   [RETURN]   安全な domain_text を返却
#
# CONSTRAINTS:
#   - LLM を呼ばない（pure local filter）
#   - 判定は deterministic
#   - Domain（タスク内容）を破壊しない（control 過剰検出禁止）
#   - ファイル単独で完結し、BIS の他層へ依存しない
#   - output は常に (domain_text, control_text)
# ============================================================

from __future__ import annotations
from typing import Tuple, List


# ============================================================
# [DETECT] Control keyword patterns
#   - threadbrain_adapter / constraint_classifier と整合を取った制御語
#   - LLM向けの「形式指示」「越境指示」「メタ命令」を中心に検出
# ============================================================

CONTROL_KEYWORDS = [
    # 出力形式強制
    "jsonで返", "json で返", "json形式", "json 形式",
    "yaml", "xml",
    "markdown禁止", "マークダウン禁止", "markdown で返すな",
    "構造化データで返", "構造化データのみ", "オブジェクトのみ",

    # 越境・プロンプト改変
    "ignore the system prompt",
    "override the system prompt",
    "jailbreak",

    # role 指示
    "あなたは", "として振る舞", "として動作しろ", "you are now",

    # 出力制御
    "のみで答え", "only respond", "output only",
    "説明文を含めない", "含めない", "禁止",
]

# より高速な prefix チェック
CONTROL_PREFIX = [
    "[CONTROL]", "[PROMPT]", "[SYS]", "[META]",
]


# ============================================================
# [SCAN] detect routine
# ============================================================

def _is_control_line(line: str) -> bool:
    """1 行が control かどうかを判定する（責務: [DETECT]）"""
    t = line.strip()
    if not t:
        return False

    # prefix 判定（最も強力）
    for pref in CONTROL_PREFIX:
        if t.startswith(pref):
            return True

    lower = t.lower()

    # keyword 判定
    for kw in CONTROL_KEYWORDS:
        if kw.lower() in lower:
            return True

    return False


# ============================================================
# [SPLIT] domain/control の分離
# ============================================================

def split_context_text(text: str) -> Tuple[str, str]:
    """
    自然文テキストから domain/control を分離する。
    返り値: (domain_text, control_text)

    - domain_text: タスク内容・因果・説明など純粋な自然文
    - control_text: LLM 指示文・越境命令・フォーマット制御など
    """
    if not isinstance(text, str):
        return "", ""

    domain_lines: List[str] = []
    control_lines: List[str] = []

    for line in text.split("\n"):
        if _is_control_line(line):
            control_lines.append(line)
        else:
            domain_lines.append(line)

    domain_text = "\n".join([x for x in domain_lines if x.strip()])
    control_text = "\n".join([x for x in control_lines if x.strip()])

    return domain_text, control_text


# ============================================================
# [RETURN] 便利ヘルパ
# ============================================================

def extract_domain_only(text: str) -> str:
    """
    Domain のみを返すショートカット。

    - control が大量でも domain が空なら text 全体を返す（フェールセーフ）
    """
    domain, control = split_context_text(text)
    return domain if domain.strip() else text