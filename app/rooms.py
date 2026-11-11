"""部屋とメッセージの API。

役割：ツインとの部屋、1対1、グループの部屋を作り、発言を保存して、部屋の人にだけ返す。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：10（部屋の発言は部屋の人だけに見せる）、12（ツインの表示）、
          15（ツインは本人がいる招かれた部屋だけを読む）。

部屋の発言と写真は部屋の人だけに見せ、ほかの人には 404 を返す。
部屋はだれでも作れる。本人がいない部屋にはツインが入れないので、本文は AI に渡らず、言明にもならない。
グループにツインが入るたびに会話を1行作り、ツインが読むのはその会話の発言だけにする。
1対1とグループには未読の数と既読を出す。通知は作らない。読んだ人の名前は返さない。
部屋を開く最初の読み込みは新しいほうの300件までにする。前の分は before で読む。
表の読み書きは app/room_store.py にある。

関連するテスト：tests/test_rooms.py、tests/test_room_access.py
全体の説明：docs/architecture.md、取り決め：docs/api.md
"""

from __future__ import annotations

import sqlite3
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app import ai, clock, config, consent, db, jobs, ledger, memory, photos, twin
from app.auth import NOT_FOUND, can_log_in, current_user, get_conn, person_view, require_self
from app.prompts import TWIN_LABEL
from app.room_store import (
    MESSAGES_PER_PAGE,
    active_presence,
    close_conversation,
    create_room,
    creator_of,
    ensure_twin_room,
    find_by_client_key,
    find_pair_room,
    get_room,
    insert_message,
    is_member,
    last_message,
    mark_read,
    members_of,
    message_view,
    messages_after,
    messages_range,
    open_conversation,
    others_read_seqs,
    position_of,
    remove_member,
    room_length,
    say_in_ledger,
    start_conversation,
    unread,
)
from app.schemas import (
    MessageKind,
    PhotoKind,
    Place,
    QuestionResult,
    Role,
    Room,
    RoomKind,
    RoomMessage,
    Theme,
    User,
)

router = APIRouter(prefix="/api")

NEEDS_KEY_MESSAGE = "ツインと話すには API キーが要ります。"
AI_FAILED_MESSAGE = "いま、うまくお答えできませんでした。少し時間をおいて、もう一度お試しください。"
_TWIN_JOINED = f"{TWIN_LABEL}がこの部屋に入りました。ここでの話を読みます。"
_TWIN_LEFT = f"{TWIN_LABEL}がこの部屋からはずれました。これからの話は読みません。"
_AI_ERRORS = (
    config.MissingApiKeyError,
    ai.AIResultError,
    ai.AIRefusedError,
    ai.AIUnavailableError,
)
_PREVIEW_CHARS = 40


class MessageInput(BaseModel):
    """発言の入力。client_key は画面が作る文字列で、同じものを送り直しても発言は増えない。"""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=4000)
    photo_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    client_key: str = Field(min_length=1, max_length=64)


class RoomInput(BaseModel):
    """部屋を作る入力。"""

    model_config = ConfigDict(extra="forbid")

    kind: RoomKind
    member_ids: list[int] = Field(min_length=1, max_length=30)
    name: str | None = Field(default=None, max_length=40)


class TwinPresenceInput(BaseModel):
    present: bool


class ReadInput(BaseModel):
    """読んだ位置の入力。部屋の中の順番で受け取る。"""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0, le=db.MAX_ID)


# ---- 部屋の人の確かめと、応答の形 ----






def _failed_detail(app, error: Exception) -> str:
    """ツインが答えられなかったときの文を返す。キーがないと分かっているなら、そう伝える。

    待っても直らないので「時間をおいて」と言わない。自分用の起動の文は変えない。
    関連するテスト：tests/test_fresh.py の test_キーがないと話せない理由を返し_言葉は残る
    """
    if isinstance(error, config.MissingApiKeyError) and app.state.deployment.kind != config.KIND_MINE:
        return NEEDS_KEY_MESSAGE
    return AI_FAILED_MESSAGE


def room_for_member(conn: sqlite3.Connection, viewer: User, room_id: int) -> Room:
    """部屋の人にだけ部屋を返す。ほかの人には、ない部屋と同じ 404 にする。"""
    room = get_room(conn, room_id)
    if room is None or not is_member(conn, room_id, viewer.id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return room






































def _shareable_conversation_id(conn: sqlite3.Connection, room: Room) -> int | None:
    """共有を勧められる直前の会話。閉じていて、まだ便りにしていないときだけ返す。"""
    if room.kind is not RoomKind.TWIN:
        return None
    row = conn.execute(
        "SELECT id, closed_at FROM conversations WHERE room_id = ? ORDER BY rowid DESC LIMIT 1",
        (room.id,),
    ).fetchone()
    if row is None or row["closed_at"] is None:
        return None
    shared = conn.execute(
        "SELECT 1 FROM letters WHERE conversation_id = ? LIMIT 1", (row["id"],)
    ).fetchone()
    if shared:
        return None
    # スタンプだけの会話は、便りにできる言葉がないので勧めない
    said = [
        item["text"]
        for item in conn.execute(
            "SELECT u.text FROM utterances u WHERE u.conversation_id = ? AND u.speaker_is_twin = 0", (row["id"],)
        )
    ]
    return row["id"] if any(not twin.stamps_only(text) for text in said) else None


def _room_title(room: Room, members: list[User], viewer: User) -> str:
    if room.kind is RoomKind.TWIN:
        return TWIN_LABEL
    if room.name:
        return room.name
    others = [member.display_name for member in members if member.id != viewer.id]
    return "、".join(others) or "部屋"


def _self_in_room(conn: sqlite3.Connection, room_id: int) -> bool:
    """部屋に本人がいるかを返す。ツインは本人がいる部屋にだけ入れる。"""
    self_user = db.self_user(conn)
    return self_user is not None and is_member(conn, room_id, self_user.id)


def _can_manage_twin(conn: sqlite3.Connection, room: Room, viewer: User) -> bool:
    return room.kind is RoomKind.GROUP and consent.can_act_for_self(conn, viewer) and _self_in_room(conn, room.id)


def _can_remove_members(conn: sqlite3.Connection, room: Room, viewer: User) -> bool:
    """グループから人をはずせるかを返す。家族の間で互いにはずし合わないよう、作った人と本人に限る。"""
    return room.kind is RoomKind.GROUP and (viewer.role is Role.SELF or creator_of(conn, room.id) == viewer.id)


def room_summary(conn: sqlite3.Connection, room: Room, viewer: User) -> dict:
    members = members_of(conn, room.id)
    last = last_message(conn, room.id)
    preview = None
    if last is not None:
        preview = last["body"][:_PREVIEW_CHARS] if last["body"] else "写真"
    summary = {
        "id": room.id,
        "kind": room.kind.value,
        "name": _room_title(room, members, viewer),
        "members": [person_view(member) for member in members],
        "twin_present": room.kind is RoomKind.TWIN or active_presence(conn, room.id) is not None,
        "last_text": preview,
        "last_at": last["sent_at"] if last is not None else None,
        "can_manage_twin": _can_manage_twin(conn, room, viewer),
        "can_remove_members": _can_remove_members(conn, room, viewer),
    }
    # ツインとの部屋には未読の数を出さない。AI が読んだことを「既読」と言わないため。
    if room.kind is not RoomKind.TWIN:
        summary["unread_count"] = unread(conn, room.id, viewer.id)[0]
    return summary


def _message_window(length: int, after: int, before: int | None, limit: int) -> tuple[int, int]:
    """返す発言の範囲を、飛ばす行の数と返す行の数で決める。返す行の数の -1 は終わりまで。

    before があればその順番より前を、after が1以上ならその順番より後を、どちらもなければ新しいほうを返す。
    after が1以上のときだけ数を限らない。3秒ごとの読み込みと、順番を進めながら読む道具が、途中の発言を飛ばさないため。
    関連するテスト：tests/test_rooms.py の test_afterが1以上なら300件を超えても全部返す
    """
    if before is None and after > 0:
        return after, -1
    end = length if before is None else min(before - 1, length)
    skip = max(end - limit, 0)
    return skip, end - skip


def _room_view(
    request: Request,
    conn: sqlite3.Connection,
    room: Room,
    viewer: User,
    after: int = 0,
    before: int | None = None,
    limit: int = MESSAGES_PER_PAGE,
) -> dict:
    length = room_length(conn, room.id)
    skip, count = _message_window(length, after, before, limit)
    items = messages_range(conn, room.id, skip, count, viewer)
    speakers = {item["sender_user_id"] for item in items if item["sender_user_id"]}
    people = {member.id: member for member in members_of(conn, room.id)}
    for user_id in speakers - people.keys():
        people[user_id] = db.get_user(conn, user_id)
    view = {
        "room": room_summary(conn, room, viewer),
        "items": items,
        "people": [person_view(person) for person in people.values()],
        "last_seq": length,
        "has_earlier": min(skip, length) > 0,
        "thinking": room.id in request.app.state.thinking_rooms,
        "shareable_conversation_id": _shareable_conversation_id(conn, room),
    }
    if room.kind is not RoomKind.TWIN:
        view["first_unread_seq"] = unread(conn, room.id, viewer.id)[1]
        view["others_read_seqs"] = others_read_seqs(conn, room.id, viewer.id)
    return view


# ---- API ----


@router.get("/rooms")
def list_rooms(
    request: Request, viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[dict]]:
    """見える部屋の一覧。本人にはいちばん上にツインとの部屋を置く。未読の数は持たない。"""
    if viewer.role is Role.SELF:
        ensure_twin_room(conn, viewer, clock.now(request.app).isoformat())
    rows = conn.execute(
        """
        SELECT r.* FROM rooms r JOIN room_members m ON m.room_id = r.id
        WHERE m.user_id = ? AND m.left_at IS NULL
        ORDER BY r.kind = ? DESC,
                 COALESCE((SELECT MAX(x.rowid) FROM room_messages x WHERE x.room_id = r.id), 0) DESC
        """,
        (viewer.id, RoomKind.TWIN.value),
    ).fetchall()
    return {"items": [room_summary(conn, db.row_to_model(row, Room), viewer) for row in rows]}


@router.post("/rooms", status_code=201)
def make_room(
    body: RoomInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """部屋を作る。だれでもでき、作った人は必ず入る。ツインとの1対1はここからは作れない。"""
    if body.kind is RoomKind.TWIN:
        raise HTTPException(status_code=400, detail="この部屋は作れません。")
    others = sorted({user_id for user_id in body.member_ids if user_id != viewer.id})
    if not others or (body.kind is RoomKind.PAIR and len(others) != 1):
        raise HTTPException(status_code=400, detail="部屋に入る人を選び直してください。")
    for user_id in others:
        if not can_log_in(db.get_user(conn, user_id), request.app):
            raise HTTPException(status_code=400, detail="部屋に入れない人が選ばれています。")
    if body.kind is RoomKind.PAIR:
        existing = find_pair_room(conn, viewer.id, others[0])
        if existing is not None:
            return room_summary(conn, existing, viewer)
    # 作った人を先頭で入れる。creator_of はこの順番で作った人を見分ける。
    room = create_room(
        conn,
        kind=body.kind,
        name=(body.name or "").strip() or None,
        member_ids=[viewer.id, *others],
        moment=clock.now(request.app).isoformat(),
    )
    return room_summary(conn, room, viewer)


@router.get("/rooms/{room_id}/messages")
def read_messages(
    request: Request,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    after: int = Query(default=0, ge=0, le=db.MAX_ID),
    before: int | None = Query(default=None, ge=1, le=db.MAX_ID),
    limit: int = Query(default=MESSAGES_PER_PAGE, ge=1, le=MESSAGES_PER_PAGE),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """部屋の発言を返す。部屋の人以外は 404。3秒ごとに読まれるので、閲覧の記録は残さない。"""
    room = room_for_member(conn, viewer, room_id)
    return _room_view(request, conn, room, viewer, after, before, limit)


def _usable_photo(conn: sqlite3.Connection, viewer: User, photo_id: int | None, room: Room):
    """発言に付ける写真を確かめる。自分が送った写真だけを付けられる。"""
    if photo_id is None:
        return None
    photo = photos.get_photo(conn, photo_id)
    if photo is None or photo.owner_user_id != viewer.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    # AI に送るのは会話の写真だけ。プロフィール写真をツインとの部屋に付けさせない。
    # 人どうしの部屋には部屋の写真だけを付ける。会話の写真の説明を別の部屋へ持ち出さないため。
    wanted = PhotoKind.TALK if room.kind is RoomKind.TWIN else PhotoKind.ROOM
    if photo.kind is not wanted:
        raise HTTPException(status_code=400, detail="この写真はここに送れません。")
    return photo


def _lock_for(app, room_id: int) -> threading.Lock:
    with app.state.room_locks_guard:
        return app.state.room_locks.setdefault(room_id, threading.Lock())


@router.post("/rooms/{room_id}/messages")
def send_message(
    body: MessageInput,
    request: Request,
    background: BackgroundTasks,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """発言する。ツインとの部屋では、返事を待って一緒に返す。"""
    room = room_for_member(conn, viewer, room_id)
    text = (body.text or "").strip() or None
    if text is None and body.photo_id is None:
        raise HTTPException(status_code=400, detail="ひとことか写真を入れてください。")
    photo = _usable_photo(conn, viewer, body.photo_id, room)
    if room.kind is RoomKind.TWIN:
        return _send_to_twin(request, background, conn, room, viewer, text, photo, body.client_key)
    return _send_to_people(request, background, conn, room, viewer, text, photo, body.client_key)


def _send_to_twin(request, background, conn, room, viewer, text, photo, client_key) -> dict:
    app = request.app
    now = clock.now(app)
    closed_id = None
    with _lock_for(app, room.id):
        message = find_by_client_key(conn, room.id, viewer.id, client_key)
        if message is None:
            conversation = open_conversation(conn, room.id)
            if conversation is not None and conversation.conversation_date != now.date():
                # 閉じ忘れは、日付の違いで閉じる。言明に分ける処理は裏で流す。
                close_conversation(conn, conversation.id, now.isoformat())
                closed_id = conversation.id
                background.add_task(jobs.after_close, app, closed_id)
                conversation = None
            if conversation is None:
                conversation = start_conversation(conn, room.id, now.date())
            message = _save_self_message(conn, room, conversation.id, viewer, text, photo, client_key, now)
        else:
            replies = messages_after(conn, room.id, position_of(conn, message.id), viewer)
            if any(item["is_twin"] for item in replies):
                return {"message": message_view(conn, message.id, viewer), "replies": replies}
        app.state.thinking_rooms.add(room.id)
        try:
            twin.respond_in_twin_room(
                conn,
                room=room,
                self_user=viewer,
                message=message,
                now=now,
                photos_dir=app.state.deployment.photos_dir,
                ask=app.state.twin_may_ask,
            )
        except _AI_ERRORS as error:
            # 失敗の応答では BackgroundTasks が動かないので、閉じた会話の処理は別に流す
            if closed_id is not None:
                threading.Thread(target=jobs.after_close, args=(app, closed_id), daemon=True).start()
            raise HTTPException(status_code=503, detail=_failed_detail(app, error)) from error
        finally:
            app.state.thinking_rooms.discard(room.id)
    return {
        "message": message_view(conn, message.id, viewer),
        "replies": messages_after(conn, room.id, position_of(conn, message.id), viewer),
    }


def _save_self_message(conn, room, conversation_id, viewer, text, photo, client_key, now) -> RoomMessage:
    """本人の発言を保存する。写真だけのときは発言の原文がないので、utterances には入れない。"""
    photo_id = photo.id if photo else None
    if text is None:
        return insert_message(
            conn,
            room_id=room.id,
            sender_user_id=viewer.id,
            photo_id=photo_id,
            conversation_id=conversation_id,
            client_key=client_key,
            sent_at=now.isoformat(),
        )
    return say_in_ledger(
        conn,
        room_id=room.id,
        conversation_id=conversation_id,
        place=Place.DIARY_AND_TALK,
        sender_user_id=viewer.id,
        text=text,
        photo_id=photo_id,
        client_key=client_key,
        sent_at=now,
    )


def _send_to_people(request, background, conn, room, viewer, text, photo, client_key) -> dict:
    app = request.app
    now = clock.now(app)
    existing = find_by_client_key(conn, room.id, viewer.id, client_key)
    if existing is not None:
        return {"message": message_view(conn, existing.id, viewer), "replies": []}
    photo_id = photo.id if photo else None
    presence = active_presence(conn, room.id) if room.kind is RoomKind.GROUP else None
    if presence is not None and text is not None:
        # ツインがいる間の発言だけを ledger に入れる。裏で新しい分を分け、必要ならツインが書く。
        message = say_in_ledger(
            conn,
            room_id=room.id,
            conversation_id=presence.conversation_id,
            place=Place.GROUP,
            sender_user_id=viewer.id,
            text=text,
            photo_id=photo_id,
            client_key=client_key,
            sent_at=now,
        )
        background.add_task(jobs.after_group_message, app, room.id, presence.conversation_id, message.id)
    else:
        # 人だけの部屋。本文はここにだけ持ち、AI を呼ばない。
        message = insert_message(
            conn,
            room_id=room.id,
            sender_user_id=viewer.id,
            text=text,
            photo_id=photo_id,
            client_key=client_key,
            sent_at=now.isoformat(),
        )
    return {"message": message_view(conn, message.id, viewer), "replies": []}


@router.post("/rooms/{room_id}/close")
def close_day(
    request: Request,
    background: BackgroundTasks,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """「今日はここまで」。ツインが短く返し、言明に分ける処理は裏で流して本人を待たせない。"""
    room = room_for_member(conn, viewer, room_id)
    if room.kind is not RoomKind.TWIN:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    app = request.app
    now = clock.now(app)
    with _lock_for(app, room.id):
        conversation = open_conversation(conn, room.id)
        if conversation is None:
            return {"closed": False, "replies": [], "conversation_id": None}
        before = room_length(conn, room.id)
        app.state.thinking_rooms.add(room.id)
        try:
            twin.close_in_twin_room(conn, room=room, self_user=viewer, conversation=conversation, now=now)
        except _AI_ERRORS:
            # 返事が作れなくても会話は閉じる。本人が終えたいのに、失敗で引き留めないため。
            pass
        finally:
            app.state.thinking_rooms.discard(room.id)
        close_conversation(conn, conversation.id, now.isoformat())
    background.add_task(jobs.after_close, app, conversation.id)
    return {
        "closed": True,
        "replies": messages_after(conn, room.id, before, viewer),
        "conversation_id": conversation.id,
    }


@router.post("/rooms/{room_id}/twin")
def set_twin_presence(
    body: TwinPresenceInput,
    request: Request,
    background: BackgroundTasks,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """グループにツインを入れる、はずす。本人と、立場が家族の託された人ができる。部屋に表示する。

    本人がいない部屋には入れない。本人のいない場でツインが本人のことを話さないようにするため。
    関連するテスト：tests/test_room_access.py
    """
    room = room_for_member(conn, viewer, room_id)
    if not consent.can_act_for_self(conn, viewer):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if room.kind is not RoomKind.GROUP:
        raise HTTPException(status_code=400, detail="ツインを入れられるのはグループだけです。")
    if not _self_in_room(conn, room.id):
        raise HTTPException(status_code=400, detail="ツインは本人がいる部屋にだけ入れます。")
    now = clock.now(request.app)
    presence = active_presence(conn, room.id)
    if body.present and presence is None:
        with ledger.atomic(conn):
            conversation = start_conversation(conn, room.id, now.date())
            conn.execute(
                "INSERT INTO twin_presences (room_id, conversation_id, joined_at) VALUES (?, ?, ?)",
                (room.id, conversation.id, now.isoformat()),
            )
            insert_message(
                conn,
                room_id=room.id,
                sender_user_id=viewer.id,
                kind=MessageKind.NOTICE,
                text=_TWIN_JOINED,
                sent_at=now.isoformat(),
            )
    elif not body.present and presence is not None:
        with ledger.atomic(conn):
            _end_twin_presence(conn, room, presence, viewer, now)
        background.add_task(jobs.split_quietly, app=request.app, conversation_id=presence.conversation_id)
    return _room_view(request, conn, room, viewer, 0)


def _end_twin_presence(conn: sqlite3.Connection, room: Room, presence, actor: User, now) -> None:
    """ツインをはずし、会話を閉じ、部屋に表示する。言明に分ける処理は呼ぶ側が裏で流す。"""
    conn.execute("UPDATE twin_presences SET left_at = ? WHERE id = ?", (now.isoformat(), presence.id))
    close_conversation(conn, presence.conversation_id, now.isoformat())
    insert_message(
        conn,
        room_id=room.id,
        sender_user_id=actor.id,
        kind=MessageKind.NOTICE,
        text=_TWIN_LEFT,
        sent_at=now.isoformat(),
    )


def _notice(conn: sqlite3.Connection, room: Room, actor: User, text: str, now) -> None:
    insert_message(
        conn, room_id=room.id, sender_user_id=actor.id, kind=MessageKind.NOTICE, text=text, sent_at=now.isoformat()
    )


@router.post("/rooms/{room_id}/members/{user_id}/remove", status_code=204)
def remove_from_room(
    request: Request,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    user_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """グループから人をはずす。作った人と部屋にいる本人ができる。本人はほかの人にはずされない。"""
    room = room_for_member(conn, viewer, room_id)
    if room.kind is not RoomKind.GROUP:
        raise HTTPException(status_code=400, detail="はずせるのはグループの人だけです。")
    if not _can_remove_members(conn, room, viewer):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    target = db.get_user(conn, user_id)
    if target is None or not is_member(conn, room.id, target.id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if target.role is Role.SELF or target.id == viewer.id:
        raise HTTPException(status_code=400, detail="この人ははずせません。")
    now = clock.now(request.app)
    with ledger.atomic(conn):
        remove_member(conn, room.id, target.id, now.isoformat())
        _notice(conn, room, viewer, f"{target.display_name}がこの部屋からはずれました。", now)


@router.post("/rooms/{room_id}/leave", status_code=204)
def leave_room(
    request: Request,
    background: BackgroundTasks,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """自分でグループを出る。本人が出たらツインも一緒にはずす。本人のいない部屋の話を読まないため。"""
    room = room_for_member(conn, viewer, room_id)
    if room.kind is not RoomKind.GROUP:
        raise HTTPException(status_code=400, detail="出られるのはグループだけです。")
    now = clock.now(request.app)
    presence = active_presence(conn, room.id) if viewer.role is Role.SELF else None
    with ledger.atomic(conn):
        if presence is not None:
            _end_twin_presence(conn, room, presence, viewer, now)
        remove_member(conn, room.id, viewer.id, now.isoformat())
        _notice(conn, room, viewer, f"{viewer.display_name}がこの部屋を出ました。", now)
    if presence is not None:
        background.add_task(jobs.split_quietly, app=request.app, conversation_id=presence.conversation_id)


@router.post("/rooms/{room_id}/read", status_code=204)
def read_room(
    body: ReadInput,
    request: Request,
    room_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """読んだ位置を進める。画面が部屋を開いて見えているときに送る。ツインとの部屋には使わない。"""
    room = room_for_member(conn, viewer, room_id)
    if room.kind is RoomKind.TWIN:
        raise HTTPException(status_code=400, detail="この部屋には使えません。")
    mark_read(conn, room.id, viewer.id, body.seq, clock.now(request.app).isoformat())


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Theme


@router.get("/resting-themes")
def list_resting_themes(
    _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, list[str]]:
    """お休みにしている話題の一覧。本人だけが開ける。"""
    return {"items": sorted(theme.value for theme in memory.resting_themes(conn))}


@router.post("/resting-themes/resume", status_code=204)
def resume_theme(
    body: ResumeInput,
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """話題のお休みをやめる。決めるのは本人なので、本人が戻せるようにする。"""
    with conn:
        conn.execute(
            "UPDATE question_logs SET result = ? WHERE result = ? AND theme = ?",
            (QuestionResult.LATER.value, QuestionResult.RESTING.value, body.theme.value),
        )


@router.post("/questions/{question_id}/rest", status_code=204)
def rest_question(
    question_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> None:
    """問いの話題をお休みにする。本人だけができる。戻せるよう、質問の記録の結果だけを変える。"""
    with conn:
        cursor = conn.execute(
            "UPDATE question_logs SET result = ? WHERE id = ?",
            (QuestionResult.RESTING.value, question_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
