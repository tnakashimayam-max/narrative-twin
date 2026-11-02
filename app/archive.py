"""これまでの記録（保管）。

役割：本人が、日付ごとに会話、写真、便りを読み返せるようにする。
4つの極：1. 書く・話す。
守る原則：5（元の記録を消さない）、10（本人だけが開ける）。

関連するテスト：tests/test_ecomap.py
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query

from app import db, room_store
from app.auth import get_conn, require_self
from app.schemas import Letter, RoomKind, User

router = APIRouter(prefix="/api")

_DAYS_PER_PAGE = 30


@router.get("/archive")
def read_archive(
    before: date | None = Query(default=None),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """日付ごとの会話と便りを、新しい順に返す。"""
    limit_day = before.isoformat() if before else "9999-12-31"
    talk_days = conn.execute(
        """
        SELECT c.id, c.conversation_date AS day FROM conversations c
        JOIN rooms r ON r.id = c.room_id
        WHERE r.kind = ? AND c.conversation_date < ?
        ORDER BY c.conversation_date DESC, c.rowid
        """,
        (RoomKind.TWIN.value, limit_day),
    ).fetchall()
    letter_rows = conn.execute(
        "SELECT * FROM letters WHERE substr(shared_at, 1, 10) < ? ORDER BY shared_at DESC, rowid DESC",
        (limit_day,),
    ).fetchall()
    days: dict[str, dict] = {}
    for row in talk_days:
        items = room_store.conversation_messages(conn, row["id"], viewer)
        if items:
            days.setdefault(row["day"], {"date": row["day"], "conversations": [], "letters": []})[
                "conversations"
            ].append({"id": row["id"], "items": items})
    for row in letter_rows:
        letter = db.row_to_model(row, Letter)
        day = letter.shared_at.date().isoformat()
        days.setdefault(day, {"date": day, "conversations": [], "letters": []})["letters"].append(
            letter.model_dump(mode="json")
        )
    ordered = [days[day] for day in sorted(days, reverse=True)]
    page = ordered[:_DAYS_PER_PAGE]
    return {
        "days": page,
        "next_before": page[-1]["date"] if len(ordered) > _DAYS_PER_PAGE else None,
    }
