"""これまでの記録（保管）。

役割：本人が、日付ごとに会話、写真、便りを読み返せるようにする。月の一覧から選んで、その月を読むこともできる。
4つの極：1. 書く・話す。
守る原則：5（元の記録を消さない）、10（本人だけが開ける）。

読む量を増やさないため、先に日付だけを軽く選び、選んだ日の会話だけを読む。
10年分の会話を全部読んでから30日分を切り出していたときは、1回に3分以上かかった（2026-11-06 の実測）。

関連するテスト：tests/test_ecomap.py
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app import db, room_store
from app.auth import get_conn, require_self
from app.schemas import Letter, RoomKind, User

router = APIRouter(prefix="/api")

_DAYS_PER_PAGE = 30

# ツインとの部屋の、発言のある会話の日。発言のない会話は読み返すものがないので、日にも数えない
_TALK_DAYS = """
    SELECT DISTINCT c.conversation_date AS day FROM conversations c
    JOIN rooms r ON r.id = c.room_id
    WHERE r.kind = ? AND c.conversation_date >= ? AND c.conversation_date < ?
      AND EXISTS (SELECT 1 FROM room_messages m WHERE m.conversation_id = c.id)
"""
_LETTER_DAYS = "SELECT DISTINCT substr(shared_at, 1, 10) AS day FROM letters WHERE substr(shared_at, 1, 10) >= ? AND substr(shared_at, 1, 10) < ?"


def _days_between(conn: sqlite3.Connection, start: str, end: str, limit: int | None = None) -> list[str]:
    """記録のある日を、新しい順に返す。start は含み、end は含まない。"""
    sql = f"SELECT day FROM ({_TALK_DAYS} UNION {_LETTER_DAYS}) ORDER BY day DESC"
    params: list = [RoomKind.TWIN.value, start, end, start, end]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [row["day"] for row in conn.execute(sql, params)]


def _days_view(conn: sqlite3.Connection, days: list[str], viewer: User) -> list[dict]:
    """選んだ日の会話と便りだけを読んで、日ごとにまとめる。"""
    if not days:
        return []
    marks = ", ".join("?" for _ in days)
    views: dict[str, dict] = {day: {"date": day, "conversations": [], "letters": []} for day in days}
    talks = conn.execute(
        f"""
        SELECT c.id, c.conversation_date AS day FROM conversations c
        JOIN rooms r ON r.id = c.room_id
        WHERE r.kind = ? AND c.conversation_date IN ({marks})
        ORDER BY c.conversation_date DESC, c.rowid
        """,
        (RoomKind.TWIN.value, *days),
    ).fetchall()
    for row in talks:
        items = room_store.conversation_messages(conn, row["id"], viewer)
        if items:
            views[row["day"]]["conversations"].append({"id": row["id"], "items": items})
    letters = conn.execute(
        f"SELECT * FROM letters WHERE substr(shared_at, 1, 10) IN ({marks}) ORDER BY shared_at DESC, rowid DESC", days
    ).fetchall()
    for row in letters:
        letter = db.row_to_model(row, Letter)
        # 日は選ぶときと同じく、記録の文字の先頭10字で決める
        views[row["shared_at"][:10]]["letters"].append(letter.model_dump(mode="json"))
    return [views[day] for day in days]


def _month_range(month: str) -> tuple[str, str]:
    """YYYY-MM の月の初日と、次の月の初日を返す。"""
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError as error:
        raise HTTPException(status_code=400, detail="月は 2036-05 の形で指定してください。") from error
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first.isoformat(), following.isoformat()


@router.get("/archive")
def read_archive(
    before: date | None = Query(default=None),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """日付ごとの会話と便りを、新しい順に返す。

    month を渡すと、その月の日をすべて返す（月の一覧から選んだとき）。渡さなければ before より前の30日分を返す。
    関連するテスト：tests/test_ecomap.py の test_これまでの記録は月を選んでその月の日をすべて読める
    """
    if month:
        start, end = _month_range(month)
        return {"days": _days_view(conn, _days_between(conn, start, end), viewer), "next_before": None}
    limit_day = before.isoformat() if before else "9999-12-31"
    # 1日多く選び、次のページがあるかを確かめる
    chosen = _days_between(conn, "0000-01-01", limit_day, _DAYS_PER_PAGE + 1)
    page = chosen[:_DAYS_PER_PAGE]
    return {
        "days": _days_view(conn, page, viewer),
        "next_before": page[-1] if len(chosen) > _DAYS_PER_PAGE else None,
    }


@router.get("/archive/months")
def read_archive_months(viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """記録のある月を、新しい順に返す。画面の年月の選び場所に使う。"""
    months: list[str] = []
    for day in _days_between(conn, "0000-01-01", "9999-12-31"):
        if not months or months[-1] != day[:7]:
            months.append(day[:7])
    return {"months": months}
