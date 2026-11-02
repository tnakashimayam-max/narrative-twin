"""設定を1か所にまとめる。

役割：ファイルの置き場所、API キーの読み込み、役割ごとのモデル名を持つ。
4つの極：特定の極に属さない土台。
守る原則：特定の原則には関わらない。

モデルを選んだ理由と選ばなかった案の全体の表は docs/tools.md にある。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "narrative_twin.db"
PHOTOS_DIR = DATA_DIR / "photos"
# 開発のログの置き場。data/ の外に置くのは、自分用の記録を data/mine/ の中だけに保つため（app/devlog.py）。
LOG_DIR = ROOT / "logs"
STATIC_DIR = ROOT / "static"
EVAL_CASES_PATH = ROOT / "eval" / "cases.json"

LABEL_FICTIONAL = "架空データ"
LABEL_MINE = "自分の記録で試用中"


@dataclass(frozen=True)
class Deployment:
    """起動の種類ごとの保存先と表示。

    架空用と自分用で保存先を丸ごと分ける。開発者が自分の本当の言葉と写真で試すとき、
    公開するものに混ざらないようにするため。data/mine/ は Git に入らない（.gitignore）。
    ポートを分けるのは、ブラウザを操作する MCP が開ける先を架空用だけに限るため（.mcp.json）。
    """

    mine: bool
    port: int
    db_path: Path
    photos_dir: Path
    label: str
    log_path: Path


def deployment(*, mine: bool = False, data_dir: Path | None = None) -> Deployment:
    """起動の種類を決める。data_dir を渡せるのは、テストで本物の data/ に触れないため。"""
    base = data_dir if data_dir is not None else DATA_DIR
    if mine:
        folder = base / "mine"
        return Deployment(
            True, 8001, folder / "narrative_twin.db", folder / "photos", LABEL_MINE, LOG_DIR / "mine.jsonl"
        )
    return Deployment(
        False, 8000, base / "narrative_twin.db", base / "photos", LABEL_FICTIONAL, LOG_DIR / "fictional.jsonl"
    )


API_KEY_NAME = "ANTHROPIC_API_KEY"

# アプリの中の Claude の役割ごとに使うモデル。モデル名を変えるときは、ここだけを直す。
# 全体の表（選ばなかった案と実測を含む）は docs/tools.md にある。
_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"
_HAIKU = "claude-haiku-5-5"

MODEL_BY_ROLE: dict[str, str] = {
    # 接続確認：いちばん多く使うモデルまで届くかを確かめるのが目的なので、Haiku で確かめる。
    "接続確認": _HAIKU,
    # 写真の問い：写真を読むのはここだけ。写真に本当に写っているものに触れてほしいので Sonnet。
    "写真の問い": _SONNET,
    # 言明への分割：会話を閉じるたびに裏で動き、回数が多い。本人の言葉の一字一句はプログラムが
    # 確かめる。Sonnet との比べは docs/tools.md にある。
    "言明への分割": _HAIKU,
    # 架空ログの作成：決まった筋書きの文を大量に作る。判断の重さがないので Haiku。
    "架空ログの作成": _HAIKU,
    # ツインの会話：本人が返事を待つので、いちばん速いモデルにする。毎日使っても費用が小さい。
    "ツインの会話": _HAIKU,
    # スレッドの問い：見えている便りとコメントだけを読み、問いを1つ書く。入力が小さいので Haiku。
    "スレッドの問い": _HAIKU,
    # グループのツイン：部屋に見えている発言だけを読み、問いか短い答えを書く。入力が小さいので Haiku。
    "グループのツイン": _HAIKU,
    # 会話の要約：閉じた会話を短くまとめ、次の会話で思い出すために使う。本人の言葉としては扱わないので Haiku。
    "会話の要約": _HAIKU,
    # 便りの下書き：本人の発言を短くまとめるだけで、本人が直してから共有するので Haiku。
    "便りの下書き": _HAIKU,
    # 人の候補：本人の言葉から名前と続柄を抜き出すだけで、決めるのは本人なので Haiku。
    "人の候補": _HAIKU,
    # 固める処理：回数は少ないが、誤ると意思モデル全体に残る。まだ動かない。作るときに Haiku と比べて決める。
    "固める処理": _OPUS,
    # 推定意思：本人の言葉がない場面で、ケアの判断の材料になる。まだ動かない。作るときに比べて決める。
    "推定意思": _OPUS,
    # 原案：ケアプランの原案で、専門職が読む。まだ動かない。作るときに比べて決める。
    "原案": _OPUS,
    # 根拠チェック：原案の根拠を疑う役割。まだ動かない。作るときに比べて決める。
    "根拠チェック": _OPUS,
}

# 安全の確認で断られたときに、API の側で別のモデルへ切り替える機能が使えるモデル。
# Haiku 5.5 にはこの機能がないので、断られた応答はアプリの側で今のメッセージとして扱う。
MODELS_WITH_FALLBACK = {_OPUS, _SONNET}

# 役割ごとの考える深さ。指定のない役割はモデルの既定のまま（Haiku は medium）。
# いずれも短く決まった形の答えなので、深く考えさせて費用と待ち時間を増やす必要がない。
# ツインの会話の深さは、返事の速さと言葉の質を実際に比べて決めた。low と medium で速さはほぼ同じで、
# medium のほうが決まりをよく守ったので medium にした（docs/tools.md）。
EFFORT_BY_ROLE: dict[str, str] = {
    "写真の問い": "low",
    "言明への分割": "medium",
    "架空ログの作成": "low",
    "ツインの会話": "medium",
    "スレッドの問い": "low",
    "グループのツイン": "low",
    "便りの下書き": "low",
    "人の候補": "low",
    "会話の要約": "low",
}

# 役割ごとの応答の上限。考える処理の分もここに含まれるので、余裕を持たせる。
# 切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS_BY_ROLE: dict[str, int] = {
    "架空ログの作成": 6000,
    "言明への分割": 12000,
    "ツインの会話": 2000,
    "写真の問い": 2000,
    "スレッドの問い": 1500,
    "グループのツイン": 1500,
    "便りの下書き": 1500,
    "人の候補": 3000,
    "会話の要約": 1500,
}

# 本人が待つ処理は、長く待たせずに「もう一度送る」を出す。単位は秒。
TIMEOUT_SECONDS_BY_ROLE: dict[str, float] = {
    "ツインの会話": 60,
    "写真の問い": 90,
    "便りの下書き": 60,
}

# ツインが前の話を思い出すために読む量の上限。記録が増えても読む量を増やさないため。
# 合計で約18,000字で、Haiku の高い値段（プロンプトが10万トークン超）にはならない。
# 固める処理ができたら、要約と本人の言葉は意思モデルに置き換える。決めた経緯：docs/decisions.md
MEMORY_LIMITS = {
    "current_utterances": 40,
    "current_chars": 8000,
    "past_conversations": 10,
    "summary_chars": 300,
    "unsummarized_utterances": 20,
    "self_statements": 40,
    "self_chars": 4000,
    "asked_questions": 30,
    "pending_questions": 5,
}

# 公式の料金表（2026-11-02 に確認）。1M トークンあたりの USD。費用の計算と docs/tools.md に使う。
# バッチは入力も出力も半額。Haiku 5.5 だけ、プロンプトが 10万トークンを超えると高い値段になる。
PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    _OPUS: {"input": 4.0, "output": 20.0},
    _SONNET: {"input": 2.0, "output": 10.0},
    _HAIKU: {"input": 0.10, "output": 0.50},
}
LONG_PROMPT_TOKENS = 100_000
PRICE_PER_MTOK_LONG_PROMPT: dict[str, dict[str, float]] = {
    _HAIKU: {"input": 0.50, "output": 2.50},
}
BATCH_DISCOUNT = 0.5

# 1回の応答の上限。ここで切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS = 16000

# 通信の失敗のときに SDK が繰り返す回数。費用と待ち時間を読めるよう、明示する。
MAX_API_RETRIES = 2


def cost_usd(model: str, input_tokens: int, output_tokens: int, *, batch: bool = False) -> float:
    """トークン数から費用を計算する。料金表にないモデルは 0 ではなくエラーにする。"""
    price = PRICE_PER_MTOK[model]
    if input_tokens > LONG_PROMPT_TOKENS and model in PRICE_PER_MTOK_LONG_PROMPT:
        price = PRICE_PER_MTOK_LONG_PROMPT[model]
    cost = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    return cost * BATCH_DISCOUNT if batch else cost


class MissingApiKeyError(RuntimeError):
    """API キーが見つからないとき。"""


def api_key() -> str:
    """API キーを返す。環境変数を先に見て、なければ .env から読む。

    os.environ に書き込まない読み方にしているのは、キーが子のプロセスへ
    意図せず受け渡されるのを避けるため。キーの値は画面にも記録にも出さない。
    """
    key = os.environ.get(API_KEY_NAME) or dotenv_values(ENV_PATH).get(API_KEY_NAME)
    if not key:
        raise MissingApiKeyError(
            f"API キーが見つかりません。{ENV_PATH.name} に {API_KEY_NAME}=... を書いてください。"
        )
    return key
