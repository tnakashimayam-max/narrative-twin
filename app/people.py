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

from app import clock, consent, db
from app.auth import NOT_FOUND, current_user, get_conn, person_detail, person_view, require_self
from app.schemas import Gender, Residence, Role, User

router = APIRouter(prefix="/api/people")

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


def add_person(conn: sqlite3.Connection, draft: PersonDraft) -> User:
    """人を登録する。番号は乱数にする。通し番号だと、はずした人の数が分かるため。"""
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
        with conn:
            conn.execute(f"INSERT INTO users ({columns}) VALUES ({marks})", list(values.values()))
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail=f"「{name}」はもう登録されています。") from error
    return db.get_user(conn, values["id"])


@router.get("")
def list_people(
    _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """はずしていない人の一覧。本人だけが開ける。"""
    return {"items": [person_detail(user) for user in db.list_users(conn)]}


@router.post("", status_code=201)
def register_person(
    draft: PersonDraft, _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """人を登録する。本人だけができる。"""
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
            raise HTTPException(status_code=409, detail="その名前はもう登録されています。") from error
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
    return consent.role_change_preview(target.role, role)


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
