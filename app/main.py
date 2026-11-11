"""アプリの組み立て。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：データベースを用意し、API と画面のファイルをつなぐ。
4つの極：特定の極に属さない土台。
守る原則：10（API を通さず記録を返す経路を作らない）。

create_app に保存先を渡せるようにしているのは、テストを本物のデータベースから切り離すため。
写真は静的ファイルとしては出さない。data/ を画面のファイルの道につながない。
関連するテスト：tests/test_api.py、tests/test_modes.py
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import (
    advocacy,
    advocacy_chat,
    advocacy_letter,
    advocacy_plan,
    advocacy_sheet,
    advocacy_voice,
    archive,
    auth,
    care_form_records,
    clock,
    config,
    db,
    demo,
    devlog,
    document_chat,
    ecomap,
    eval_cases,
    fresh,
    made_documents,
    growth,
    handover,
    intake,
    life_plan,
    observations,
    people,
    petals,
    photos,
    professional_records,
    records,
    rooms,
    self_letters,
    timeline,
)


class FreshStaticFiles(StaticFiles):
    """画面のファイルを、毎回サーバーに確かめてから使わせる（Cache-Control: no-cache）。

    指示がないと、ブラウザーは起動し直したあとも古い画面のファイルを使い続け、新しい画面が出なかった
    （2026-11-06、ツインの花びらで実際に起きた）。変わっていなければ 304 が返るだけなので、読む量はほとんど増えない。
    関連するテスト：tests/test_api.py の test_画面のファイルは毎回確かめてから使わせる
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    db_path: Path | str | None = None,
    eval_cases_path: Path | str | None = None,
    *,
    deployment: config.Deployment | None = None,
) -> FastAPI:
    """アプリを作る。省くと架空用の保存先（data/narrative_twin.db、data/photos/）を使う。

    db_path を渡すと、写真もその隣に置く。テストを本物の保存先から切り離すため。
    自分用の起動では、架空の4人を入れない。
    """
    deployment = deployment or config.deployment()
    if db_path:
        path = Path(db_path)
        deployment = replace(deployment, db_path=path, photos_dir=path.parent / "photos")
    path = deployment.db_path
    db.init_db(path, seed_demo=not deployment.mine)

    # 自動で作られる API の説明の画面は閉じる。取り決めは docs/api.md に書いているので使わない。
    app = FastAPI(title="Narrative Twin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = path
    app.state.deployment = deployment
    # 開発のログ。自分用では本文を書かない。決まりは app/devlog.py にある。
    app.state.devlog = devlog.DevLog(deployment.log_path, mine=deployment.mine)
    app.add_middleware(devlog.RequestLog)
    app.state.eval_cases_path = Path(eval_cases_path) if eval_cases_path else config.EVAL_CASES_PATH
    app.state.day_offset = timedelta(0)
    # ツインが考えている部屋と、同じ対象を同時に処理しないための鍵。1つのプロセスの中で足りる。
    app.state.thinking_rooms = set()
    app.state.room_locks = {}
    app.state.room_locks_guard = threading.Lock()
    app.state.job_locks = {}
    app.state.job_locks_guard = threading.Lock()
    # 裏の処理の受け手。ふつうは None で、応答のあとにその場で流す（app/jobs.py）。
    # 架空の記録を流す道具（fiction/run_script.py）だけが、流している間に入れる。
    app.state.job_runner = None
    # 専門職の問いを順に処理する鍵。2回押しで AI を2回呼ばないため。
    app.state.advocacy_lock = threading.Lock()
    # ツインのアドボカシーは時間がかかるので、ふだんの問いを待たせないよう別の鍵にする
    app.state.voice_lock = threading.Lock()
    # 固める処理を同時に動かさないための鍵。
    app.state.consolidate_lock = threading.Lock()
    # ツインが問いを出してよいか。ふだんは出してよい。架空の記録を流す道具だけが、問わない日に切り替える（fiction/run_script.py）。
    app.state.twin_may_ask = True
    # /api/people/candidates を先につなぐ。/api/people/{番号} より後だと番号の道に吸われるため。
    app.include_router(auth.router)
    app.include_router(ecomap.router)
    app.include_router(people.router)
    app.include_router(photos.router)
    app.include_router(rooms.router)
    app.include_router(timeline.router)
    app.include_router(archive.router)
    # /api/statements/{番号}/confirm などの道を、記録の道と一緒につなぐ
    app.include_router(records.router)
    app.include_router(handover.router)
    app.include_router(observations.router)
    app.include_router(advocacy.router)
    app.include_router(advocacy_voice.router)
    app.include_router(advocacy_sheet.router)
    app.include_router(advocacy_plan.router)
    app.include_router(advocacy_chat.router)
    app.include_router(professional_records.router)
    app.include_router(advocacy_letter.router)
    app.include_router(care_form_records.router)
    app.include_router(life_plan.router)
    app.include_router(growth.router)
    app.include_router(demo.router)
    app.include_router(intake.router)
    app.include_router(eval_cases.router)
    app.include_router(clock.router)
    app.include_router(made_documents.router)
    app.include_router(document_chat.router)
    app.include_router(petals.router)
    app.include_router(self_letters.router)
    # 入口の「新規で始める」。自分用の起動と新規のアプリには付けない。振り分けが一番外になるよう、ログのあとに足す。
    if not deployment.mine:
        fresh.attach(app, deployment, create_app)
    # 画面のファイルは最後につなぐ。先につなぐと /api の道を隠してしまうため。
    app.mount("/", FreshStaticFiles(directory=config.STATIC_DIR, html=True), name="static")
    return app
