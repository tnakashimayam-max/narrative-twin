"""デモ用の簡易ログイン。

役割：名前を選ぶだけのログインと、ログイン中の人の取り出し。
4つの極：特定の極に属さない土台。
守る原則：8（利用者は架空の4人）、10（役割はサーバーが利用者の表から引く）。

これは本物の認証ではない。パスワードがなく、だれでも4人のだれにでもなれる。
見せてよい記録の絞り込みを確かめるためのデモの仕組みで、架空データだけを扱う。
役割はブラウザから受け取らず、覚えている番号で利用者の表から毎回引く。

関連するテスト：tests/test_api.py、tests/test_consent.py
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import db
from app.schemas import User

COOKIE_NAME = "nt_user_id"

router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    user_id: int = Field(ge=1, le=db.MAX_ID)


def _user_id_from_cookie(raw: str) -> int | None:
    """覚えている番号を読む。SQLite に渡せない長さの値は、だれでもない扱いにする。"""
    if raw.isascii() and raw.isdigit() and len(raw) <= 18:
        return int(raw)
    return None


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """1回の要求の間だけ使う接続を渡す。"""
    connection = db.connect(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> User:
    """ログイン中の人を返す。わからなければ 401 にする。"""
    user_id = _user_id_from_cookie(request.cookies.get(COOKIE_NAME, ""))
    user = db.get_user(conn, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="名前を選んでください")
    return user


@router.get("/mode")
def mode(request: Request) -> dict[str, object]:
    """起動の種類を返す。画面の隅の表示に使う。"""
    deployment = request.app.state.deployment
    return {"mine": deployment.mine, "label": deployment.label}


@router.get("/users")
def list_users(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, list[User]]:
    """ログイン画面に出す名前の一覧。"""
    return {"items": db.list_users(conn)}


@router.post("/login")
def login(
    body: LoginRequest, response: Response, conn: sqlite3.Connection = Depends(get_conn)
) -> User:
    """選ばれた名前の人としてログインする。"""
    user = db.get_user(conn, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="見つかりません")
    # 画面の JavaScript からは読めない形で覚える
    response.set_cookie(COOKIE_NAME, str(user.id), httponly=True, samesite="lax")
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """ログインをやめる。"""
    response.delete_cookie(COOKIE_NAME)


@router.get("/me")
def me(user: User = Depends(current_user)) -> User:
    """ログイン中の人を返す。"""
    return user
