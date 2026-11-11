"""今の時刻を1か所で決める。

役割：サーバーの「今」を返す。架空用の起動では、検証と架空の記録づくりのために日付と時刻を動かせる。
4つの極：特定の極に属さない土台。
守る原則：8（動かせるのは架空用の起動だけ。本物の記録の日付は動かさない）。

会話の区切りは日付で決めるので、日を変えた会話を確かめるには「今日」を動かす必要がある。
frozen は、裏の処理の中でだけ時刻を止める。処理を待ちに入れてから動くまでの間に日付が進んでも、
記録の日付が話した日からずれないようにするため。止めるのはその処理の文脈だけで、ほかの要求の「今」は変わらない。
関連するテスト：tests/test_modes.py、tests/test_run_script_jobs.py
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/dev")

# 文脈ごとの値なので、ほかのスレッドと要求には届かない
_frozen: ContextVar[dt.datetime | None] = ContextVar("clock_frozen", default=None)


class DateRequest(BaseModel):
    """date は日だけを動かす。at は時刻まで動かし、その時刻から進む。両方あれば at を使う。"""

    date: dt.date | None = None
    at: dt.datetime | None = None


def now(app) -> dt.datetime:
    """今の時刻を返す。止めていればその時刻を、動かしていれば、その分だけずらした時刻を返す。"""
    moment = _frozen.get()
    if moment is not None:
        return moment
    return dt.datetime.now().astimezone() + app.state.day_offset


@contextmanager
def frozen(moment: dt.datetime) -> Iterator[None]:
    """この中の now を moment に止める。裏の処理を流すときに使う。

    関連するテスト：tests/test_run_script_jobs.py の test_裏の処理が日付の切り替えのあとに動いても_記録の日付は会話の日のまま
    """
    token = _frozen.set(moment if moment.tzinfo else moment.astimezone())
    try:
        yield
    finally:
        _frozen.reset(token)


@router.post("/date")
def set_date(body: DateRequest, request: Request) -> dict[str, str]:
    """サーバーの今日を動かす。自分用の起動では、道がないのと同じ応答にする。"""
    app = request.app
    if app.state.deployment.mine:
        raise HTTPException(status_code=404, detail="見つかりません")
    if body.at is not None:
        target = body.at if body.at.tzinfo else body.at.astimezone()
        app.state.day_offset = target - dt.datetime.now().astimezone()
    elif body.date is None:
        app.state.day_offset = dt.timedelta(0)
    else:
        app.state.day_offset = dt.timedelta(days=(body.date - dt.date.today()).days)
    return {"today": now(app).date().isoformat()}
