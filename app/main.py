"""アプリの組み立て。

役割：データベースを用意し、API と画面のファイルをつなぐ。
4つの極：特定の極に属さない土台。
守る原則：10（API を通さず記録を返す経路を作らない）。

create_app に保存先を渡せるようにしているのは、テストを本物のデータベースから切り離すため。
関連するテスト：tests/test_api.py
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import auth, config, db, eval_cases, intake, records


def create_app(
    db_path: Path | str | None = None, eval_cases_path: Path | str | None = None
) -> FastAPI:
    """アプリを作る。保存先を省くと data/narrative_twin.db と eval/cases.json を使う。"""
    path = Path(db_path) if db_path else config.DB_PATH
    db.init_db(path)

    # 自動で作られる API の説明の画面は閉じる。取り決めは docs/api.md に書いているので使わない。
    app = FastAPI(title="Narrative Twin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = path
    app.state.eval_cases_path = Path(eval_cases_path) if eval_cases_path else config.EVAL_CASES_PATH
    app.include_router(auth.router)
    app.include_router(records.router)
    app.include_router(intake.router)
    app.include_router(eval_cases.router)
    # 画面のファイルは最後につなぐ。先につなぐと /api の道を隠してしまうため。
    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
    return app
