"""ツインが前の話を思い出すために読むものを集める（決まった手順で動く層）。

役割：今の会話、過去の会話の要約、前に聞いた本人の言葉、聞いた問い、お休みの話題を、上限の中で集める。
4つの極：1. 書く・話す。
守る原則：3（要約は AI の読み取りで、本人の言葉と分ける）、6（お休みの話題は聞かない）、
          15（招かれた部屋だけを読む。読むのはツインとの部屋の話だけ）。

意思モデルができるまでの仮の形で、固める処理ができたら要約と本人の言葉を意思モデルに置き換える。
読む量の上限は config.MEMORY_LIMITS で決める。この製品は記録が増えても読む量を増やさない。
証言と AI の推測は入れない。家族の言葉を本人の意思として扱わないため。

AI に渡す文は utterances の表だけから作る。人だけの部屋の本文は room_messages にだけあるので、
ここからは読めない。

関連するテスト：tests/test_twin.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from datetime import date

from app import config, db
from app.prompts import TWIN_LABEL
from app.splitter import speech
from app.schemas import ConfirmStatus, Place, QuestionResult, Room, RoomKind, RoomMessage, Source, Theme, User

LIMITS = config.MEMORY_LIMITS
_SIMILAR_QUESTION = 0.8


def _normalized(question: str) -> str:
    return re.sub(r"[\s、。？?！!「」]", "", question)


def is_repeated(question: str, asked: list[str]) -> bool:
    """同じ問いか、よく似た問いかを返す。"""
    target = _normalized(question)
    return any(
        difflib.SequenceMatcher(None, target, _normalized(item)).ratio() >= _SIMILAR_QUESTION
        for item in asked
    )


def date_label(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def message_text(conn: sqlite3.Connection, message: RoomMessage) -> str:
    """発言の本文を返す。写真だけのときは空にする。"""
    if message.utterance_id is None:
        return message.text or ""
    row = conn.execute("SELECT text FROM utterances WHERE id = ?", (message.utterance_id,)).fetchone()
    return row["text"]


def _speaker_label(conn: sqlite3.Connection, user_id: int | None, cache: dict[int, str]) -> str:
    if user_id is None:
        return TWIN_LABEL
    if user_id not in cache:
        user = db.get_user(conn, user_id)
        cache[user_id] = user.display_name if user else "はずした人"
    return cache[user_id]


def conversation_lines(
    conn: sqlite3.Connection,
    conversation_id: int,
    self_user: User,
    *,
    max_lines: int | None = None,
    max_chars: int | None = None,
    with_photos: bool = False,
    with_added: bool = False,
) -> list[str]:
    """会話の発言を、話した順の文にする。新しいほうから上限まで。

    写真の説明は AI の読み取りなので、話した人の行にせず分ける。ツインとの部屋でだけ使う
    （with_photos）。説明を別の部屋へ持ち出さないため。
    with_added は、架空の記録で足した本人の短い受け答え（発言の表に入らない行）も読む。ツインの次の返事が答えを
    無視しないよう、ツインの文にだけ渡す。要約と分割には渡さないので、記録にならない（fiction/run_script.py）。
    """
    rows = conn.execute(
        """
        SELECT m.sender_user_id, u.text AS body, m.text AS added, p.description AS photo_description, m.photo_id
        FROM room_messages m
        LEFT JOIN utterances u ON u.id = m.utterance_id
        LEFT JOIN photos p ON p.id = m.photo_id AND p.kind = '会話'
        WHERE m.conversation_id = ?
        ORDER BY m.rowid
        """,
        (conversation_id,),
    ).fetchall()
    names: dict[int, str] = {}
    lines = []
    for row in rows:
        speaker = _speaker_label(conn, row["sender_user_id"], names)
        if with_photos and row["photo_id"] and row["photo_description"]:
            lines.append(f"（{speaker}が送った写真。AI の読み取り）{row['photo_description']}")
        body = row["body"] or (row["added"] if with_added else None)
        if body:
            lines.append(f"{speaker}：{speech(body)}")
    return _newest_within(lines, max_lines, max_chars)


def _newest_within(lines: list[str], max_lines: int | None, max_chars: int | None) -> list[str]:
    """新しいほうから、行数と字数の上限まで残す。"""
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if max_lines is not None and len(kept) >= max_lines:
            break
        if max_chars is not None and total + len(line) > max_chars and kept:
            break
        kept.append(line)
        total += len(line)
    return list(reversed(kept))


def _past_conversations(conn: sqlite3.Connection, room_id: int, current_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, conversation_date, summary FROM conversations
        WHERE room_id = ? AND id <> ? AND closed_at IS NOT NULL
        ORDER BY rowid DESC LIMIT ?
        """,
        (room_id, current_id, LIMITS["past_conversations"]),
    ).fetchall()


def _summaries(conn: sqlite3.Connection, room_id: int, current_id: int, self_user: User) -> list[str]:
    """過去の会話の要約を古い順に返す。直前の会話に要約がなければ、発言をそのまま添える。"""
    rows = _past_conversations(conn, room_id, current_id)
    parts = []
    for index, row in enumerate(rows):
        day = date_label(date.fromisoformat(row["conversation_date"]))
        if row["summary"]:
            parts.append(f"- {day}：{row['summary'][:LIMITS['summary_chars']]}")
        elif index == 0:
            # 閉じた直後や要約の失敗のときに忘れないよう、新しいほうから発言を渡す
            lines = conversation_lines(
                conn, row["id"], self_user, max_lines=LIMITS["unsummarized_utterances"], with_photos=True
            )
            if lines:
                parts.append(f"- {day}（要約の前の発言）：\n" + "\n".join(f"  {line}" for line in lines))
    return list(reversed(parts))


def _self_words(conn: sqlite3.Connection, current_id: int) -> list[str]:
    """前に聞いた本人の言葉を返す。ツインとの部屋から来た本人の言葉と本人の訂正だけ。"""
    rows = conn.execute(
        """
        SELECT s.body, s.effective_date FROM statements s
        WHERE s.source IN (?, ?)
          AND s.confirm_status <> ?
          AND s.id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
          AND EXISTS (
              SELECT 1 FROM json_each(s.source_utterance_ids) j
              JOIN utterances u ON u.id = j.value
              WHERE u.place = ? AND u.conversation_id <> ?
          )
        ORDER BY s.created_at DESC, s.rowid DESC
        LIMIT ?
        """,
        (
            Source.SELF_WORDS.value,
            Source.SELF_CORRECTION.value,
            ConfirmStatus.RESTING.value,
            Place.DIARY_AND_TALK.value,
            current_id,
            LIMITS["self_statements"],
        ),
    ).fetchall()
    lines = [f"- {date_label(date.fromisoformat(row['effective_date']))}：{row['body']}" for row in reversed(rows)]
    return _newest_within(lines, None, LIMITS["self_chars"])


def asked_questions(conn: sqlite3.Connection, room_id: int) -> list[str]:
    """この部屋で聞いた問いを、新しいほうから上限まで返す。"""
    rows = conn.execute(
        """
        SELECT question FROM question_logs
        WHERE room_id = ? AND asked_on IS NOT NULL ORDER BY id DESC LIMIT ?
        """,
        (room_id, LIMITS["asked_questions"]),
    ).fetchall()
    return [row["question"] for row in rows]


def questions_in_a_row(conn: sqlite3.Connection, conversation_id: int) -> int:
    """ツインの返事に問いが何回続いているかを、新しいほうから数える。"""
    rows = conn.execute(
        """
        SELECT is_twin, question_log_id FROM room_messages
        WHERE conversation_id = ? ORDER BY rowid DESC
        """,
        (conversation_id,),
    ).fetchall()
    count = 0
    has_question = False
    seen_twin_turn = False
    for row in rows:
        if row["is_twin"]:
            seen_twin_turn = True
            has_question = has_question or row["question_log_id"] is not None
        elif seen_twin_turn:
            # 本人の発言で1回の返事が区切られる
            if not has_question:
                return count
            count += 1
            has_question = False
            seen_twin_turn = False
    return count + 1 if has_question else count


def resting_themes(conn: sqlite3.Connection) -> set[Theme]:
    """お休みにした話題を返す。"""
    rows = conn.execute(
        "SELECT DISTINCT theme FROM question_logs WHERE result = ? AND theme IS NOT NULL",
        (QuestionResult.RESTING.value,),
    ).fetchall()
    return {Theme(row["theme"]) for row in rows}


def _pending_candidates(conn: sqlite3.Connection, resting: set[Theme]) -> list[str]:
    """言明に分ける処理が残した、本人に確かめる問いの候補を返す。お休みのテーマは入れない。

    ツインとの部屋の発言から作った候補だけを使う。グループとスレッドから生まれた問いを、
    本人とツインの部屋へ持ち込まないため。
    """
    rows = conn.execute(
        """
        SELECT q.question, q.theme FROM question_logs q
        JOIN conversations c ON c.id = q.conversation_id
        JOIN rooms r ON r.id = c.room_id
        WHERE q.asked_on IS NULL AND q.result IS NULL AND r.kind = ?
        ORDER BY q.id DESC LIMIT ?
        """,
        (RoomKind.TWIN.value, LIMITS["pending_questions"] * 3),
    ).fetchall()
    usable = [row["question"] for row in rows if not row["theme"] or Theme(row["theme"]) not in resting]
    return usable[: LIMITS["pending_questions"]]


def mark_candidate_used(conn: sqlite3.Connection, question: str, day: date) -> None:
    """候補とよく似た問いを聞いたら、その候補に聞いた日を入れる。同じことを聞き直さないため。"""
    rows = conn.execute(
        "SELECT id, question FROM question_logs WHERE asked_on IS NULL AND result IS NULL"
    ).fetchall()
    for row in rows:
        if is_repeated(row["question"], [question]):
            conn.execute(
                "UPDATE question_logs SET asked_on = ?, result = ? WHERE id = ?",
                (day.isoformat(), QuestionResult.ANSWERED.value, row["id"]),
            )


def build_twin_context(
    conn: sqlite3.Connection, room: Room, conversation_id: int, self_user: User, today: date
) -> dict:
    """ツインの会話の入力の材料を集める。"""
    resting = resting_themes(conn)
    return {
        "today": date_label(today),
        "summaries": _summaries(conn, room.id, conversation_id, self_user),
        "self_words": _self_words(conn, conversation_id),
        "asked": asked_questions(conn, room.id),
        "candidates": _pending_candidates(conn, resting),
        "resting": sorted(theme.value for theme in resting),
        "transcript": "\n".join(
            conversation_lines(
                conn,
                conversation_id,
                self_user,
                max_lines=LIMITS["current_utterances"],
                max_chars=LIMITS["current_chars"],
                with_photos=True,
                with_added=True,
            )
        ),
    }


def thread_lines(conn: sqlite3.Connection, letter_id: int) -> tuple[str, list[str]]:
    """スレッドに見えている便りの本文とコメントだけを、新しいほうから上限まで返す。"""
    letter = conn.execute("SELECT body FROM letters WHERE id = ?", (letter_id,)).fetchone()
    rows = conn.execute(
        """
        SELECT c.author_user_id, c.body FROM comments c
        WHERE c.letter_id = ? ORDER BY c.rowid
        """,
        (letter_id,),
    ).fetchall()
    names: dict[int, str] = {}
    lines = [f"{_speaker_label(conn, row['author_user_id'], names)}：{speech(row['body'])}" for row in rows]
    return letter["body"], _newest_within(lines, LIMITS["current_utterances"], LIMITS["current_chars"])
