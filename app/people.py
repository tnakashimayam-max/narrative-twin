"""人の登録と管理。

役割：家族と知人の登録、立場の変更、はずすことを扱う。
4つの極：2. 分かち合う。
守る原則：10（託す人と見せる範囲は本人が決める）。

登録、立場の変更、はずすことができるのは本人だけで、サーバーで確かめる。各人は自分の名前、
呼ばれ方、写真を直せる。消さずに「はずす」にするのは、過去の発言が記録として残るため。
はずした人はログインできず、部屋の人でもなくなる。
立場を変えると見える範囲が変わるので、変える前に言葉で確かめられるようにしている。

関連するテスト：tests/test_people.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app import clock, consent, db, ledger, photos
from app.auth import (
    NOT_FOUND,
    can_log_in,
    current_user,
    get_conn,
    person_detail,
    person_view,
    professionals_open,
    require_self,
)
from app.schemas import Gender, PhotoKind, Residence, Role, ShareScope, User

router = APIRouter(prefix="/api/people")

NAME_NOT_USABLE = "その名前は使えません。別の名前にしてください。"

_CHOOSABLE_ROLES = {Role.FAMILY, Role.CONNECTED, Role.PROFESSIONAL}


class PersonDraft(BaseModel):
    """人の登録の入力。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    called_name: str | None = Field(default=None, max_length=40)
    role: Role
    relation: str | None = Field(default=None, max_length=40)
    residence: Residence | None = None
    birth_year: int | None = Field(default=None, ge=1880, le=2100)
    gender: Gender | None = None
    is_deceased: bool = False
    memo: str | None = Field(default=None, max_length=2000)
    photo_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)


class PersonChange(BaseModel):
    """人の変更の入力。送られた項目だけを変える。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=40)
    called_name: str | None = Field(default=None, max_length=40)
    role: Role | None = None
    relation: str | None = Field(default=None, max_length=40)
    residence: Residence | None = None
    birth_year: int | None = Field(default=None, ge=1880, le=2100)
    gender: Gender | None = None
    is_deceased: bool | None = None
    memo: str | None = Field(default=None, max_length=2000)
    photo_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)


# 本人以外が自分について直せる項目
_OWN_FIELDS = {"name", "called_name", "photo_id"}


def check_profile_photo(conn: sqlite3.Connection, operator: User, photo_id: int | None) -> None:
    """プロフィールに指定できる写真を確かめる。

    指定できるのは、操作する人が送ったプロフィールの種類の写真だけ。プロフィール写真はログイン中の
    全員に見えるので、番号を指すだけで、便りや部屋の写真を全員に見せられてしまうのを防ぐ。
    本人は家族の写真も登録できるので、持ち主は「対象の人」ではなく「操作する人」で確かめる。
    関連するテスト：tests/test_photos.py
    """
    if photo_id is None:
        return
    photo = photos.get_photo(conn, photo_id)
    if photo is None or photo.kind is not PhotoKind.PROFILE or photo.owner_user_id != operator.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def check_names(conn: sqlite3.Connection, person_id: int | None, *names: str | None) -> None:
    """名前と呼ばれ方を確かめる。

    ツインの呼び名と、ほかの人の名前や呼ばれ方は使えない。吹き出しと AI への入力で、本人やツインに
    なりすませないようにするため。はずした人と亡くなった方の名前とも重ならないようにする。
    理由を分けないので、断られたことから一覧に出ない人の有無を読むことは完全には防げない。
    決めた経緯：docs/decisions.md
    """
    wanted = {name.strip() for name in names if name and name.strip()}
    for name in wanted:
        if "ツイン" in name:
            raise HTTPException(status_code=400, detail=NAME_NOT_USABLE)
    rows = conn.execute("SELECT id, name, called_name FROM users").fetchall()
    for row in rows:
        if row["id"] != person_id and wanted & {row["name"], row["called_name"]}:
            raise HTTPException(status_code=400, detail=NAME_NOT_USABLE)


class ProfessionalInvite(BaseModel):
    """専門職を招く入力。立場は受け取らず、専門職に固定する。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    called_name: str | None = Field(default=None, max_length=40)
    relation: str | None = Field(default=None, max_length=40)


def add_person(conn: sqlite3.Connection, draft: PersonDraft) -> User:
    """人を登録する。番号は乱数にする。通し番号だと、はずした人の数が分かるため。"""
    check_names(conn, None, draft.name, draft.called_name)
    if draft.role not in _CHOOSABLE_ROLES:
        raise HTTPException(status_code=400, detail="本人は1人だけです。立場を選び直してください。")
    name = draft.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="お名前を入れてください。")
    values = draft.model_dump(mode="json")
    values.update(
        id=db.new_id(conn, "users"),
        name=name,
        called_name=(draft.called_name or "").strip() or name,
        is_deceased=int(draft.is_deceased),
    )
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    try:
        with ledger.atomic(conn):
            conn.execute(f"INSERT INTO users ({columns}) VALUES ({marks})", list(values.values()))
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=400, detail=NAME_NOT_USABLE) from error
    return db.get_user(conn, values["id"])


@router.get("")
def list_people(
    _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """はずしていない人の一覧。本人だけが開ける。"""
    return {"items": [person_detail(user) for user in db.list_users(conn)]}


@router.get("/choices")
def list_choices(
    request: Request, viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """部屋の相手を選ぶ一覧。ログインできる人の名前だけを返し、生まれた年やメモは返さない。"""
    people = [user for user in db.login_users(conn) if user.id != viewer.id and can_log_in(user, request.app)]
    return {"items": [person_view(user) for user in people]}


@router.post("/professional", status_code=201)
def invite_professional(
    body: ProfessionalInvite,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """専門職を招く。人の登録と託す設定を1つの処理で入れる。

    招かれた人に見えるのは、本人が「意思決定支援用」にした記録だけ。招いた人が見える範囲を広げることは
    できない（原則10）。関連するテスト：tests/test_room_access.py
    """
    if not (consent.can_act_for_self(conn, viewer) and professionals_open(request.app)):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    draft = PersonDraft(name=body.name, called_name=body.called_name, role=Role.PROFESSIONAL, relation=body.relation)
    with ledger.atomic(conn):
        person = add_person(conn, draft)
        conn.execute(
            "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (?, ?, ?)",
            (person.id, ShareScope.DECISION_SUPPORT.value, clock.now(request.app).date().isoformat()),
        )
    return person_view(person)


@router.post("", status_code=201)
def register_person(
    draft: PersonDraft, viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """人を登録する。本人だけができる。"""
    check_profile_photo(conn, viewer, draft.photo_id)
    return person_detail(add_person(conn, draft))


@router.put("/{person_id}")
def change_person(
    change: PersonChange,
    person_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """人を直す。本人はすべての項目を、本人以外は自分の名前、呼ばれ方、写真だけを直せる。"""
    target = db.get_user(conn, person_id)
    is_self_viewer = viewer.role is Role.SELF
    if target is None or target.removed_at or (not is_self_viewer and target.id != viewer.id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    fields = change.model_dump(mode="json", exclude_unset=True)
    if not is_self_viewer and set(fields) - _OWN_FIELDS:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if "role" in fields and (target.role is Role.SELF or change.role not in _CHOOSABLE_ROLES):
        raise HTTPException(status_code=400, detail="この立場には変えられません。")
    if target.role is Role.SELF and fields.get("is_deceased"):
        raise HTTPException(status_code=400, detail="本人を亡くなった方にはできません。")
    if "photo_id" in fields:
        check_profile_photo(conn, viewer, fields["photo_id"])
    check_names(conn, person_id, fields.get("name"), fields.get("called_name"))
    if "name" in fields:
        fields["name"] = (fields["name"] or "").strip()
        if not fields["name"]:
            raise HTTPException(status_code=400, detail="お名前を入れてください。")
    if "called_name" in fields:
        fields["called_name"] = (fields["called_name"] or "").strip() or fields.get("name") or target.name
    if "is_deceased" in fields:
        fields["is_deceased"] = int(bool(fields["is_deceased"]))
    if fields:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        try:
            with conn:
                conn.execute(
                    f"UPDATE users SET {assignments} WHERE id = ?", [*fields.values(), person_id]
                )
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=400, detail=NAME_NOT_USABLE) from error
    updated = db.get_user(conn, person_id)
    return person_detail(updated) if is_self_viewer else person_view(updated)


@router.get("/{person_id}/role-preview")
def role_preview(
    person_id: int = Path(ge=1, le=db.MAX_ID),
    role: Role = Query(),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, list[str]]:
    """立場を変えると見えるようになるものと、見えなくなるものを言葉で返す。"""
    target = db.get_user(conn, person_id)
    if target is None or target.removed_at:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return consent.role_change_preview(conn, target, role)


@router.post("/{person_id}/remove", status_code=204)
def remove_person(
    request: Request,
    person_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """はずす。消さない。過去の発言は残る。ログインの一覧から消え、部屋の人でもなくなる。"""
    target = db.get_user(conn, person_id)
    if target is None or target.removed_at:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if target.role is Role.SELF:
        raise HTTPException(status_code=400, detail="本人ははずせません。")
    moment = clock.now(request.app).isoformat()
    with conn:
        conn.execute("UPDATE users SET removed_at = ? WHERE id = ?", (moment, person_id))
        conn.execute(
            "UPDATE room_members SET left_at = ? WHERE user_id = ? AND left_at IS NULL",
            (moment, person_id),
        )
