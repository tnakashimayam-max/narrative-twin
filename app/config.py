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


def deployment(*, mine: bool = False, data_dir: Path | None = None) -> Deployment:
    """起動の種類を決める。data_dir を渡せるのは、テストで本物の data/ に触れないため。"""
    base = data_dir if data_dir is not None else DATA_DIR
    if mine:
        folder = base / "mine"
        return Deployment(True, 8001, folder / "narrative_twin.db", folder / "photos", LABEL_MINE)
    return Deployment(False, 8000, base / "narrative_twin.db", base / "photos", LABEL_FICTIONAL)


API_KEY_NAME = "ANTHROPIC_API_KEY"

# アプリの中の Claude の役割ごとに使うモデル。モデル名を変えるときは、ここだけを直す。
# 全体の表（選ばなかった案と実測を含む）は docs/tools.md にある。
_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"

MODEL_BY_ROLE: dict[str, str] = {
    # 接続確認：判断の重い役割で使うモデルまで届くかを確かめるのが目的なので、Opus で確かめる。
    "接続確認": _OPUS,
    # 写真の問い：写真を読み、短い問いを1つ返すだけ。本人が待つ場面なので、速さと費用で Sonnet。
    "写真の問い": _SONNET,
    # 言明への分割：発言のたびに動き、10年分では回数が多い。本人の言葉の一字一句はプログラムが
    # 確かめるので、費用の低い Sonnet にしている。
    "言明への分割": _SONNET,
    # 架空ログの作成：決まった筋書きの文を大量に作る。判断の重さがないので Sonnet。
    "架空ログの作成": _SONNET,
    # ツインの会話：本人が返事を待つ。速さと、やさしい言葉の質の釣り合いで Sonnet。
    "ツインの会話": _SONNET,
    # スレッドの問い：見えている便りとコメントだけを読み、問いを1つ書く。入力が小さいので Sonnet。
    "スレッドの問い": _SONNET,
    # 固める処理：回数は少ないが、誤ると意思モデル全体に残る。判断の質を優先して Opus。
    "固める処理": _OPUS,
    # 推定意思：本人の言葉がない場面で、ケアの判断の材料になる。誤りの影響が大きいので Opus。
    "推定意思": _OPUS,
    # 原案：ケアプランの原案で、専門職が読む。根拠のつながりの質を優先して Opus。
    "原案": _OPUS,
    # 根拠チェック：原案の根拠を疑う役割。原案と同じ強さのモデルにする。
    "根拠チェック": _OPUS,
}

# 役割ごとの考える深さ。指定のない役割はモデルの既定のまま。
# いずれも短く決まった形の答えなので、深く考えさせて費用を増やす必要がない。
EFFORT_BY_ROLE: dict[str, str] = {
    "写真の問い": "low",
    "言明への分割": "medium",
    "架空ログの作成": "low",
}

# 役割ごとの応答の上限。バッチは数が多いので、1回あたりの費用の上限をここで決める。
MAX_TOKENS_BY_ROLE: dict[str, int] = {
    "架空ログの作成": 4000,
    "言明への分割": 8000,
}

# 公式の料金表（2026-11-02 に確認）。1M トークンあたりの USD。費用の計算と docs/tools.md に使う。
# バッチは入力も出力も半額。
PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    _OPUS: {"input": 4.0, "output": 20.0},
    _SONNET: {"input": 2.0, "output": 10.0},
}
BATCH_DISCOUNT = 0.5

# 1回の応答の上限。ここで切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS = 16000

# 通信の失敗のときに SDK が繰り返す回数。費用と待ち時間を読めるよう、明示する。
MAX_API_RETRIES = 2


def cost_usd(model: str, input_tokens: int, output_tokens: int, *, batch: bool = False) -> float:
    """トークン数から費用を計算する。料金表にないモデルは 0 ではなくエラーにする。"""
    price = PRICE_PER_MTOK[model]
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
