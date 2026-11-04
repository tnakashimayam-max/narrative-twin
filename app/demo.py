"""固める処理のボタンと、「10年後へ進む」。

役割：本人の操作で固める処理を流す。架空用の起動では、架空ログを読み込んで2036年へ進む。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：8（架空のデータだけを使う。自分用の起動では 404）、10（本人が託す範囲を決めるまで進めない）、
          13（色は本人が確かめた分だけ）。

「10年後へ進む」は、保存した架空ログを AI を呼ばずに読み込み（fiction/load.py）、そのあと、その場で入れた言葉の
分だけ固める処理を流す。2026年の年と人生の段階も流し直すので、その場で入れた言葉が田中さんに届く。
2回押しても2回読み込まないよう、印と鍵で止める。
進むと戻れないので、最初の起動のときと進む直前に控えを取る。戻す道具は fiction/snapshot.py にある。

関連するテスト：tests/test_demo.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app import ai, clock, config, consolidate, db, devlog, handover, jobs
from app.auth import NOT_FOUND, get_conn, require_self
from app.schemas import User

router = APIRouter(prefix="/api")

_FUTURE_STAGE = "future"
_AI_ERRORS = (config.MissingApiKeyError, ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError)
AI_FAILED_MESSAGE = "いま、記録を整理できませんでした。少し時間をおいて、もう一度お試しください。"


def _story():
    """架空ログの道具を読み込む。自分用の起動では使わない。"""
    from fiction import stage

    return stage


def _snapshot():
    """控えの道具を読み込む。自分用の起動では使わない。"""
    from fiction import snapshot

    return snapshot


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


def _status(request: Request, conn: sqlite3.Connection) -> dict:
    deployment = request.app.state.deployment
    if deployment.mine:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    stage = _story()
    return {
        "future_loaded": stage.is_loaded(conn, _FUTURE_STAGE),
        "ready": stage.story_is_ready(),
        "working": request.app.state.advancing,
        "today": clock.now(request.app).date().isoformat(),
    }


@router.get("/dev/story")
def story_status(
    request: Request, _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """「10年後へ進む」の状態を返す。自分用の起動では 404。"""
    return _status(request, conn)


def _after_advance(app, moment) -> None:
    """読み込みのあとで、その場で入れた言葉の分を固める。失敗しても2036年には進んでいる。"""
    def work(conn) -> None:
        _consolidate_recent(conn, moment)

    try:
        jobs._guarded(app, "固める処理", "10年後へ進む", work)
    finally:
        app.state.advancing = False


@router.post("/dev/advance")
def advance(
    request: Request,
    background: BackgroundTasks,
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """架空ログを読み込み、2036年へ進む。本人が託す範囲を決めていなければ進めない。"""
    app = request.app
    if app.state.deployment.mine:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    stage = _story()
    if not handover.handover_themes(conn):
        raise HTTPException(status_code=400, detail="先に、託す人と範囲を決めてください。")
    with app.state.consolidate_lock:
        if not stage.is_loaded(conn, _FUTURE_STAGE):
            if not stage.story_is_ready():
                raise HTTPException(status_code=409, detail="10年分の記録がまだ用意できていません。")
            # 控えを取れないまま進むと戻せないので、進まない
            tool = _snapshot()
            try:
                tool.save(app.state.deployment, tool.BEFORE_ADVANCE)
            except (tool.SnapshotError, OSError, sqlite3.Error) as error:
                devlog.record("進む前の控え", outcome=devlog.FAILED, error=devlog.error_name(error))
                raise HTTPException(status_code=503, detail="控えを取れなかったので、進みませんでした。") from error
            app.state.advancing = True
            stage.load_future(conn, clock.now(app))
            # 日付を2036年へ進める。専門職の招待と、その後の会話の日付に使う
            app.state.day_offset = timedelta(days=(stage.FUTURE_DAY - clock.now(app).date()).days) + app.state.day_offset
            background.add_task(_after_advance, app, clock.now(app))
    return _status(request, conn)


def restore_day(app) -> bool:
    """進んだあとで起動し直したら、日付を2036年へ合わせる。戻したら True を返す。

    日付のずれは記憶の中にしかないので、起動し直すと2026年に戻り、招いた専門職が入れなくなる。
    """
    deployment = app.state.deployment
    if deployment.mine:
        return False
    stage = _story()
    connection = db.connect(deployment.db_path)
    try:
        loaded = stage.is_loaded(connection, _FUTURE_STAGE)
    finally:
        connection.close()
    if not loaded:
        return False
    app.state.day_offset = timedelta(days=(stage.FUTURE_DAY - date.today()).days)
    return True


def ensure_demo_faces(deployment) -> list[str]:
    """架空用の起動で、顔が決まっている人の空いた枠に写真を入れ、入れた人の名前を返す。

    最初の控えに顔が入るよう、ensure_prologue より前に呼ぶ。create_app からは呼ばない。テストが本物の写真を
    読まないようにするため。写真のフォルダがなければ何も入れず、画面は頭文字の丸のまま動く。
    関連するテスト：tests/test_profile_photo.py
    """
    if deployment.mine:
        return []
    from fiction import faces

    return faces.fill_everyone(deployment)


def ensure_prologue(deployment) -> bool:
    """架空用の起動のときに、2026年11月3日より前の架空ログを入れる。入れたら True を返す。

    入れた直後の控えも取る。入れた言葉と訂正の場面まで撮り直せるようにするため。控えを取れなくても起動は止めない。
    """
    stage = _story()
    if not stage.load.read_logs():
        return False
    connection = db.connect(deployment.db_path)
    try:
        loaded = stage.load_prologue(connection)
    finally:
        connection.close()
    if loaded:
        tool = _snapshot()
        try:
            tool.save(deployment, tool.FIRST_START)
        except (tool.SnapshotError, OSError, sqlite3.Error) as error:
            # 要求の外なので devlog.record は何も書かない。失敗が見えるよう、ログへ直接書く
            devlog.DevLog(deployment.log_path, mine=deployment.mine).write(
                "最初の起動の控え", outcome=devlog.FAILED, error=devlog.error_name(error)
            )
    return loaded
