"""ツイン（AI）が作った書類の削除。

役割：手紙、1枚の紙、ケアプラン案、生活まとめプランに削除の印を付け、印のある書類を画面と API から外す。
4つの極：4. 10年後に代弁する。
守る原則：1（消すかは人が決める。書類を頼んだ専門職が消す）、5（書類から台帳に入った言葉は消さない）。

決まり（tyumajinさんの依頼「Twinで作った書類は削除する機能もつけてほしい」、2026-11-06）
- 消せるのは、その書類を頼んだ専門職だけ。ほかの人の書類は、あることも見せないので 404 を返す。
- 書類の行は消さず、削除の印（deleted_documents）を足す。印も追記だけ。書類を作った AI の呼び出しの記録
  （どのモデルで作ったか）を残すためと、台帳を足すだけにする決まりの例外を増やさないため。
- 手紙を確かめて直した言葉や、残すと決めた伝えは、書類と別に台帳に入っているので、消しても残る。
- 確かめ待ちの手紙を消すと、本人と託された家族の確かめ待ちからも消える。

関連するテスト：tests/test_made_documents.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app import clock, db, ledger
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import MadeDocumentKind, User

router = APIRouter(prefix="/api/advocacy")

# 画面から呼ぶ名前と、書類の表と、頼んだ人の列。表と列はここで決めた文字だけを SQL に入れる
_KINDS: dict[str, tuple[MadeDocumentKind, str, str]] = {
    "letter": (MadeDocumentKind.LETTER, "advocacy_letters", "requester_user_id"),
    "sheet": (MadeDocumentKind.SHEET, "advocacy_sheets", "asker_user_id"),
    "plan": (MadeDocumentKind.PLAN, "advocacy_plans", "asker_user_id"),
    "life_plan": (MadeDocumentKind.LIFE_PLAN, "life_plans", "requester_user_id"),
}


def not_deleted(kind: MadeDocumentKind, column: str = "id") -> str:
    """書類を読む SQL に足す条件。削除の印のある書類を外す。kind は決めた言葉なので、そのまま入れてよい。"""
    return f"{column} NOT IN (SELECT document_id FROM deleted_documents WHERE kind = '{kind.value}')"


def is_deleted(conn: sqlite3.Connection, kind: MadeDocumentKind, document_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM deleted_documents WHERE kind = ? AND document_id = ?", (kind.value, document_id)
    ).fetchone() is not None


@router.delete("/made/{name}/{document_id}", status_code=204)
def delete_document(
    request: Request,
    name: str = Path(pattern="^(letter|sheet|plan|life_plan)$"),
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """ツインが作った書類に削除の印を付ける。頼んだ専門職だけが消せる。2回押しても印は1つ。"""
    kind, table, owner = _KINDS[name]
    row = conn.execute(f"SELECT {owner} FROM {table} WHERE id = ?", (document_id,)).fetchone()
    if row is None or row[0] != viewer.id or is_deleted(conn, kind, document_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "deleted_documents",
            {
                "kind": kind.value,
                "document_id": document_id,
                "deleted_by_user_id": viewer.id,
                "deleted_at": clock.now(request.app).isoformat(),
            },
        )
