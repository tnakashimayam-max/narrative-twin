"""起動の入口。python -m app で架空用、python -m app --mine で自分用が動く。

役割：開発用のサーバーを起動する。
4つの極：特定の極に属さない土台。
守る原則：8（提出物とデモは架空。開発者が自分の記録で試すときは手元だけに置く）。

どちらも 127.0.0.1 だけで待ち受ける。パスワードのない簡易ログインを、
同じネットワークのほかの機器から開けないようにするため。
保存先の分け方は app/config.py の Deployment にある。
関連するテスト：tests/test_modes.py
"""

from __future__ import annotations

import argparse
import threading
from collections.abc import Sequence

import uvicorn

from app import config, jobs
from app.main import create_app

HOST = "127.0.0.1"


def choose_deployment(argv: Sequence[str] | None = None) -> config.Deployment:
    """起動の指定を読む。"""
    parser = argparse.ArgumentParser(prog="python -m app", description="Narrative Twin を起動します。")
    parser.add_argument("--mine", action="store_true", help="自分用（ポート 8001、data/mine/）で起動します。")
    return config.deployment(mine=parser.parse_args(argv).mine)


def main() -> None:
    deployment = choose_deployment()
    app = create_app(deployment=deployment)
    # 前回の起動で止まった処理を流し直す。テストが本物の API を呼ばないよう、ここからだけ呼ぶ。
    threading.Thread(target=jobs.catch_up, args=(app,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=deployment.port)


if __name__ == "__main__":
    main()
