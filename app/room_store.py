"""部屋とメッセージの表の読み書き（決まった手順で動く層）。AI は呼ばない。

役割：部屋、部屋の人、会話の区切り、部屋の発言を読み書きする。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：10（部屋の発言は部屋の人だけに見せる。確かめは app/rooms.py）、15（ツインは招かれた部屋だけを読む）。

AI に渡す処理は utterances の表しか読まない。ツインとの部屋の発言と、ツインがいる間のグループの発言は、
本文を ledger を通して utterances に入れ、room_messages からは番号で指す。
人だけの部屋の発言と、ツインが入る前の発言は、本文を room_messages にだけ持つ。
だから「人だけの部屋の発言が AI に渡らない」は、表の形で守られる。

関連するテスト：tests/test_rooms.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app import db, ledger
from app.prompts import TWIN_LABEL
from app.schemas import (
    Conversation,
    ConversationDraft,
    MessageKind,
    Place,
    QuestionResult,
    Role,
    Room,
    RoomKind,
    RoomMessage,
    TwinPresence,
    User,
    UtteranceDraft,
)


def get_room(conn: sqlite3.Connection, room_id: int) -> Room | None:
    row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    return db.row_to_model(row, Room) if row else None


def is_member(conn: sqlite3.Connection, room_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM room_members WHERE room_id = ? AND user_id = ? AND left_at IS NULL",
        (room_id, user_id),
    ).fetchone()
    return row is not None


def members_of(conn: sqlite3.Connection, room_id: int) -> list[User]:
    rows = conn.execute(
        """
        SELECT u.* FROM users u JOIN room_members r ON r.user_id = u.id
        WHERE r.room_id = ? AND r.left_at IS NULL ORDER BY r.rowid
        """,
        (room_id,),
    ).fetchall()
    return [db.row_to_model(row, User) for row in rows]


def creator_of(conn: sqlite3.Connection, room_id: int) -> int | None:
    """部屋を作った人を返す。作るときに、作った人を先頭で入れている。出たあとも変わらない。"""
    row = conn.execute(
        "SELECT user_id FROM room_members WHERE room_id = ? ORDER BY rowid LIMIT 1", (room_id,)
    ).fetchone()
    return row["user_id"] if row else None


def remove_member(conn: sqlite3.Connection, room_id: int, user_id: int, moment: str) -> None:
    """部屋の人でなくする。発言は記録として残る。"""
    conn.execute(
        "UPDATE room_members SET left_at = ? WHERE room_id = ? AND user_id = ? AND left_at IS NULL",
        (moment, room_id, user_id),
    )


def find_pair_room(conn: sqlite3.Connection, first: int, second: int) -> Room | None:
    """2人とも入っている1対1の部屋を返す。本人のいない部屋もあるので、片方だけでは探さない。"""
    row = conn.execute(
        """
        SELECT r.* FROM rooms r
        WHERE r.kind = ?
          AND EXISTS (SELECT 1 FROM room_members a WHERE a.room_id = r.id AND a.user_id = ? AND a.left_at IS NULL)
          AND EXISTS (SELECT 1 FROM room_members b WHERE b.room_id = r.id AND b.user_id = ? AND b.left_at IS NULL)
        """,
        (RoomKind.PAIR.value, first, second),
    ).fetchone()
    return db.row_to_model(row, Room) if row else None


def create_room(
    conn: sqlite3.Connection, *, kind: RoomKind, name: str | None, member_ids: list[int], moment: str
) -> Room:
    with ledger.atomic(conn):
        room_id = db.insert_row(
            conn, "rooms", {"kind": kind.value, "name": name, "created_at": moment}
        )
        for user_id in member_ids:
            conn.execute(
                "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?, ?, ?)",
                (room_id, user_id, moment),
            )
    return get_room(conn, room_id)


def twin_room_of(conn: sqlite3.Connection) -> Room | None:
    row = conn.execute(
        "SELECT * FROM rooms WHERE kind = ? ORDER BY rowid LIMIT 1", (RoomKind.TWIN.value,)
    ).fetchone()
    return db.row_to_model(row, Room) if row else None


def ensure_twin_room(conn: sqlite3.Connection, self_user: User, moment: str) -> Room:
    """本人とツインの部屋を返す。なければ作る。本人のほかの人は入らない。"""
    return twin_room_of(conn) or create_room(
        conn, kind=RoomKind.TWIN, name=TWIN_LABEL, member_ids=[self_user.id], moment=moment
    )


def active_presence(conn: sqlite3.Connection, room_id: int) -> TwinPresence | None:
    row = conn.execute(
        "SELECT * FROM twin_presences WHERE room_id = ? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
        (room_id,),
    ).fetchone()
    return db.row_to_model(row, TwinPresence) if row else None


def open_conversation(conn: sqlite3.Connection, room_id: int) -> Conversation | None:
    row = conn.execute(
        "SELECT * FROM conversations WHERE room_id = ? AND closed_at IS NULL ORDER BY rowid DESC LIMIT 1",
        (room_id,),
    ).fetchone()
    return db.row_to_model(row, Conversation) if row else None


def start_conversation(conn: sqlite3.Connection, room_id: int, day: date) -> Conversation:
    return ledger.add_conversation(conn, ConversationDraft(conversation_date=day, room_id=room_id))


def close_conversation(conn: sqlite3.Connection, conversation_id: int, moment: str) -> None:
    with ledger.atomic(conn):
        conn.execute("UPDATE conversations SET closed_at = ? WHERE id = ?", (moment, conversation_id))
        # 答えないまま閉じた問いは途中として残す。次の会話で「答えを待っている」扱いにしないため。
        conn.execute(
            "UPDATE question_logs SET result = ? WHERE conversation_id = ? AND result IS NULL AND asked_on IS NOT NULL",
            (QuestionResult.PARTIAL.value, conversation_id),
        )


def insert_message(
    conn: sqlite3.Connection,
    *,
    room_id: int,
    sent_at: str,
    sender_user_id: int | None = None,
    is_twin: bool = False,
    kind: MessageKind = MessageKind.SAY,
    text: str | None = None,
    photo_id: int | None = None,
    utterance_id: int | None = None,
    conversation_id: int | None = None,
    question_log_id: int | None = None,
    client_key: str | None = None,
) -> RoomMessage:
    with ledger.atomic(conn):
        message_id = db.insert_row(
            conn,
            "room_messages",
            {
                "room_id": room_id,
                "sender_user_id": sender_user_id,
                "is_twin": int(is_twin),
                "kind": kind.value,
                "text": text,
                "photo_id": photo_id,
                "utterance_id": utterance_id,
                "conversation_id": conversation_id,
                "question_log_id": question_log_id,
                "client_key": client_key,
                "sent_at": sent_at,
            },
        )
    return get_message(conn, message_id)


def get_message(conn: sqlite3.Connection, message_id: int) -> RoomMessage | None:
    row = conn.execute("SELECT * FROM room_messages WHERE id = ?", (message_id,)).fetchone()
    return db.row_to_model(row, RoomMessage) if row else None


def say_in_ledger(
    conn: sqlite3.Connection,
    *,
    room_id: int,
    conversation_id: int,
    place: Place,
    text: str,
    sent_at: datetime,
    sender_user_id: int | None = None,
    is_twin: bool = False,
    photo_id: int | None = None,
    client_key: str | None = None,
    question_log_id: int | None = None,
) -> RoomMessage:
    """AI が読む部屋の発言を保存する。本文は ledger を通して utterances にだけ入れる。"""
    with ledger.atomic(conn):
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation_id,
                speaker_user_id=sender_user_id,
                speaker_is_twin=is_twin,
                text=text,
                spoken_at=sent_at,
                place=place,
            ),
        )
        return insert_message(
            conn,
            room_id=room_id,
            sender_user_id=sender_user_id,
            is_twin=is_twin,
            utterance_id=utterance.id,
            conversation_id=conversation_id,
            photo_id=photo_id,
            client_key=client_key,
            question_log_id=question_log_id,
            sent_at=sent_at.isoformat(),
        )


_MESSAGE_QUERY = """
SELECT m.id, m.kind, m.sender_user_id, m.is_twin, m.photo_id, m.sent_at, m.client_key,
       m.question_log_id, COALESCE(u.text, m.text) AS body, q.result AS question_result
FROM room_messages m
LEFT JOIN utterances u ON u.id = m.utterance_id
LEFT JOIN question_logs q ON q.id = m.question_log_id
"""


def _message_view(row: sqlite3.Row, viewer: User | None, seq: int) -> dict:
    """部屋の行を画面に返す形にする。

    seq は部屋の中の順番にする。表全体の rowid を返すと、ほかの部屋の発言の数が分かるため。
    質問の記録の番号は通し番号なので、本人にだけ返す。ほかの人には問いかどうかだけを返す。
    """
    is_self = viewer is not None and viewer.role is Role.SELF
    return {
        "id": row["id"],
        "seq": seq,
        "kind": row["kind"],
        "sender_user_id": row["sender_user_id"],
        "is_twin": bool(row["is_twin"]),
        "text": row["body"],
        "photo_id": row["photo_id"],
        "is_question": row["question_log_id"] is not None,
        "question_id": row["question_log_id"] if is_self else None,
        "question_rested": is_self and row["question_result"] == QuestionResult.RESTING.value,
        "sent_at": row["sent_at"],
        # 送り直しの判定に使う印は、送った本人にだけ返す
        "client_key": row["client_key"] if viewer and row["sender_user_id"] == viewer.id else None,
    }


def messages_after(conn: sqlite3.Connection, room_id: int, after: int, viewer: User | None) -> list[dict]:
    """部屋の中の順番が after より後の行を返す。部屋の行は追記だけなので、順番は変わらない。"""
    rows = conn.execute(
        _MESSAGE_QUERY + " WHERE m.room_id = ? ORDER BY m.rowid LIMIT -1 OFFSET ?", (room_id, after)
    ).fetchall()
    return [_message_view(row, viewer, after + index) for index, row in enumerate(rows, start=1)]


def conversation_messages(conn: sqlite3.Connection, conversation_id: int, viewer: User) -> list[dict]:
    rows = conn.execute(
        _MESSAGE_QUERY + " WHERE m.conversation_id = ? ORDER BY m.rowid", (conversation_id,)
    ).fetchall()
    return [_message_view(row, viewer, index) for index, row in enumerate(rows, start=1)]


def room_length(conn: sqlite3.Connection, room_id: int) -> int:
    """部屋の行の数を返す。"""
    return conn.execute("SELECT COUNT(*) FROM room_messages WHERE room_id = ?", (room_id,)).fetchone()[0]


def mark_read(conn: sqlite3.Connection, room_id: int, user_id: int, seq: int, moment: str) -> None:
    """読んだ位置を進める。戻さず、部屋の行の数を超えない。

    画面が開いて見えているときにだけ呼ばれる。3秒ごとの読み込みでは読んだことにしない。
    関連するテスト：tests/test_room_access.py
    """
    position = min(seq, room_length(conn, room_id))
    with ledger.atomic(conn):
        row = conn.execute(
            "SELECT last_read_seq FROM room_reads WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO room_reads (room_id, user_id, last_read_seq, updated_at) VALUES (?, ?, ?, ?)",
                (room_id, user_id, position, moment),
            )
        elif position > row["last_read_seq"]:
            conn.execute(
                "UPDATE room_reads SET last_read_seq = ?, updated_at = ? WHERE room_id = ? AND user_id = ?",
                (position, moment, room_id, user_id),
            )


def unread(conn: sqlite3.Connection, room_id: int, user_id: int) -> tuple[int, int | None]:
    """未読の数と、最初の未読の順番を返す。自分の発言とお知らせは数えない。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS total, MIN(seq) AS first FROM (
            SELECT ROW_NUMBER() OVER (ORDER BY m.rowid) AS seq, m.sender_user_id, m.kind
            FROM room_messages m WHERE m.room_id = ?
        )
        WHERE seq > COALESCE(
                (SELECT last_read_seq FROM room_reads WHERE room_id = ? AND user_id = ?), 0
              )
          AND kind <> ?
          AND (sender_user_id IS NULL OR sender_user_id <> ?)
        """,
        (room_id, room_id, user_id, MessageKind.NOTICE.value, user_id),
    ).fetchone()
    return row["total"], row["first"]


def others_read_seqs(conn: sqlite3.Connection, room_id: int, user_id: int) -> list[int]:
    """部屋にいるほかの人の読んだ位置を、小さい順に返す。

    名前を返さず、人の順にも並べない。だれが読んだかまでは伝えないと決めたため。
    """
    rows = conn.execute(
        """
        SELECT COALESCE(x.last_read_seq, 0) AS seq FROM room_members m
        LEFT JOIN room_reads x ON x.room_id = m.room_id AND x.user_id = m.user_id
        WHERE m.room_id = ? AND m.user_id <> ? AND m.left_at IS NULL
        """,
        (room_id, user_id),
    ).fetchall()
    return sorted(row["seq"] for row in rows)


def position_of(conn: sqlite3.Connection, message_id: int) -> int:
    """部屋の中で、その行が何番目かを返す。"""
    return conn.execute(
        """
        SELECT COUNT(*) FROM room_messages x
        JOIN room_messages m ON m.id = ? AND x.room_id = m.room_id AND x.rowid <= m.rowid
        """,
        (message_id,),
    ).fetchone()[0]


def message_view(conn: sqlite3.Connection, message_id: int, viewer: User | None) -> dict:
    row = conn.execute(_MESSAGE_QUERY + " WHERE m.id = ?", (message_id,)).fetchone()
    return _message_view(row, viewer, position_of(conn, message_id))


def find_by_client_key(
    conn: sqlite3.Connection, room_id: int, sender_id: int, client_key: str
) -> RoomMessage | None:
    row = conn.execute(
        "SELECT * FROM room_messages WHERE room_id = ? AND sender_user_id = ? AND client_key = ?",
        (room_id, sender_id, client_key),
    ).fetchone()
    return db.row_to_model(row, RoomMessage) if row else None


def last_message(conn: sqlite3.Connection, room_id: int) -> sqlite3.Row | None:
    return conn.execute(
        _MESSAGE_QUERY + " WHERE m.room_id = ? ORDER BY m.rowid DESC LIMIT 1", (room_id,)
    ).fetchone()
