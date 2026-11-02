"""設定を1か所にまとめる。

役割：ファイルの置き場所、API キーの読み込み、役割ごとのモデル名を持つ。
4つの極：特定の極に属さない土台。
守る原則：特定の原則には関わらない。

モデルを選んだ理由と選ばなかった案の全体の表は docs/tools.md にある。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "narrative_twin.db"
STATIC_DIR = ROOT / "static"

API_KEY_NAME = "ANTHROPIC_API_KEY"

# アプリの中の Claude の役割ごとに使うモデル。モデル名を変えるときは、ここだけを直す。
MODEL_BY_ROLE: dict[str, str] = {
    # 接続確認：あとの処理で使う既定のモデルまで届くかを確かめるのが目的なので、
    # 安いモデルに替えず、既定のモデルで確かめる。
    "接続確認": "claude-opus-5-5",
}

# 1回の応答の上限。ここで切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS = 16000


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
