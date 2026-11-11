"""ツインの色を計算する（決まった手順で動く層）。AI は呼ばない。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：本人が確かめた根拠を持つ意思モデルの要素から、花びらごとの色の濃さを求める。
4つの極：3. 読み解き、積み上げる。
守る原則：13（本人が確かめた根拠を持つ要素だけで色がのる。数字、割合、連続日数は出さない。
          使わなくても色は減らさない）。

色は保存せず、その時点の意思モデルから毎回作る。確かめた行は追記だけで消えないので、
ある日までに確かめた行を数えれば、あとから減ることがない。お休みにしても、前に確かめた分の色は減らさない。

返すのは濃さの言葉だけで、数を返さない。本人の画面に花びらの名前を出さないため、順番だけで返す。

関連するテスト：tests/test_growth.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request

from app import clock
from app.auth import current_user, get_conn
from app.schemas import ConfirmStatus, Petal, User

router = APIRouter(prefix="/api/twin")

NONE = "なし"
LIGHT = "うすい"
MIDDLE = "中くらい"
DEEP = "こい"
_LIGHT_FROM = 1
_MIDDLE_FROM = 4
_DEEP_FROM = 10


def _level(count: int) -> str:
    if count >= _DEEP_FROM:
        return DEEP
    if count >= _MIDDLE_FROM:
        return MIDDLE
    if count >= _LIGHT_FROM:
        return LIGHT
    return NONE


def _first_confirmed(conn: sqlite3.Connection) -> dict[int, date]:
    """言明の連鎖のいちばん元の行ごとに、本人が初めて確かめた日を返す。"""
    rows = conn.execute("SELECT id, replaces_id, confirm_status, created_at FROM statements ORDER BY rowid").fetchall()
    roots: dict[int, int] = {}
    first: dict[int, date] = {}
    done = {ConfirmStatus.CONFIRMED.value, ConfirmStatus.CORRECTED.value}
    for row in rows:
        root = roots.get(row["replaces_id"], row["replaces_id"]) if row["replaces_id"] else row["id"]
        roots[row["id"]] = root
        if row["confirm_status"] in done:
            day = datetime.fromisoformat(row["created_at"]).date()
            if root not in first or day < first[root]:
                first[root] = day
    return first


def levels_by_day(conn: sqlite3.Connection, days: list[date]) -> list[list[str]]:
    """それぞれの日の、花びらごとの濃さを返す。要素の版を問わず数えるので、減らない。"""
    confirmed = _first_confirmed(conn)
    rows = conn.execute("SELECT petals, valid_from, evidence_statement_ids FROM model_elements").fetchall()
    import json

    elements = [
        (json.loads(row["petals"]), date.fromisoformat(row["valid_from"]), json.loads(row["evidence_statement_ids"]))
        for row in rows
    ]
    result = []
    for day in days:
        counts = dict.fromkeys(Petal, 0)
        for petals, valid_from, evidence in elements:
            if valid_from > day:
                continue
            if any(number in confirmed and confirmed[number] <= day for number in evidence):
                for petal in petals:
                    counts[Petal(petal)] += 1
        result.append([_level(counts[petal]) for petal in Petal])
    return result


@router.get("/appearance")
def appearance(
    request: Request, _: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[str]]:
    """今のツインの色を返す。花びらの名前は返さない。"""
    return {"levels": levels_by_day(conn, [clock.now(request.app).date()])[0]}


@router.get("/growth")
def growth(
    request: Request, _: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """1年ごとの色を返す。"""
    today = clock.now(request.app).date()
    row = conn.execute("SELECT MIN(conversation_date) AS first FROM conversations").fetchone()
    first_year = date.fromisoformat(row["first"]).year if row and row["first"] else today.year
    days = [date(year, 12, 31) for year in range(first_year, today.year)] + [today]
    return {
        "items": [
            {"year": day.year, "levels": levels}
            for day, levels in zip(days, levels_by_day(conn, days), strict=True)
        ]
    }
