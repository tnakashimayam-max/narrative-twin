"""便りとタイムライン。

役割：便りの下書きを作り、本人が確かめてから共有し、見える人にスレッドつきで返す。
4つの極：2. 分かち合う。
守る原則：3（コメントは役割に合わせた証言にする）、10（見える範囲はサーバーで絞る）、
          14（いいねの数、通知、既読、おすすめ表示は作らない）。

本人が内容を直し、相手を選んで「この内容で共有する」を押すまで、タイムラインに出さない。
下書きは保存しない。便りの相手を選ぶことと、言明の共有範囲を広げることは別のことで、
言明は「本人だけ」のまま変えない。

関連するテスト：tests/test_timeline.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import ai, clock, config, consent, db, jobs, ledger, memory, photos, prompts, twin
from app.auth import NOT_FOUND, current_user, get_conn, person_view, require_self
from app.schemas import Comment, CommentDraft, Letter, LetterDraft, ShareScope, User

router = APIRouter(prefix="/api")

AI_FAILED_MESSAGE = "いま、下書きをうまく作れませんでした。少し時間をおいて、もう一度お試しください。"
_AI_ERRORS = (
    config.MissingApiKeyError,
    ai.AIResultError,
    ai.AIRefusedError,
    ai.AIUnavailableError,
)
_SHARE_CHOICES = {ShareScope.FAMILY, ShareScope.CONNECTED}


class LetterInput(BaseModel):
    """「この内容で共有する」の入力。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    body: str = Field(min_length=1, max_length=500)
    photo_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    share_with: ShareScope


class CommentInput(BaseModel):
    """コメントの入力。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    client_key: str = Field(min_length=1, max_length=64)


def _comment_view(row: sqlite3.Row) -> dict:
    """コメントを、トークの吹き出しと同じ形にする。"""
    return {
        "id": row["id"],
        "seq": row["rowid"],
        "kind": "発言",
        "sender_user_id": row["author_user_id"],
        "is_twin": bool(row["is_twin_question"]),
        "text": row["body"],
        "photo_id": None,
        "question_id": None,
        "question_rested": False,
        "sent_at": row["commented_at"],
        "client_key": None,
    }


def _thread(conn: sqlite3.Connection, letter_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT rowid, * FROM comments WHERE letter_id = ? ORDER BY rowid", (letter_id,)
    ).fetchall()
    return [_comment_view(row) for row in rows]


def _letter_view(conn: sqlite3.Connection, letter: Letter) -> dict:
    return {**letter.model_dump(mode="json"), "thread": _thread(conn, letter.id)}


def _conversation_of_twin_room(conn: sqlite3.Connection, conversation_id: int) -> None:
    row = conn.execute(
        """
        SELECT 1 FROM conversations c JOIN rooms r ON r.id = c.room_id
        WHERE c.id = ? AND r.kind = 'ツイン'
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


@router.post("/conversations/{conversation_id}/letter-draft")
def make_letter_draft(
    conversation_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """便りの下書きを作る。保存しない。本人が直して共有するまで、だれにも見えない。"""
    _conversation_of_twin_room(conn, conversation_id)
    lines = [
        line
        for line in memory.conversation_lines(conn, conversation_id, viewer)
        if line.startswith(f"{viewer.display_name}：")
    ]
    photo_ids = [
        row["photo_id"]
        for row in conn.execute(
            "SELECT photo_id FROM room_messages WHERE conversation_id = ? AND photo_id IS NOT NULL ORDER BY rowid",
            (conversation_id,),
        )
    ]
    if not lines:
        raise HTTPException(status_code=400, detail="この会話には、便りにできる言葉がありません。")
    target = f"会話 {conversation_id}"
    try:
        result = ai.letter_draft(
            system=prompts.letter_draft_system(viewer.display_name), text=prompts.letter_user_text(lines)
        )
    except _AI_ERRORS as error:
        if isinstance(error, twin._SPENT_ERRORS):
            twin._log_spent(conn, "便りの下書き", error, target)
        raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
    twin._log_result(conn, "便りの下書き", result, target)
    return {"body": result.value.body, "photo_ids": photo_ids}


@router.post("/letters", status_code=201)
def share_letter(
    body: LetterInput,
    request: Request,
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """本人が確かめた便りを共有する。ここで初めてタイムラインに出る。"""
    if body.share_with not in _SHARE_CHOICES:
        raise HTTPException(status_code=400, detail="相手は、家族か縁のある人までから選んでください。")
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="便りの本文を入れてください。")
    if body.conversation_id is not None:
        _conversation_of_twin_room(conn, body.conversation_id)
    if body.photo_id is not None:
        photo = photos.get_photo(conn, body.photo_id)
        if photo is None or photo.owner_user_id != viewer.id:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    letter = ledger.add_letter(
        conn,
        LetterDraft(
            conversation_id=body.conversation_id,
            body=text,
            photo_id=body.photo_id,
            share_with=body.share_with,
            shared_at=clock.now(request.app),
        ),
    )
    return _letter_view(conn, letter)


@router.get("/timeline")
def read_timeline(
    viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """見える便りを新しい順に、スレッドつきで返す。絞り込みはサーバーで行う。"""
    letters = consent.visible_letters(conn, viewer)
    items = [_letter_view(conn, letter) for letter in letters]
    people: dict[int, User] = {}
    self_user = db.self_user(conn)
    if self_user:
        people[self_user.id] = self_user
    for item in items:
        for comment in item["thread"]:
            user_id = comment["sender_user_id"]
            if user_id and user_id not in people:
                people[user_id] = db.get_user(conn, user_id)
    return {
        "items": items,
        "people": [person_view(person) for person in people.values()],
        "self_id": self_user.id if self_user else None,
    }


@router.post("/letters/{letter_id}/comments", status_code=201)
def add_comment(
    body: CommentInput,
    request: Request,
    background: BackgroundTasks,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """コメントする。便りを見られない人には、ない便りと同じ 404 にする。"""
    if consent.visible_letter(conn, viewer, letter_id) is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="コメントを入れてください。")
    existing = conn.execute(
        "SELECT rowid, * FROM comments WHERE letter_id = ? AND author_user_id = ? AND client_key = ?",
        (letter_id, viewer.id, body.client_key),
    ).fetchone()
    if existing is not None:
        return _comment_view(existing)
    comment: Comment = ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter_id,
            author_user_id=viewer.id,
            body=text,
            commented_at=clock.now(request.app),
            client_key=body.client_key,
        ),
    )
    background.add_task(jobs.after_comment, request.app, letter_id, viewer.id)
    row = conn.execute("SELECT rowid, * FROM comments WHERE id = ?", (comment.id,)).fetchone()
    return _comment_view(row)
