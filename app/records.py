"""記録を返す API。

役割：言明と便りを、ログイン中の人に見える分だけ返す。閲覧の記録も残す。
4つの極：2. 分かち合う、4. 10年後に代弁する。
守る原則：2（今の様子 → 過去の本人の言葉 → AIの推測の順に並べる）、
          3（出どころをそのまま返す）、10（絞り込みはサーバーで行う）。

応答には一覧そのものだけを入れ、合計や件数の項目を持たせない。
絞り込みの決まりは app/consent.py、API の取り決めは docs/api.md にある。

関連するテスト：tests/test_consent.py、tests/test_api.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path

from app import consent, db
from app.auth import current_user, get_conn
from app.schemas import Letter, Source, Statement, User

router = APIRouter(prefix="/api")

# 並べる順番（原則2）。証言は本人の言葉のあと、AIの推測の前に置く。
_DISPLAY_RANK = {
    Source.OBSERVATION: 0,
    Source.SELF_WORDS: 1,
    Source.SELF_CORRECTION: 1,
    Source.FAMILY_TESTIMONY: 2,
    Source.CONNECTED_TESTIMONY: 2,
    Source.AI_GUESS: 3,
}


def order_for_display(statements: list[Statement]) -> list[Statement]:
    """原則2の順に並べる。同じ順位の中では、有効になった日が新しいものを先にする。

    番号は乱数で順番を表さないので使わない。同じ日の行は、並べ替えが元の順を保つので、
    受け取った順（台帳の、作成日時と追記の順）のままになる。
    """
    newest_first = sorted(statements, key=lambda item: item.effective_date, reverse=True)
    return sorted(newest_first, key=lambda item: _DISPLAY_RANK[item.source])


@router.get("/statements")
def list_statements(
    viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """見える、いま有効な言明の一覧。"""
    items = order_for_display(consent.visible_statements(conn, viewer))
    db.log_access(conn, viewer.id, "言明の一覧")
    return {"items": [consent.statement_for_viewer(viewer, item) for item in items]}


@router.get("/statements/{statement_id}")
def read_statement(
    statement_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """番号で開く。見えない行と存在しない行は、同じ 404 を返す。"""
    statement = consent.visible_statement(conn, viewer, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="見つかりません")
    db.log_access(conn, viewer.id, f"言明 {statement_id}")
    return consent.statement_for_viewer(viewer, statement)


@router.get("/letters")
def list_letters(
    viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[Letter]]:
    """見える便りの一覧。"""
    items = consent.visible_letters(conn, viewer)
    db.log_access(conn, viewer.id, "便りの一覧")
    return {"items": items}
