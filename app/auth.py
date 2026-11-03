"""名前を押すだけの簡易ログイン。

役割：名前を選ぶログインと、ログイン中の人の取り出し。本人の最初の登録。
4つの極：特定の極に属さない土台。
守る原則：8（提出物とデモは架空）、10（立場はサーバーが人の表から引く）。

これは本物の認証ではない。パスワードがなく、だれでも一覧のだれにでもなれる。サーバーは
127.0.0.1 だけで待ち受ける。立場はブラウザから受け取らず、番号で人の表から毎回引く。
はずした人と亡くなった方は、ここで止める。一覧から消すだけでは、別のウィンドウで開いたままの人に
見え続けるため。

だれとして使っているかは、ヘッダーの番号で受け取る。cookie は同じブラウザのウィンドウで共有されるので、
2つのウィンドウで別の人として使えないため。

関連するテスト：tests/test_api.py、tests/test_people.py
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import clock, config, consent, db
from app.schemas import Gender, Role, ShareScope, User

USER_HEADER = "X-NT-User"
NOT_FOUND = "見つかりません"

router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    user_id: int = Field(ge=1, le=db.MAX_ID)


class SelfDraft(BaseModel):
    """本人の登録の入力。"""

    name: str = Field(min_length=1, max_length=40)
    called_name: str | None = Field(default=None, max_length=40)
    birth_year: int | None = Field(default=None, ge=1880, le=2100)
    gender: Gender | None = None


def _user_id_from_header(raw: str) -> int | None:
    """番号を読む。SQLite に渡せない長さの値は、だれでもない扱いにする。"""
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


def professionals_open(app) -> bool:
    """専門職が現れてよい日付かを返す。

    デモで10年後の場面を日付で切り替え、何度も撮り直せるようにするため。自分用の起動は日付を
    動かせないので、切り替えない。関連するテスト：tests/test_room_access.py
    """
    return app.state.deployment.mine or clock.now(app).date() >= config.PROFESSIONAL_FROM


def can_log_in(user: User | None, app) -> bool:
    """ログインできる人かを返す。はずした人と亡くなった方と、現れる前の専門職はログインできない。"""
    if user is None or user.removed_at is not None or user.is_deceased:
        return False
    return user.role is not Role.PROFESSIONAL or professionals_open(app)


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> User:
    """ログイン中の人を返す。わからなければ 401 にする。"""
    user_id = _user_id_from_header(request.headers.get(USER_HEADER, ""))
    user = db.get_user(conn, user_id) if user_id is not None else None
    if not can_log_in(user, request.app):
        raise HTTPException(status_code=401, detail="名前を選んでください")
    return user


def self_only(viewer: User) -> None:
    """本人だけが使える道。本人以外には、道がないのと同じ応答にする。"""
    if viewer.role is not Role.SELF:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def require_self(viewer: User = Depends(current_user)) -> User:
    """本人だけが使える道の入口。"""
    self_only(viewer)
    return viewer


def person_view(user: User) -> dict:
    """本人以外にも返してよい形。生まれた年やメモなどは入れない。"""
    return {
        "id": user.id,
        "name": user.name,
        "called_name": user.display_name,
        "role": user.role.value,
        "photo_id": None if user.removed_at else user.photo_id,
    }


def person_detail(user: User) -> dict:
    """本人だけに返す形。"""
    return {
        **person_view(user),
        "relation": user.relation,
        "residence": user.residence.value if user.residence else None,
        "birth_year": user.birth_year,
        "gender": user.gender.value if user.gender else None,
        "is_deceased": user.is_deceased,
        "memo": user.memo,
    }


@router.get("/mode")
def mode(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, object]:
    """起動の種類を返す。画面の隅の表示と、最初の登録の画面を出すかの判断に使う。"""
    deployment = request.app.state.deployment
    return {
        "mine": deployment.mine,
        "label": deployment.label,
        "needs_self": db.self_user(conn) is None,
    }


@router.get("/users")
def list_users(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, list[dict]]:
    """ログイン画面に出す名前の一覧。プロフィール写真はログインのあとで読む。"""
    return {
        "items": [
            {"id": user.id, "name": user.name, "called_name": user.display_name, "role": user.role.value}
            for user in db.login_users(conn)
            if can_log_in(user, request.app)
        ]
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """選ばれた人として使えるかを確かめる。覚えておくのは画面の側。"""
    user = db.get_user(conn, body.user_id)
    if not can_log_in(user, request.app):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return person_view(user)


@router.get("/me")
def me(
    request: Request, user: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """ログイン中の人を返す。画面が「ケアマネジャーを招く」を出すかを決められるようにする。"""
    can_invite = consent.can_act_for_self(conn, user) and professionals_open(request.app)
    # 支援者の画面を使えるのは、招かれた専門職と、立場が家族の託された人
    supporter = user.role in {Role.PROFESSIONAL, Role.FAMILY} and ShareScope.DECISION_SUPPORT in consent.visible_scopes(
        conn, user
    )
    return {**person_view(user), "can_invite_professional": can_invite, "can_use_supporter_screen": supporter}


@router.post("/self", status_code=201)
def register_self(body: SelfDraft, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """本人を登録する。本人がまだいないときだけ。自分用の最初の起動で使う。"""
    if db.self_user(conn) is not None:
        raise HTTPException(status_code=409, detail="本人はもう登録されています。")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="お名前を入れてください。")
    if "ツイン" in name or "ツイン" in (body.called_name or ""):
        raise HTTPException(status_code=400, detail="その名前は使えません。別の名前にしてください。")
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name, birth_year, gender) VALUES (?, ?, ?, ?, ?, ?)",
            (
                db.new_id(conn, "users"),
                name,
                Role.SELF.value,
                (body.called_name or "").strip() or name,
                body.birth_year,
                body.gender.value if body.gender else None,
            ),
        )
    return person_detail(db.self_user(conn))
