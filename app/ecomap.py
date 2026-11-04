"""つながりの地図（エコマップ）、家系図（ジェノグラム）、話に出てきた人の候補。

役割：本人のまわりの人とつながりを持ち、線の種類と親子と夫婦の関係を返す。候補を本人が決める。
4つの極：2. 分かち合う。
守る原則：1（決めるのは本人）、10（今は本人だけに見せる）。

線の強さとストレスは本人が選ぶ。AI から提案しない。人の仲を AI が決めて記録に残さないため。
話に出てきた人は、本人が「登録する」を押すまで、地図にも一覧にも足さない。

関連するテスト：tests/test_ecomap.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import clock, db, photos
from app.auth import NOT_FOUND, get_conn, person_detail, require_self
from app.people import PersonDraft, add_person
from app.schemas import (
    CandidateStatus,
    FamilyLink,
    FamilyLinkKind,
    Gender,
    LinkStrength,
    MapLink,
    MapPlace,
    PersonCandidate,
    RelativeKind,
    Role,
    SupportDirection,
    User,
)

router = APIRouter(prefix="/api")

_DECISIONS = {
    "登録する": CandidateStatus.REGISTERED,
    "ちがう": CandidateStatus.REJECTED,
    "今はいい": CandidateStatus.LATER,
}
_FAMILY_KINDS = {RelativeKind.PARENT, RelativeKind.CHILD, RelativeKind.SPOUSE, RelativeKind.SIBLING}


class PlaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    kind: str | None = Field(default=None, max_length=40)


class LinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    place_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    strength: LinkStrength
    direction: SupportDirection


class FamilyLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FamilyLinkKind
    from_user_id: int = Field(ge=1, le=db.MAX_ID)
    to_user_id: int = Field(ge=1, le=db.MAX_ID)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    role: Role | None = None


def _link_of(conn: sqlite3.Connection, column: str, value: int) -> dict | None:
    row = conn.execute(f"SELECT * FROM map_links WHERE {column} = ?", (value,)).fetchone()
    return db.row_to_model(row, MapLink).model_dump(mode="json") if row else None


@router.get("/map")
def read_map(viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """地図の中身。今は本人だけに見せる。亡くなった方は地図には出さず、家系図に出す。"""
    people = [user for user in db.list_users(conn) if user.id != viewer.id and not user.is_deceased]
    places = conn.execute("SELECT * FROM map_places WHERE removed_at IS NULL ORDER BY id").fetchall()
    return {
        "self": person_detail(viewer),
        "people": [
            {"person": person_detail(user), "link": _link_of(conn, "user_id", user.id)} for user in people
        ],
        "places": [
            {
                "place": db.row_to_model(row, MapPlace).model_dump(mode="json"),
                "link": _link_of(conn, "place_id", row["id"]),
            }
            for row in places
        ],
    }


@router.post("/map/places", status_code=201)
def add_place(
    body: PlaceInput,
    request: Request,
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """病院、デイサービス、町内会などのつながりを足す。ログインはできない。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名前を入れてください。")
    with conn:
        cursor = conn.execute(
            "INSERT INTO map_places (name, kind, created_at) VALUES (?, ?, ?)",
            (name, (body.kind or "").strip() or None, clock.now(request.app).isoformat()),
        )
    row = conn.execute("SELECT * FROM map_places WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return db.row_to_model(row, MapPlace).model_dump(mode="json")


@router.post("/map/places/{place_id}/remove", status_code=204)
def remove_place(
    request: Request,
    place_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    with conn:
        cursor = conn.execute(
            "UPDATE map_places SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
            (clock.now(request.app).isoformat(), place_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


@router.put("/map/links")
def set_link(
    body: LinkInput,
    request: Request,
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """線の種類と支援の向きを決める。本人だけが選ぶ。"""
    if (body.user_id is None) == (body.place_id is None):
        raise HTTPException(status_code=400, detail="相手を1つ選んでください。")
    if body.user_id is not None:
        target = db.get_user(conn, body.user_id)
        if target is None or target.removed_at or target.id == viewer.id:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        column, value = "user_id", body.user_id
    else:
        place = conn.execute(
            "SELECT 1 FROM map_places WHERE id = ? AND removed_at IS NULL", (body.place_id,)
        ).fetchone()
        if place is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        column, value = "place_id", body.place_id
    moment = clock.now(request.app).isoformat()
    with conn:
        cursor = conn.execute(
            f"UPDATE map_links SET strength = ?, direction = ?, updated_at = ? WHERE {column} = ?",
            (body.strength.value, body.direction.value, moment, value),
        )
        if cursor.rowcount == 0:
            conn.execute(
                f"INSERT INTO map_links ({column}, strength, direction, updated_at) VALUES (?, ?, ?, ?)",
                (value, body.strength.value, body.direction.value, moment),
            )
    return _link_of(conn, column, value)


# ---- 家系図 ----


def generations(self_id: int, links: list[FamilyLink]) -> dict[int, int]:
    """本人を 0 にして、親を -1、子を +1、夫婦を同じ世代にする。3世代の外は入れない。"""
    levels = {self_id: 0}
    queue = [self_id]
    while queue:
        current = queue.pop(0)
        for link in links:
            if link.kind is FamilyLinkKind.SPOUSE:
                pairs = [(link.from_user_id, link.to_user_id, 0), (link.to_user_id, link.from_user_id, 0)]
            else:
                pairs = [(link.from_user_id, link.to_user_id, 1), (link.to_user_id, link.from_user_id, -1)]
            for start, end, step in pairs:
                if start == current and end not in levels and -1 <= levels[current] + step <= 1:
                    levels[end] = levels[current] + step
                    queue.append(end)
    return levels


def _family_links(conn: sqlite3.Connection) -> list[FamilyLink]:
    rows = conn.execute("SELECT * FROM family_links ORDER BY id").fetchall()
    return [db.row_to_model(row, FamilyLink) for row in rows]


@router.get("/genogram")
def read_genogram(viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """家系図の中身。今は本人だけに見せる。"""
    links = _family_links(conn)
    levels = generations(viewer.id, links)
    people = []
    for user in db.list_users(conn):
        if user.id in levels:
            people.append({**person_detail(user), "generation": levels[user.id]})
    shown = {person["id"] for person in people}
    return {
        "people": people,
        "links": [
            link.model_dump(mode="json")
            for link in links
            if link.from_user_id in shown and link.to_user_id in shown
        ],
        "all_links": [link.model_dump(mode="json") for link in links],
        "self_id": viewer.id,
    }


@router.post("/family-links", status_code=201)
def add_family_link(
    body: FamilyLinkInput,
    request: Request,
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """親子か夫婦の関係を足す。親子は from が親、to が子。"""
    for user_id in (body.from_user_id, body.to_user_id):
        user = db.get_user(conn, user_id)
        if user is None or user.removed_at:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    if body.from_user_id == body.to_user_id:
        raise HTTPException(status_code=400, detail="別の人を選んでください。")
    link = _insert_family_link(conn, body.kind, body.from_user_id, body.to_user_id, clock.now(request.app).isoformat())
    return link.model_dump(mode="json")


def _insert_family_link(
    conn: sqlite3.Connection, kind: FamilyLinkKind, from_id: int, to_id: int, moment: str
) -> FamilyLink:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO family_links (from_user_id, to_user_id, kind, created_at) VALUES (?, ?, ?, ?)",
            (from_id, to_id, kind.value, moment),
        )
    row = conn.execute(
        "SELECT * FROM family_links WHERE from_user_id = ? AND to_user_id = ? AND kind = ?",
        (from_id, to_id, kind.value),
    ).fetchone()
    return db.row_to_model(row, FamilyLink)


@router.delete("/family-links/{link_id}", status_code=204)
def delete_family_link(
    link_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """関係を消す。発言と違って本人が入れた関係の印なので、間違えたら消せるようにする。"""
    with conn:
        cursor = conn.execute("DELETE FROM family_links WHERE id = ?", (link_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


# ---- 話に出てきた人の候補 ----


def _candidate_view(conn: sqlite3.Connection, candidate: PersonCandidate) -> dict:
    evidence = conn.execute(
        "SELECT text, spoken_at FROM utterances WHERE id = ?", (candidate.evidence_utterance_id,)
    ).fetchone()
    return {
        "id": candidate.id,
        "name": candidate.name,
        "relation": candidate.relation,
        "relative_kind": candidate.relative_kind.value,
        "related_to_name": candidate.related_to_name,
        "is_deceased": candidate.is_deceased,
        "gender": candidate.gender.value,
        "evidence": evidence["text"],
        "evidence_date": evidence["spoken_at"],
    }


@router.get("/people/candidates")
def list_candidates(
    _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """まだ決めていない候補の一覧。"""
    rows = conn.execute(
        "SELECT * FROM person_candidates WHERE status = ? ORDER BY id", (CandidateStatus.PENDING.value,)
    ).fetchall()
    return {"items": [_candidate_view(conn, db.row_to_model(row, PersonCandidate)) for row in rows]}


def _find_person(conn: sqlite3.Connection, name: str | None) -> User | None:
    if not name:
        return None
    for user in db.list_users(conn):
        if name in (user.name, user.display_name):
            return user
    return None


@router.post("/people/candidates/{candidate_id}/decide")
def decide_candidate(
    body: DecisionInput,
    request: Request,
    candidate_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """候補を本人が決める。「登録する」のときだけ人を足し、親子と夫婦の関係も足す。"""
    status = _DECISIONS.get(body.decision)
    if status is None:
        raise HTTPException(status_code=400, detail="登録する、ちがう、今はいい から選んでください。")
    row = conn.execute("SELECT * FROM person_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    candidate = db.row_to_model(row, PersonCandidate)
    moment = clock.now(request.app).isoformat()
    person = None
    if status is CandidateStatus.REGISTERED:
        role = body.role or (Role.FAMILY if candidate.relative_kind in _FAMILY_KINDS else Role.CONNECTED)
        person = add_person(
            conn,
            PersonDraft(
                name=candidate.name,
                role=role,
                relation=candidate.relation,
                is_deceased=candidate.is_deceased,
                gender=None if candidate.gender is Gender.UNKNOWN else candidate.gender,
            ),
        )
        anchor = _find_person(conn, candidate.related_to_name) or viewer
        if candidate.relative_kind is RelativeKind.PARENT:
            _insert_family_link(conn, FamilyLinkKind.PARENT_CHILD, person.id, anchor.id, moment)
        elif candidate.relative_kind is RelativeKind.CHILD:
            _insert_family_link(conn, FamilyLinkKind.PARENT_CHILD, anchor.id, person.id, moment)
        elif candidate.relative_kind is RelativeKind.SPOUSE:
            _insert_family_link(conn, FamilyLinkKind.SPOUSE, anchor.id, person.id, moment)
    with conn:
        conn.execute(
            "UPDATE person_candidates SET status = ?, decided_at = ? WHERE id = ?",
            (status.value, moment, candidate_id),
        )
    if person:
        person = photos.attach_demo_face(conn, request.app.state.deployment, person)
    return {"person": person_detail(person) if person else None}
