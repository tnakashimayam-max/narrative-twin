"""今の時刻を1か所で決める。

役割：サーバーの「今」を返す。架空用の起動では、検証のために日付を動かせる。
4つの極：特定の極に属さない土台。
守る原則：8（日付を動かせるのは架空用の起動だけ。本物の記録の日付は動かさない）。

会話の区切りは日付で決めるので、日を変えた会話を確かめるには「今日」を動かす必要がある。
関連するテスト：tests/test_modes.py
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/dev")


class DateRequest(BaseModel):
    date: dt.date | None = None


def now(app) -> dt.datetime:
    """今の時刻を返す。日付を動かしていれば、その分だけずらす。"""
    return dt.datetime.now().astimezone() + app.state.day_offset


@router.post("/date")
def set_date(body: DateRequest, request: Request) -> dict[str, str]:
    """サーバーの今日を動かす。自分用の起動では、道がないのと同じ応答にする。"""
    app = request.app
    if app.state.deployment.mine:
        raise HTTPException(status_code=404, detail="見つかりません")
    if body.date is None:
        app.state.day_offset = dt.timedelta(0)
    else:
        app.state.day_offset = dt.timedelta(days=(body.date - dt.date.today()).days)
    return {"today": now(app).date().isoformat()}
