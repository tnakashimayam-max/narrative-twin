"""固める処理のボタンと、流した物語の起動。

役割：本人の操作で固める処理を流す。架空用の起動では、リポジトリの物語を読み込み、記録の日付で開く。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：8（架空のデータだけを使う。自分用の起動では読み込まない）、13（色は本人が確かめた分だけ）。

関連するテスト：tests/test_demo.py、tests/test_story_data.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from app import ai, clock, config, consolidate, db, devlog, handover, ledger
from app.auth import NOT_FOUND, get_conn, require_self
from app.schemas import User

router = APIRouter(prefix="/api")

# 流した物語の今日を残す印
_STORY_DAY_STAGE = "story-day"
_AI_ERRORS = (config.MissingApiKeyError, ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError)
AI_FAILED_MESSAGE = "いま、記録を整理できませんでした。少し時間をおいて、もう一度お試しください。"


def _consolidate_recent(conn: sqlite3.Connection, moment) -> consolidate.RunReport:
    """本人の決めた範囲を新しい言葉に当ててから固める。

    託す範囲を決めたあとに入った言葉は「本人だけ」のまま入ってくる。先に当てないと、その言葉を根拠に持つ要素が
    田中さんに見えない。画面では「選んだテーマの話は見えます」と伝えているので、それと同じ動きにする。
    関連するテスト：tests/test_demo.py の test_託す範囲を決めたあとに入れた言葉も_記録を整理すると本人の決めた範囲になる
    """
    self_user = db.self_user(conn)
    if self_user is not None:
        handover.apply_handover(conn, self_user, at=moment)
    return consolidate.run_recent(conn, moment)


@router.post("/model/consolidate")
def run_consolidate(
    request: Request, _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """言明が変わった月と、その年と人生の段階を固める。同じ入力なら AI を呼ばない。"""
    with request.app.state.consolidate_lock:
        try:
            report = _consolidate_recent(conn, clock.now(request.app))
        except _AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
    devlog.record("固める処理", outcome=devlog.SUCCESS)
    return {"months": report.months, "elements": report.elements}


def own_story(connection: sqlite3.Connection) -> bool:
    """記録が入っているデータベースかを返す。入っていれば、物語を読み込まず、記録の日付で開く。"""
    return connection.execute("SELECT 1 FROM utterances LIMIT 1").fetchone() is not None


@router.get("/dev/story")
def story_status(request: Request, _: User = Depends(require_self)) -> dict:
    """物語の今日を返す。画面が「今日」を出すため。自分用の起動では 404。"""
    if request.app.state.deployment.mine:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"today": clock.now(request.app).date().isoformat()}


def _story_day(connection: sqlite3.Connection) -> date:
    """流した物語の今日を返す。最後に固めた日の翌日で、記録から決まる。最初に決めた日を印に残す。

    印に残すのは、撮影中に「記録を整理する」を押すと最後に固めた日が動き、起動し直すたびに日付が進むため。
    固めていない記録なら、最後の発言の翌日にする。
    """
    mark = connection.execute("SELECT loaded_at FROM story_marks WHERE stage = ?", (_STORY_DAY_STAGE,)).fetchone()
    if mark is not None:
        return date.fromisoformat(mark["loaded_at"][:10])
    last = connection.execute("SELECT MAX(run_at) FROM consolidation_runs").fetchone()[0]
    last = last or connection.execute("SELECT MAX(spoken_at) FROM utterances").fetchone()[0]
    day = date.fromisoformat(last[:10]) + timedelta(days=1)
    with ledger.atomic(connection):
        connection.execute(
            "INSERT INTO story_marks (stage, loaded_at) VALUES (?, ?)",
            (_STORY_DAY_STAGE, f"{day.isoformat()}T00:00:00"),
        )
    return day


def restore_day(app) -> bool:
    """起動し直したら、日付を記録の時点へ合わせる。合わせたら True を返す。

    日付のずれは記憶の中にしかないので、起動し直すと今日に戻り、招いた専門職が入れなくなる。
    関連するテスト：tests/test_demo.py の test_流した物語は起動し直しても同じ日付で田中さんが入れる
    """
    deployment = app.state.deployment
    if deployment.mine:
        return False
    connection = db.connect(deployment.db_path)
    try:
        if not own_story(connection):
            return False
        day = _story_day(connection)
    finally:
        connection.close()
    app.state.day_offset = timedelta(days=(day - date.today()).days)
    return True


def ensure_demo_faces(deployment) -> list[str]:
    """架空用の起動で、顔が決まっている人の空いた枠に写真を入れ、入れた人の名前を返す。

    create_app からは呼ばない。テストが本物の写真を読まないようにするため。写真のフォルダがなければ何も入れず、画面は頭文字の丸のまま動く。
    関連するテスト：tests/test_profile_photo.py
    """
    if deployment.mine:
        return []
    from fiction import faces

    return faces.fill_everyone(deployment)


def ensure_story(deployment) -> bool:
    """架空用の最初の起動で、リポジトリの物語をデータベースへ読み込む。読み込んだら True を返す。AI は呼ばない。

    新しいクローンでキーがなくても、10年分の記録と、保存したツインの考え、写真が開くようにするため。
    関連するテスト：tests/test_story_data.py
    """
    if deployment.mine:
        return False
    from fiction import story_data

    if not story_data.has_story():
        return False
    connection = db.connect(deployment.db_path)
    try:
        if connection.execute("SELECT 1 FROM utterances LIMIT 1").fetchone() is not None:
            return False
        story_data.load(connection)
    finally:
        connection.close()
    story_data.copy_photos(deployment.photos_dir)
    return True

