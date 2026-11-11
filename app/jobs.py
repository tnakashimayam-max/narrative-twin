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

app.state.job_runner は、ふつうは None で、その場で順に流す。サーバーはこの動きだけを使う。
架空の10年分を流す道具（fiction/run_script.py）は、流している間だけ受け手を入れて、裏で流せる分を渡す。
次の日の会話が読む要約は本線で待ち、言明への分割、人の候補、スレッドの問いを渡す。受け手があるときは、
AI の一時の失敗を待ってやり直すかどうかも受け手が決める。

関連するテスト：tests/test_twin.py、tests/test_rooms.py、tests/test_timeline.py、tests/test_ecomap.py、
tests/test_run_script_jobs.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import nullcontext
from datetime import datetime

from app import ai, clock, db, devlog, heart, intake, ledger, memory, prompts, room_store, splitter, twin
from app.schemas import CandidateStatus, HeartLevel, RoomKind, SplitTargetKind

# 1回の処理で分け直す回数の上限。発言が届き続けても止まるようにする。
# 1回に渡す発言には上限があるので（splitter._SPLIT_MAX_LINES）、長い会話は数回に分かれる。
_MAX_ROUNDS = 10
_EARLIER_LINES = 10


def _lock_for(app, key: str) -> threading.Lock:
    with app.state.job_locks_guard:
        return app.state.job_locks.setdefault(key, threading.Lock())


def thread_key(letter_id: int) -> str:
    """スレッドの裏の処理の名札。次のコメントの前に、同じ便りの処理が終わるのを待つために使う。"""
    return f"便り:{letter_id}"


def _guarded(app, job: str, target: str, work) -> None:
    """裏の処理を動かす。失敗しても、ほかの処理を止めず、記録に残す。

    受け手があるときは、受け手が「やり直す」と決めた失敗を待ってやり直す。やり直す分は失敗の表に入れない。
    やり直しても印は同じ発言から始まるので、言明は重ならない。止める途中なら何もせずに戻る。
    印が進まないので、続きから流すときに拾う。
    """
    runner = app.state.job_runner
    log = app.state.devlog
    started = time.monotonic()
    conn = db.connect(app.state.db_path)
    tries = 0
    # 別のスレッドから呼ばれることがあるので、ログの行き先をここで決め直す
    with devlog.use(log):
        try:
            while True:
                if runner is not None and not runner.wait_for_api():
                    return
                try:
                    work(conn)
                except Exception as error:  # noqa: BLE001  裏の処理の失敗は、どんな種類でも記録して続ける
                    log.write(
                        job,
                        outcome=devlog.FAILED,
                        error=devlog.error_name(error),
                        ms=devlog.since(started),
                        target=target,
                        where=devlog.where(error),
                    )
                    tries += 1
                    if runner is not None and runner.retry_after_failure(job, target, error, tries):
                        continue
                    try:
                        db.log_job_failure(conn, job, target, error)
                    except sqlite3.Error:
                        pass
                else:
                    log.write(job, outcome=devlog.SUCCESS, ms=devlog.since(started), target=target)
                return
        finally:
            conn.close()


def split_new(app, conn: sqlite3.Connection, kind: SplitTargetKind, target_id: int) -> splitter.SplitReport:
    """印より後の発言を言明に分ける。人の発言がなければ AI を呼ばない。"""
    total = splitter.SplitReport()
    runner = app.state.job_runner
    with _lock_for(app, f"{kind.value}:{target_id}"):
        for _ in range(_MAX_ROUNDS):
            # 長い会話は何回かに分けて AI を呼ぶので、全体で待っている間は次の回へ進まない
            if runner is not None and not runner.wait_for_api():
                break
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
            if report.dropped:
                devlog.record(
                    "言明への分割",
                    outcome=devlog.STATEMENT_DROPPED,
                    text={"理由": [dropped.reason for dropped in report.dropped]},
                )
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


def _stem(name: str) -> str:
    """呼び方の違いを除く。「美穂」と「美穂さん」を同じ人として見るため。"""
    return re.sub(r"(さん|様|さま|ちゃん|くん|君)$", "", name.strip())


def _known_names(conn: sqlite3.Connection) -> set[str]:
    """登録した人と、これまでの候補の呼び方を返す。"""
    people = db.list_users(conn)
    known = {name for person in people for name in (person.name, person.display_name, person.relation) if name}
    # 候補の続柄も入れる。「和子（妻）」が候補にいるのに「妻」を別の人として出さないため。
    past = conn.execute("SELECT name, relation FROM person_candidates").fetchall()
    return {_stem(value) for value in [*known, *(v for row in past for v in (row["name"], row["relation"]))] if value}


def find_candidates(conn: sqlite3.Connection, conversation_id: int, moment: str) -> None:
    """本人の発言から、話に出てきた人の候補を抜き出す。決めるのは本人なので、候補の表にだけ入れる。

    発言にその呼び方が本当に出てくるものだけを残す。AI が作った名前を候補にしないため。
    保存の前に、書き込みの鍵を取ってから読み直す。AI を待つ間に、別の裏の処理が同じ人を入れていることがあるため。
    """
    self_user = db.self_user(conn)
    utterances = [
        utterance
        for utterance in splitter.load_utterances(conn, conversation_id=conversation_id)
        if utterance.speaker_user_id == self_user.id
    ]
    if not utterances:
        return
    known_names = _known_names(conn)
    numbered = "\n".join(
        f"[{index}] {splitter.speech(utterance.text)}" for index, utterance in enumerate(utterances, start=1)
    )
    # AI に渡すのは発言に出てくる呼び方だけ。登録した全員の名前と続柄を渡さないため。照合は全員で行う。
    named_here = sorted(name for name in known_names if name in numbered)
    target = f"会話 {conversation_id}"
    try:
        result = ai.person_candidates(
            system=prompts.person_candidates_system(self_user.display_name),
            text=prompts.people_user_text(named_here, numbered),
        )
    except twin._SPENT_ERRORS as error:
        twin._log_spent(conn, "人の候補", error, target)
        raise
    twin._log_result(conn, "人の候補", result, target)
    with ledger.atomic(conn):
        known_names = _known_names(conn)
        for person in result.value.people:
            name = person.name.strip()
            if not 1 <= person.utterance_number <= len(utterances):
                continue
            evidence = utterances[person.utterance_number - 1]
            if not name or name not in evidence.text or _stem(name) in known_names:
                continue
            known_names.add(_stem(name))
            if person.relation:
                known_names.add(_stem(person.relation))
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


def read_heart(conn: sqlite3.Connection, moment) -> None:
    """その月の本人の言葉を読み直し、新しい版を足す。次の会話の返事が「ここ最近の読み」として読む（app/heart.py）。

    目次に固め直すのは道具（fiction/heart_reread.py）で行う。会話を閉じるたびに固めると、費用が読みの数倍になるため。
    """
    request = heart.month_request(conn, moment.date().isoformat()[:7])
    if request is None:
        return
    result = ai.heart_month(system=request.system, text=request.text)
    with ledger.atomic(conn):
        db.log_ai_call(
            conn,
            role=heart.ROLE_MONTH,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=f"月 {request.period}",
        )
        heart.save_month(
            conn,
            request,
            result.value,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            generation=heart.next_generation(conn, HeartLevel.MONTH),
            moment=moment,
        )


def after_close(app, conversation_id: int) -> None:
    """会話を閉じたあとの処理。1つが失敗しても、ほかは流す。

    受け手があるときは、要約だけをここで待つ。次の日の会話が読むため。分割と人の候補は受け手に渡す。
    人の候補を先に流すのは、途中で落ちても「印が進んでいない会話」を見れば両方まだだと分かるため。
    候補の保存は同じ人を重ねないので、流し直しても増えない。
    """
    target = f"会話 {conversation_id}"
    moment = clock.now(app)

    def _split(conn: sqlite3.Connection) -> None:
        split_new(app, conn, SplitTargetKind.CONVERSATION, conversation_id)

    def _people(conn: sqlite3.Connection) -> None:
        find_candidates(conn, conversation_id, moment.isoformat())

    def _summary(conn: sqlite3.Connection) -> None:
        summarize(conn, conversation_id)

    runner = app.state.job_runner
    if runner is None:
        _guarded(app, "言明への分割", target, _split)
        _guarded(app, "会話の要約", target, _summary)
        _guarded(app, "人の候補", target, _people)
        _guarded(app, "心の読み", target, lambda conn: read_heart(conn, moment))
        return
    _guarded(app, "会話の要約", target, _summary)

    def _behind() -> None:
        _guarded(app, "人の候補", target, _people)
        _guarded(app, "言明への分割", target, _split)

    runner.submit(f"会話:{conversation_id}", moment, _behind)


def after_group_message(app, room_id: int, conversation_id: int, message_id: int) -> None:
    """グループの発言のあとの処理。新しい分を言明に分け、必要ならツインが書く。"""
    target = f"部屋 {room_id}"
    may_ask = app.state.twin_may_ask

    def _twin(conn: sqlite3.Connection) -> None:
        # 問わない日は、グループのツインも書かない。答える人がいないため（fiction/answer_plan.py）
        if not may_ask:
            return
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
    """コメントのあとの処理。ツインの問いを書き、新しい分を言明に分ける。

    受け手があるときは、まとめて渡す。問いの日付は、渡したときの時刻で止めた clock.now で決まる。
    """
    target = f"便り {letter_id}"
    # 裏で動く前に読む。動くころには、流す道具が次の行へ進んでいるため
    may_ask = app.state.twin_may_ask

    def _twin(conn: sqlite3.Connection) -> None:
        with _lock_for(app, f"スレッド:{letter_id}"):
            twin.thread_turn(conn, letter_id=letter_id, author_user_id=author_user_id, now=clock.now(app), ask=may_ask)

    def _flow() -> None:
        _guarded(app, "スレッドの問い", target, _twin)
        _guarded(
            app,
            "言明への分割",
            target,
            lambda conn: split_new(app, conn, SplitTargetKind.LETTER, letter_id),
        )

    runner = app.state.job_runner
    if runner is None:
        _flow()
    else:
        runner.submit(thread_key(letter_id), clock.now(app), _flow)


def _marks(conn: sqlite3.Connection, kind: SplitTargetKind) -> dict[int, int]:
    rows = conn.execute(
        "SELECT target_id, last_utterance_rowid FROM split_marks WHERE target_kind = ?", (kind.value,)
    ).fetchall()
    return {row["target_id"]: row["last_utterance_rowid"] for row in rows}


def _last_spoken(conn: sqlite3.Connection, column: str) -> dict[int, tuple[int, int]]:
    """会話か便りごとに、いちばん新しい発言と、いちばん新しい人の発言の rowid を返す。

    10年分では会話が数千になるので、1件ずつ読まず、発言の表を1回だけ読む。
    """
    rows = conn.execute(
        f"""
        SELECT {column} AS target, MAX(rowid) AS last_any,
               MAX(CASE WHEN speaker_is_twin = 0 THEN rowid END) AS last_person
        FROM utterances WHERE {column} IS NOT NULL GROUP BY {column}
        """
    ).fetchall()
    return {row["target"]: (row["last_any"], row["last_person"] or 0) for row in rows}


def unsplit_letters(conn: sqlite3.Connection) -> list[int]:
    """まだ言明に分けていない人のコメントがある便りの番号を返す。"""
    marks = _marks(conn, SplitTargetKind.LETTER)
    spoken = _last_spoken(conn, "letter_id")
    return [letter_id for letter_id, (_, person) in spoken.items() if person > marks.get(letter_id, 0)]


def unsplit_conversations(conn: sqlite3.Connection) -> list[int]:
    """部屋の閉じた会話のうち、まだ言明に分けていない人の発言がある会話の番号を返す。"""
    marks = _marks(conn, SplitTargetKind.CONVERSATION)
    spoken = _last_spoken(conn, "conversation_id")
    rows = conn.execute("SELECT id FROM conversations WHERE room_id IS NOT NULL AND closed_at IS NOT NULL").fetchall()
    return [row["id"] for row in rows if spoken.get(row["id"], (0, 0))[1] > marks.get(row["id"], 0)]


def _at_close(app, closed_at: str | None):
    """受け手があるときは、閉じた時刻で流し直す。あとから拾っても、候補の時刻がずれないようにするため。"""
    if app.state.job_runner is None or not closed_at:
        return nullcontext()
    return clock.frozen(datetime.fromisoformat(closed_at))


def catch_up(app) -> None:
    """未処理を流し直す。python -m app の起動のときと、記録を流す道具の続きのときにだけ呼ぶ。

    テストが本物の API を呼ばないよう、アプリを作るときには呼ばない。
    印を見て、未処理の分だけ AI を呼ぶ。10年分でも、全部の会話を AI に送り直さない。
    """
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
                "SELECT id, summary, closed_at FROM conversations WHERE room_id = ? AND closed_at IS NOT NULL",
                (twin_room.id,),
            ).fetchall()
            marks = _marks(conn, SplitTargetKind.CONVERSATION)
            spoken = _last_spoken(conn, "conversation_id")
            for row in closed:
                mark = marks.get(row["id"], 0)
                last_any, last_person = spoken.get(row["id"], (0, 0))
                needs_split = last_person > mark
                has_new = last_any > mark
                if needs_split or (has_new and not row["summary"]) or (mark == 0 and not row["summary"]):
                    with _at_close(app, row["closed_at"]):
                        after_close(app, row["id"])
        groups = conn.execute(
            """
            SELECT c.id FROM conversations c JOIN rooms r ON r.id = c.room_id WHERE r.kind = ?
            """,
            (RoomKind.GROUP.value,),
        ).fetchall()
        marks = _marks(conn, SplitTargetKind.CONVERSATION)
        spoken = _last_spoken(conn, "conversation_id")
        for row in groups:
            if spoken.get(row["id"], (0, 0))[1] > marks.get(row["id"], 0):
                split_quietly(app, row["id"])
        for letter_id in unsplit_letters(conn):
            _guarded(
                app,
                "言明への分割",
                f"便り {letter_id}",
                lambda c, letter_id=letter_id: split_new(app, c, SplitTargetKind.LETTER, letter_id),
            )
    finally:
        conn.close()
