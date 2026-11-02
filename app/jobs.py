"""裏で動く処理。

役割：会話を閉じたあとの言明への分割、要約、人の候補と、スレッドとグループの新しい発言の処理を流す。
4つの極：3. 読み解き、積み上げる。
守る原則：3（出どころを分ける）、9（本人の言葉は一字も変えない）、15（招かれた部屋だけを読む）。

本人を待たせないために、応答を返したあとで動く。要求の接続は閉じられているので、処理の中で接続し直す。
対象ごとにロックを取り、同じ対象を同時に分けない。AI を待つ間に届いた発言は、保存のあとでもう一巡して拾う。
失敗は job_failures の表に残す。応答のあとに起きるので、黙って失われると原因が追えないため。
本文は残さず、処理の名前と例外の種類だけを書く。

未処理は次に動いたときと、python -m app の起動のときに流し直す。処理済みの印（split_marks）を
保存と同じ処理の中で進めるので、途中で止まっても同じ発言からやり直せる。

関連するテスト：tests/test_jobs.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import sqlite3
import threading

from app import ai, clock, db, intake, memory, prompts, room_store, splitter, twin
from app.schemas import CandidateStatus, RoomKind, SplitTargetKind

# 1回の処理で分け直す回数の上限。発言が届き続けても止まるようにする。
_MAX_ROUNDS = 3
_EARLIER_LINES = 10


def _lock_for(app, key: str) -> threading.Lock:
    with app.state.job_locks_guard:
        return app.state.job_locks.setdefault(key, threading.Lock())


def _guarded(app, job: str, target: str, work) -> None:
    """裏の処理を動かす。失敗しても、ほかの処理を止めず、記録に残す。"""
    conn = db.connect(app.state.db_path)
    try:
        work(conn)
    except Exception as error:  # noqa: BLE001  裏の処理の失敗は、どんな種類でも記録して続ける
        try:
            db.log_job_failure(conn, job, target, error)
        except sqlite3.Error:
            pass
    finally:
        conn.close()


def split_new(app, conn: sqlite3.Connection, kind: SplitTargetKind, target_id: int) -> splitter.SplitReport:
    """印より後の発言を言明に分ける。人の発言がなければ AI を呼ばない。"""
    total = splitter.SplitReport()
    with _lock_for(app, f"{kind.value}:{target_id}"):
        for _ in range(_MAX_ROUNDS):
            mark = splitter.read_mark(conn, kind, target_id)
            new, last_rowid = splitter.load_after_mark(conn, kind, target_id, mark)
            if not any(not utterance.speaker_is_twin for utterance in new):
                break
            earlier = splitter.load_before_mark(conn, kind, target_id, mark, _EARLIER_LINES)
            letter_body = None
            if kind is SplitTargetKind.LETTER:
                letter_body = memory.thread_lines(conn, target_id)[0]
            report = intake.split_and_save(
                conn,
                target=f"{kind.value} {target_id}",
                place=new[0].place.value,
                utterances=new,
                earlier=earlier,
                letter_body=letter_body,
                mark=(kind, target_id, last_rowid),
            )
            total.statement_ids.extend(report.statement_ids)
            total.interpretation_count += report.interpretation_count
            total.question_count += report.question_count
            total.dropped_question_count += report.dropped_question_count
            total.dropped.extend(report.dropped)
            # 入らなかった言明は理由だけを残す。黙って失われると、原因が追えないため。本文は残さない。
            for dropped in report.dropped:
                db.log_job_note(conn, "入らなかった言明", f"{kind.value} {target_id}", dropped.reason)
    return total


def split_quietly(app, conversation_id: int) -> None:
    """会話の新しい発言を裏で分ける。"""
    _guarded(
        app,
        "言明への分割",
        f"会話 {conversation_id}",
        lambda conn: split_new(app, conn, SplitTargetKind.CONVERSATION, conversation_id),
    )


def summarize(conn: sqlite3.Connection, conversation_id: int) -> None:
    """閉じた会話の要約を作る。次の会話で思い出すために使う。"""
    row = conn.execute("SELECT summary FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None or row["summary"]:
        return
    self_user = db.self_user(conn)
    lines = memory.conversation_lines(conn, conversation_id, self_user)
    if not any(line.startswith(f"{self_user.display_name}：") for line in lines):
        return
    target = f"会話 {conversation_id}"
    try:
        result = ai.summarize_conversation(
            system=prompts.summary_system(self_user.display_name), text="\n".join(lines)
        )
    except twin._SPENT_ERRORS as error:
        twin._log_spent(conn, "会話の要約", error, target)
        raise
    twin._log_result(conn, "会話の要約", result, target)
    with conn:
        conn.execute(
            "UPDATE conversations SET summary = ? WHERE id = ?", (result.value.summary, conversation_id)
        )


def find_candidates(conn: sqlite3.Connection, conversation_id: int, moment: str) -> None:
    """本人の発言から、話に出てきた人の候補を抜き出す。決めるのは本人なので、候補の表にだけ入れる。

    発言にその呼び方が本当に出てくるものだけを残す。AI が作った名前を候補にしないため。
    """
    self_user = db.self_user(conn)
    utterances = [
        utterance
        for utterance in splitter.load_utterances(conn, conversation_id=conversation_id)
        if utterance.speaker_user_id == self_user.id
    ]
    if not utterances:
        return
    people = db.list_users(conn)
    known = sorted({name for person in people for name in (person.name, person.display_name, person.relation) if name})
    past = conn.execute("SELECT name FROM person_candidates").fetchall()
    known_names = set(known) | {row["name"] for row in past}
    numbered = "\n".join(f"[{index}] {utterance.text}" for index, utterance in enumerate(utterances, start=1))
    target = f"会話 {conversation_id}"
    try:
        result = ai.person_candidates(
            system=prompts.person_candidates_system(self_user.display_name),
            text=prompts.people_user_text(sorted(known_names), numbered),
        )
    except twin._SPENT_ERRORS as error:
        twin._log_spent(conn, "人の候補", error, target)
        raise
    twin._log_result(conn, "人の候補", result, target)
    with conn:
        for person in result.value.people:
            name = person.name.strip()
            if not 1 <= person.utterance_number <= len(utterances):
                continue
            evidence = utterances[person.utterance_number - 1]
            if not name or name not in evidence.text or name in known_names:
                continue
            known_names.add(name)
            conn.execute(
                """
                INSERT INTO person_candidates (name, relation, relative_kind, related_to_name, is_deceased,
                    gender, evidence_utterance_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    person.relation or None,
                    person.relative_kind.value,
                    person.related_to,
                    int(person.is_deceased),
                    person.gender.value,
                    evidence.id,
                    CandidateStatus.PENDING.value,
                    moment,
                ),
            )


def after_close(app, conversation_id: int) -> None:
    """会話を閉じたあとの処理。1つが失敗しても、ほかは流す。"""
    target = f"会話 {conversation_id}"
    moment = clock.now(app).isoformat()
    _guarded(
        app,
        "言明への分割",
        target,
        lambda conn: split_new(app, conn, SplitTargetKind.CONVERSATION, conversation_id),
    )
    _guarded(app, "会話の要約", target, lambda conn: summarize(conn, conversation_id))
    _guarded(app, "人の候補", target, lambda conn: find_candidates(conn, conversation_id, moment))


def after_group_message(app, room_id: int, conversation_id: int, message_id: int) -> None:
    """グループの発言のあとの処理。新しい分を言明に分け、必要ならツインが書く。"""
    target = f"部屋 {room_id}"

    def _twin(conn: sqlite3.Connection) -> None:
        room = room_store.get_room(conn, room_id)
        presence = room_store.active_presence(conn, room_id)
        # 待つ間にはずされていたら書かない
        if presence is None or presence.conversation_id != conversation_id:
            return
        app.state.thinking_rooms.add(room_id)
        try:
            with _lock_for(app, f"部屋:{room_id}"):
                twin.group_turn(
                    conn,
                    room=room,
                    conversation_id=conversation_id,
                    trigger=room_store.get_message(conn, message_id),
                    now=clock.now(app),
                )
        finally:
            app.state.thinking_rooms.discard(room_id)

    _guarded(app, "グループのツイン", target, _twin)
    split_quietly(app, conversation_id)


def after_comment(app, letter_id: int, author_user_id: int) -> None:
    """コメントのあとの処理。ツインの問いを書き、新しい分を言明に分ける。"""
    target = f"便り {letter_id}"

    def _twin(conn: sqlite3.Connection) -> None:
        with _lock_for(app, f"スレッド:{letter_id}"):
            twin.thread_turn(conn, letter_id=letter_id, author_user_id=author_user_id, now=clock.now(app))

    _guarded(app, "スレッドの問い", target, _twin)
    _guarded(
        app,
        "言明への分割",
        target,
        lambda conn: split_new(app, conn, SplitTargetKind.LETTER, letter_id),
    )


def catch_up(app) -> None:
    """未処理を流し直す。python -m app の起動のときにだけ呼ぶ。テストが本物の API を呼ばないようにするため。"""
    conn = db.connect(app.state.db_path)
    try:
        today = clock.now(app).date().isoformat()
        moment = clock.now(app).isoformat()
        twin_room = room_store.twin_room_of(conn)
        if twin_room is not None:
            stale = conn.execute(
                "SELECT id FROM conversations WHERE room_id = ? AND closed_at IS NULL AND conversation_date <> ?",
                (twin_room.id, today),
            ).fetchall()
            for row in stale:
                room_store.close_conversation(conn, row["id"], moment)
            closed = conn.execute(
                "SELECT id, summary FROM conversations WHERE room_id = ? AND closed_at IS NOT NULL",
                (twin_room.id,),
            ).fetchall()
            for row in closed:
                mark = splitter.read_mark(conn, SplitTargetKind.CONVERSATION, row["id"])
                new, _ = splitter.load_after_mark(conn, SplitTargetKind.CONVERSATION, row["id"], mark)
                needs_split = any(not utterance.speaker_is_twin for utterance in new)
                if needs_split or (new and not row["summary"]) or (mark == 0 and not row["summary"]):
                    after_close(app, row["id"])
        groups = conn.execute(
            """
            SELECT c.id FROM conversations c JOIN rooms r ON r.id = c.room_id WHERE r.kind = ?
            """,
            (RoomKind.GROUP.value,),
        ).fetchall()
        for row in groups:
            split_quietly(app, row["id"])
        letters = conn.execute("SELECT id FROM letters").fetchall()
        for row in letters:
            _guarded(
                app,
                "言明への分割",
                f"便り {row['id']}",
                lambda c, letter_id=row["id"]: split_new(app, c, SplitTargetKind.LETTER, letter_id),
            )
    finally:
        conn.close()
