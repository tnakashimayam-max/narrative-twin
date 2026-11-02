"""写真の保存と、写真を返すときの確かめ（決まった手順で動く層）。

役割：写真を整えて保存し、見てよい人にだけ返す。AI は呼ばない。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：8（提出物とデモの写真は架空）、10（見せる範囲はサーバーで確かめる）。

ファイル名は乱数で、静的ファイルとしては出さない。この道だけで返す。
見てよいかは保存した印ではなく参照元から毎回求める。部屋の人や便りの範囲が変わっても、
食い違わないようにするため。写真を返す道は中身だけを返す。種類、持ち主、説明を返さないので、
便りに会話の写真を載せても、会話の情報は漏れない。

関連するテスト：tests/test_photos.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path as FilePath

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from app import clock, consent, db, images
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import Photo, PhotoKind, Role, User

router = APIRouter(prefix="/api/photos")

# 本人だけが送れる種類。会話はツインとの部屋、便りはタイムラインへ出すもので、どちらも本人だけが使う。
_SELF_ONLY_KINDS = {PhotoKind.TALK, PhotoKind.LETTER}


def get_photo(conn: sqlite3.Connection, photo_id: int) -> Photo | None:
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return db.row_to_model(row, Photo) if row else None


def store_photo(
    conn: sqlite3.Connection,
    photos_dir: FilePath,
    raw: bytes,
    *,
    owner_user_id: int,
    kind: PhotoKind,
    moment: str,
) -> Photo:
    """写真を整えて乱数の名前で保存し、photos の表に入れる。"""
    data = images.normalize(raw)
    photos_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{secrets.token_hex(16)}.jpg"
    (photos_dir / file_name).write_bytes(data)
    with conn:
        photo_id = db.insert_row(
            conn,
            "photos",
            {"file": file_name, "owner_user_id": owner_user_id, "kind": kind.value, "created_at": moment},
        )
    return get_photo(conn, photo_id)


def read_photo_bytes(photos_dir: FilePath, photo: Photo) -> bytes:
    return (photos_dir / photo.file).read_bytes()


def can_see_photo(conn: sqlite3.Connection, viewer: User, photo: Photo) -> bool:
    """この人がこの写真を見てよいかを、参照元から求める。"""
    if photo.owner_user_id == viewer.id:
        return True
    # 全員に見せるのはプロフィールの種類の写真だけ。番号を指しただけでは見えるようにしない。
    if photo.kind is PhotoKind.PROFILE:
        profile = conn.execute(
            "SELECT 1 FROM users WHERE photo_id = ? AND removed_at IS NULL LIMIT 1", (photo.id,)
        ).fetchone()
        if profile:
            return True
    in_room = conn.execute(
        """
        SELECT 1 FROM room_messages m
        JOIN room_members r ON r.room_id = m.room_id
        WHERE m.photo_id = ? AND r.user_id = ? AND r.left_at IS NULL
        LIMIT 1
        """,
        (photo.id, viewer.id),
    ).fetchone()
    if in_room:
        return True
    scopes = [scope.value for scope in consent.visible_scopes(conn, viewer)]
    if not scopes:
        return False
    marks = ", ".join("?" for _ in scopes)
    in_letter = conn.execute(
        f"SELECT 1 FROM letters WHERE photo_id = ? AND share_with IN ({marks}) LIMIT 1",
        [photo.id, *scopes],
    ).fetchone()
    return in_letter is not None


@router.post("", status_code=201)
async def upload_photo(
    request: Request,
    kind: PhotoKind = Query(),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, int]:
    """写真を受け取って保存する。本文に写真そのものを送る。"""
    if kind in _SELF_ONLY_KINDS and viewer.role is not Role.SELF:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    raw = await request.body()
    try:
        photo = store_photo(
            conn,
            request.app.state.deployment.photos_dir,
            raw,
            owner_user_id=viewer.id,
            kind=kind,
            moment=clock.now(request.app).isoformat(),
        )
    except images.PhotoError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": photo.id}


@router.get("/{photo_id}")
def read_photo(
    request: Request,
    photo_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    """写真の中身を返す。見てよくない写真と、ない写真は同じ 404 にする。"""
    photo = get_photo(conn, photo_id)
    if photo is None or not can_see_photo(conn, viewer, photo):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    data = read_photo_bytes(request.app.state.deployment.photos_dir, photo)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
