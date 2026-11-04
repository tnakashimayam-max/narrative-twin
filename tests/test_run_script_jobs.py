"""script.jsonl を流す道具の、裏の処理のテスト。

役割：裏の処理を待たずに進めても、記録の日付と順番が崩れず、欠けず、重ならないことを確かめる。
      同時の数の上げ下げ、API の上限のときの待ち方、落ちたあとの続きも確かめる。
4つの極：1. 書く・話す、2. 分かち合う、3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、5（元の記録を消さない）、8（人物と記録はすべて架空）、9（本人の言葉は一字も変えない）。

本物の Claude API は呼ばない。通信だけを差し替える。待ちは差し替えて、本当には待たない。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import ai, clock, config, db, jobs, ledger
from app.main import create_app
from app.schemas import SplitTargetKind
from fiction import run_script
from fiction.run_script import JobRunner, Line, Progress, StopRun, catch_up_for_resume, read_api_failure, run_lines
from tests.fake_claude import RoleClaude, _user_text, message, role_of

_NUMBERED = re.compile(r"^\[(\d+)\] (.+?)（(本人|家族|縁のある人|専門職)）：(.*)$")
_SOURCES = {"本人": "本人の言葉", "家族": "家族の証言", "縁のある人": "縁のある人の証言"}


class EchoClaude(RoleClaude):
    """人の発言をすべて、そのまま言明にして返す。役割ごとの失敗と待ちを差し込める。

    同じ入力なら同じ答えになるので、裏の処理の順番が変わっても言明の数を比べられる。
    """

    def __init__(self) -> None:
        super().__init__()
        self.answers["不明"] = {"before": "庭を見ています。"}
        self.failures: dict[str, list] = {}
        self.always_fail: dict[str, tuple] = {}
        self.delay: dict[str, float] = {}
        self.hold: set[str] = set()
        self.release = threading.Event()
        self.release_word: str | None = None
        self.before_call = None
        self.refused: set[str] = set()
        self._guard = threading.Lock()

    def _failure_for(self, role: str):
        with self._guard:
            if role in self.always_fail:
                return self.always_fail[role]
            queue = self.failures.get(role)
            return queue.pop(0) if queue else None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        role = role_of(body)
        if self.before_call is not None:
            self.before_call(role)
        if self.release_word and self.release_word in _user_text(body):
            self.release.set()
        if role in self.hold:
            assert self.release.wait(10), "待ちが解けませんでした"
        if role in self.delay:
            time.sleep(self.delay[role])
        failure = self._failure_for(role)
        if failure is not None:
            self.requests.append(request)
            status, headers = failure
            return httpx2.Response(status, headers=headers, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        if role in self.refused:
            self.requests.append(request)
            return httpx2.Response(200, json=message("", stop_reason="refusal", model=body["model"]))
        if role == "言明への分割":
            self.requests.append(request)
            return httpx2.Response(200, json=message(json.dumps(_echo(body), ensure_ascii=False), model=body["model"]))
        return super().__call__(request)


def _echo(body: dict) -> dict:
    statements = []
    section = _user_text(body).split("## 発言", 1)[1]
    for line in section.splitlines():
        found = _NUMBERED.match(line)
        if found is None:
            continue
        number, speaker, role, text = found.groups()
        statements.append(
            {
                "source": _SOURCES[role],
                "subject": "誠一さん",
                "speaker": speaker,
                "body": text,
                "period": "今",
                "theme": "好きなこと",
                "petals": [],
                "utterance_numbers": [int(number)],
                "interpretations": [],
                "confirm_question": None,
            }
        )
    return {"statements": statements}


class FakeTimer:
    """本当には待たない時計。待った秒数を覚えて、その分だけ時刻を進める。"""

    def __init__(self) -> None:
        self.at = 1000.0
        self.slept: list[float] = []
        self._guard = threading.Lock()

    def now(self) -> float:
        with self._guard:
            return self.at

    def sleep(self, _stop: threading.Event, seconds: float) -> None:
        with self._guard:
            self.slept.append(seconds)
            self.at += seconds

    def forward(self, seconds: float) -> None:
        with self._guard:
            self.at += seconds


@pytest.fixture
def claude(monkeypatch):
    fake = EchoClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def world(tmp_path, claude):
    folder = tmp_path / "流し込み"
    folder.mkdir()
    deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
    app = create_app(deployment=deployment)
    return TestClient(app), deployment.db_path, Progress(folder / "progress.json")


def lines_of(rows) -> list[Line]:
    return [Line.model_validate(row) for row in rows]


def flow(world, rows, **options):
    client, db_path, progress = world
    return run_lines(client, lines_of(rows), progress, db_path=db_path, **options)


def resume(world, rows, **options):
    """同じ命令で続きから流すときの順番。"""
    client, db_path, progress = world
    timer = options.get("timer")
    catch_up_for_resume(client, lines_of(rows), progress, workers=options.get("workers", 0), timer=timer)
    return run_lines(client, lines_of(rows), progress, db_path=db_path, **options)


def say(day, minute, text, **fields):
    return {"at": f"{day}T08:{minute:02d}", "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text, **fields}


def close(day, minute=30):
    return {"at": f"{day}T08:{minute:02d}", "kind": "閉じる", "room": "ツイン", "by": "誠一さん"}


def letter(day, ref, minute=31):
    return {"at": f"{day}T08:{minute:02d}", "kind": "便り", "by": "誠一さん", "ref": ref, "body": f"{ref}の話です。", "share_with": "家族"}


def comment(day, ref, text, minute=40, by="美穂さん"):
    return {"at": f"{day}T08:{minute:02d}", "kind": "コメント", "by": by, "letter": ref, "text": text}


def day_of(number: int) -> str:
    return (datetime(2026, 4, 1) + timedelta(days=number)).date().isoformat()


def many_days(count: int) -> list[dict]:
    rows = []
    for number in range(count):
        day = day_of(number)
        rows += [
            say(day, 10, f"{number}日目の庭の話です。"),
            say(day, 11, f"{number}日目は縁側にいました。"),
            close(day),
            letter(day, f"便り{number}"),
            comment(day, f"便り{number}", f"{number}日目もまた帰ります。"),
        ]
    return rows


def query(db_path: Path, sql: str, *params) -> list[sqlite3.Row]:
    conn = db.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def people_utterances(db_path: Path) -> list[sqlite3.Row]:
    return query(db_path, "SELECT * FROM utterances WHERE speaker_is_twin = 0 ORDER BY rowid")


def assert_all_split(db_path: Path) -> None:
    """すべての人の発言が言明になり、印が最後まで進んでいる。"""
    conn = db.connect(db_path)
    try:
        assert jobs.unsplit_conversations(conn) == []
        assert jobs.unsplit_letters(conn) == []
    finally:
        conn.close()
    bodies = [row["body"] for row in query(db_path, "SELECT body FROM statements WHERE replaces_id IS NULL")]
    texts = [row["text"] for row in people_utterances(db_path)]
    assert sorted(bodies) == sorted(texts)


# ---- 日付と順番 ----


def test_裏の処理が日付の切り替えのあとに動いても_記録の日付は会話の日のまま(world, claude):
    _, db_path, _ = world
    first, second = "2026-04-12", "2026-04-20"
    claude.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
    claude.answers["人の候補"] = {"people": [{"name": "佐藤さん", "utterance_number": 1}]}
    # 次の日の会話の AI が呼ばれるまで、裏の処理を止めておく。1本にすると、スレッドの処理は日付が進んでから始まる
    claude.hold = {"言明への分割", "スレッドの問い", "人の候補"}
    claude.release_word = "次の日の話"
    rows = [
        say(first, 10, "佐藤さんと庭の話をしました。"),
        close(first),
        letter(first, "庭"),
        comment(first, "庭", "また帰ります。"),
        say(second, 10, "次の日の話です。"),
        close(second),
    ]

    flow(world, rows, workers=1)

    words = {row["body"]: row for row in query(db_path, "SELECT * FROM statements")}
    assert words["佐藤さんと庭の話をしました。"]["effective_date"] == first
    assert words["佐藤さんと庭の話をしました。"]["created_at"].startswith(first)
    assert words["また帰ります。"]["effective_date"] == first
    question = query(db_path, "SELECT * FROM comments WHERE is_twin_question = 1")[0]
    assert question["commented_at"].startswith(first)
    asked = query(db_path, "SELECT asked_on FROM question_logs WHERE letter_id IS NOT NULL")[0]
    assert asked["asked_on"] == first
    spoken = query(db_path, "SELECT spoken_at FROM utterances WHERE id = ?", question["utterance_id"])[0]
    assert spoken["spoken_at"].startswith(first)
    candidate = query(db_path, "SELECT created_at FROM person_candidates")[0]
    assert candidate["created_at"].startswith(first)


def test_次の日の会話は_裏の処理を待たずに前の日の要約を読める(world, claude):
    claude.answers["会話の要約"] = {"summary": "目印の要約です。"}
    # 要約を遅らせ、分割は止めておく。本線で待たなければ、次の日の会話に要約が間に合わない
    claude.delay["会話の要約"] = 0.3
    claude.hold = {"言明への分割", "人の候補"}
    claude.release_word = "次の日の話"
    first, second = "2026-04-12", "2026-04-13"
    rows = [say(first, 10, "庭の話です。"), close(first), say(second, 10, "次の日の話です。"), close(second)]

    flow(world, rows, workers=2)

    asked = [body for body in claude.bodies("ツインの会話") if "次の日の話" in _user_text(body)]
    assert "目印の要約です。" in _user_text(asked[0])


def test_スレッドの問いが次のコメントより前に入る(world, claude):
    _, db_path, _ = world
    day = "2026-04-12"
    claude.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
    # 問いを遅らせる。待たずに次のコメントを送ると、問いが後ろに入る
    claude.delay["スレッドの問い"] = 0.2
    rows = [
        say(day, 10, "庭の話です。"),
        close(day),
        letter(day, "庭"),
        comment(day, "庭", "一つ目のコメントです。", 40),
        comment(day, "庭", "二つ目のコメントです。", 41),
    ]

    flow(world, rows, workers=2)

    thread = query(db_path, "SELECT body, is_twin_question, commented_at FROM comments ORDER BY rowid")
    assert [row["body"] for row in thread] == ["一つ目のコメントです。", "庭の話を、もう少し聞かせていただけますか？", "二つ目のコメントです。"]
    times = [row["commented_at"] for row in thread]
    assert times == sorted(times)


def test_本人のコメントで_問いが答えられたことになる順番も崩れない(world, claude):
    _, db_path, _ = world
    day = "2026-04-12"
    claude.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
    claude.delay["スレッドの問い"] = 0.1
    rows = [
        say(day, 10, "庭の話です。"),
        close(day),
        letter(day, "庭"),
        comment(day, "庭", "一つ目のコメントです。", 40),
        comment(day, "庭", "本人の答えです。", 41, by="誠一さん"),
    ]

    flow(world, rows, workers=2)

    row = query(db_path, "SELECT result FROM question_logs WHERE letter_id IS NOT NULL")[0]
    assert row["result"] == "答えた"


def test_記録の順番は日付と時刻の順に崩れない(world, claude):
    _, db_path, _ = world
    claude.delay["言明への分割"] = 0.02

    flow(world, many_days(8), workers=4)

    # 本人の言葉の日付は、話した日と同じ
    spoken = {row["id"]: row for row in people_utterances(db_path)}
    for row in query(db_path, "SELECT * FROM statements"):
        utterance = spoken[json.loads(row["source_utterance_ids"])[0]]
        assert row["effective_date"] == utterance["spoken_at"][:10]
        assert row["created_at"] == utterance["spoken_at"]
    # 発言とコメントは、追記の順と時刻の順が同じ
    for sql, column in (
        ("SELECT sent_at FROM room_messages ORDER BY rowid", "sent_at"),
        ("SELECT commented_at FROM comments ORDER BY rowid", "commented_at"),
    ):
        values = [row[column] for row in query(db_path, sql)]
        assert values == sorted(values)
    # 画面が読む順（台帳の順）では、日付が前に戻らない
    conn = db.connect(db_path)
    try:
        days = [item.effective_date for item in ledger.current_statements(conn)]
    finally:
        conn.close()
    assert days == sorted(days)


# ---- 書き込みの重なり ----


def test_本数を増やして多数流しても_失敗の記録がなく全部の発言が分割済み(world, claude, monkeypatch):
    client, db_path, progress = world
    # すぐに上限まで上げて、書き込みを重ねる
    monkeypatch.setattr(run_script, "RAMP_SECONDS", 0)
    claude.delay["言明への分割"] = 0.002
    claude.delay["人の候補"] = 0.002
    # 番号を選ぶ読み取りと書き込みの間を広げる。鍵を先に取らないと、ここで別の書き込みが入って失敗する
    real_new_id = db.new_id

    def slow_new_id(connection, table):
        number = real_new_id(connection, table)
        time.sleep(0.002)
        return number

    monkeypatch.setattr(db, "new_id", slow_new_id)

    flow(world, many_days(25), workers=6)

    assert query(db_path, "SELECT job, message FROM job_failures") == []
    # ロックの失敗は待ってやり直すので、失敗の記録には残らない。数字で確かめる
    stats = json.loads((progress.path.parent / run_script.STATS_FILE).read_text(encoding="utf-8"))
    assert stats["lock_failures"] == 0
    assert_all_split(db_path)
    marks = query(db_path, "SELECT COUNT(*) AS total FROM split_marks")[0]["total"]
    assert marks == 50
    assert client.app.state.job_runner is None


def test_書き込みの処理は最初に鍵を取る(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    first = db.connect(path)
    second = sqlite3.connect(path, timeout=0)
    try:
        with ledger.atomic(first):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.execute("BEGIN IMMEDIATE")
    finally:
        first.close()
        second.close()


def test_読んだあとに別の書き込みが入っても_鍵を持っていれば待って書ける(tmp_path):
    """ふつうの BEGIN では、読んだあとの書き込みが待たずに失敗する（SQLITE_BUSY_SNAPSHOT）。"""
    path = tmp_path / "test.db"
    db.init_db(path)
    first = db.connect(path)
    second = db.connect(path)
    done = threading.Event()

    def write_later() -> None:
        with ledger.atomic(second):
            second.execute("INSERT INTO job_failures (job, target, message, failed_at) VALUES ('後', NULL, 'x', 'x')")
        done.set()

    try:
        with ledger.atomic(first):
            first.execute("SELECT COUNT(*) FROM job_failures").fetchone()
            thread = threading.Thread(target=write_later)
            thread.start()
            time.sleep(0.1)
            first.execute("INSERT INTO job_failures (job, target, message, failed_at) VALUES ('先', NULL, 'x', 'x')")
        thread.join(5)
    finally:
        first.close()
    second.close()
    assert done.is_set()


def test_AIの呼び出しの間は書き込みの鍵を持たない(world, claude):
    """鍵を持ったまま AI を待つと、ほかの書き込みがすべて止まる。裏の処理も順に流して確かめる。"""
    _, db_path, _ = world
    held = []
    seen = set()

    def check_lock(role: str) -> None:
        seen.add(role)
        probe = sqlite3.connect(db_path, timeout=0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
        except sqlite3.OperationalError:
            held.append(role)
        finally:
            probe.close()

    claude.before_call = check_lock
    claude.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
    claude.answers["グループのツイン"] = {"kind": "問い", "text": "庭の話を聞かせていただけますか？"}
    day = "2026-04-12"
    rows = [
        {"at": f"{day}T07:00", "kind": "部屋", "by": "誠一さん", "room": "将棋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": f"{day}T07:01", "kind": "ツインを入れる", "by": "誠一さん", "room": "将棋", "present": True},
        {"at": f"{day}T07:02", "kind": "発言", "room": "将棋", "by": "正雄さん", "text": "また指しましょう。"},
        {"at": f"{day}T07:03", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": None, "text": "庭の話です。"},
        {"at": f"{day}T07:04", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": "縁側にいます。"}},
        close(day),
        letter(day, "庭"),
        comment(day, "庭", "また帰ります。"),
        {"at": f"{day}T09:00", "kind": "整理", "by": "誠一さん"},
    ]

    flow(world, rows)

    # 裏の処理と本線の AI の呼び出しを、ひと通り通っていること
    assert seen >= {"ツインの会話", "今日はここまで", "言明への分割", "会話の要約", "人の候補", "スレッドの問い", "グループのツイン", "固める処理", "不明"}
    assert held == []


def test_同じ人を2つの裏の処理が候補にしても_1人だけ入る(world, claude):
    _, db_path, _ = world
    claude.answers["人の候補"] = {"people": [{"name": "佐藤さん", "relation": "友人", "utterance_number": 1}]}
    # 2つの処理が AI を待つ時間を重ねる
    barrier = threading.Barrier(2, timeout=5)

    def meet(role: str) -> None:
        if role == "人の候補":
            barrier.wait()

    claude.before_call = meet
    rows = [
        say("2026-04-12", 10, "佐藤さんと話しました。"),
        close("2026-04-12"),
        say("2026-04-13", 10, "佐藤さんにまた会いました。"),
        close("2026-04-13"),
    ]

    flow(world, rows, workers=2)

    assert [row["name"] for row in query(db_path, "SELECT name FROM person_candidates")] == ["佐藤さん"]


# ---- API の上限 ----


def error_429(retry_after: str | None = None, status: int = 429) -> ai.AIUnavailableError:
    """SDK を読み込まずに形だけ作る。本物の流れは、下の偽物の通信で確かめる。"""
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    cause = RuntimeError("x")
    cause.status_code = status
    cause.response = SimpleNamespace(headers=headers, status_code=status)
    error = ai.AIUnavailableError("x")
    error.__cause__ = cause
    return error


def test_retry_afterの秒数を読む():
    assert read_api_failure(error_429("7")) == (429, 7.0)
    assert read_api_failure(error_429("1.5")) == (429, 1.5)


def test_retry_afterが読めなくても動く():
    assert read_api_failure(error_429()) == (429, None)
    assert read_api_failure(error_429("Wed, 21 Oct 2026 07:28:00 GMT")) == (429, None)
    assert read_api_failure(error_429("-3")) == (429, None)
    assert read_api_failure(error_429("nan")) == (429, None)
    assert read_api_failure(ai.AIUnavailableError("応答のない失敗")) == (None, None)


def test_偽物がretry_after付きの429を返すと_その秒数を待ち_欠けない(world, claude):
    _, db_path, progress = world
    timer = FakeTimer()
    claude.failures["言明への分割"] = [(429, {"retry-after": "7"})]
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2, timer=timer)

    assert timer.slept == [7.0]
    assert_all_split(db_path)
    assert query(db_path, "SELECT * FROM job_failures") == []
    stats = json.loads((progress.path.parent / run_script.STATS_FILE).read_text(encoding="utf-8"))
    assert stats["rate_limited"] == 1
    assert stats["waited_seconds"] == 7.0


def test_retry_afterがなければ5秒から倍々で待つ(world, claude):
    _, db_path, _ = world
    timer = FakeTimer()
    claude.failures["言明への分割"] = [(503, {}), (429, {}), (529, {})]
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2, timer=timer)

    assert timer.slept == [5, 10, 20]
    assert_all_split(db_path)


def test_待ちの秒数は最大60秒まで():
    assert max(run_script.JOB_RETRY_SECONDS) == 60
    assert run_script.JOB_RETRY_SECONDS[:5] == (5, 10, 20, 40, 60)


def test_本数は2本から始めて_間隔ごとに1本ずつ上限まで上がる():
    timer = FakeTimer()
    runner = JobRunner(4, timer=timer)
    try:
        assert runner.current == 2
        timer.forward(run_script.RAMP_SECONDS - 1)
        runner.submit("a", datetime.now().astimezone(), lambda: None)
        assert runner.current == 2
        timer.forward(1)
        runner.submit("b", datetime.now().astimezone(), lambda: None)
        assert runner.current == 3
        timer.forward(run_script.RAMP_SECONDS)
        runner.submit("c", datetime.now().astimezone(), lambda: None)
        assert runner.current == 4
        timer.forward(run_script.RAMP_SECONDS)
        runner.submit("d", datetime.now().astimezone(), lambda: None)
        assert runner.current == 4
    finally:
        runner.close()


def test_上限が1本なら1本のまま():
    runner = JobRunner(1, timer=FakeTimer())
    try:
        assert runner.current == 1
    finally:
        runner.close()


@pytest.mark.parametrize("status", [429, 529])
def test_混んでいると言われたら本数が半分になり_また上がる(status):
    timer = FakeTimer()
    runner = JobRunner(4, timer=timer)
    try:
        for _ in range(2):
            timer.forward(run_script.RAMP_SECONDS)
            runner.submit("a", datetime.now().astimezone(), lambda: None)
        assert runner.current == 4

        assert runner.retry_after_failure("言明への分割", "会話 1", error_429("3", status), 1) is True
        assert runner.current == 2

        # 待っている間は上がらない
        timer.forward(2)
        runner.submit("b", datetime.now().astimezone(), lambda: None)
        assert runner.current == 2
        # 待ち終わってから、間隔ごとに上がる
        timer.forward(1 + run_script.RAMP_SECONDS)
        runner.submit("c", datetime.now().astimezone(), lambda: None)
        assert runner.current == 3
    finally:
        runner.close()


def test_同時に返った429では_本数を何度も下げない():
    timer = FakeTimer()
    runner = JobRunner(8, timer=timer)
    runner.current = 8
    try:
        runner.retry_after_failure("言明への分割", "会話 1", error_429("10"), 1)
        runner.retry_after_failure("言明への分割", "会話 2", error_429("10"), 1)
        assert runner.current == 4
        assert runner.stats.rate_limited == 2
        assert runner.stats.workers_lowered == 1
    finally:
        runner.close()


def test_本数は1本より下がらない():
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    runner.current = 1
    try:
        runner.retry_after_failure("言明への分割", "会話 1", error_429("1"), 1)
        assert runner.current == 1
    finally:
        runner.close()


def test_待っている間は_ほかの裏の処理も新しくAIを呼ばない(world):
    client, _, _ = world
    app = client.app
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    app.state.job_runner = runner
    called_at = []
    try:
        start = timer.now()
        runner.retry_after_failure("言明への分割", "会話 1", error_429("7"), 1)
        runner.submit(
            "ほかの処理",
            datetime.now().astimezone(),
            lambda: jobs._guarded(app, "人の候補", "会話 2", lambda _conn: called_at.append(timer.now())),
        )
        runner.drain()
    finally:
        app.state.job_runner = None
        runner.close()

    assert called_at == [start + 7]
    assert timer.slept == [7.0]


def test_本線も全体の待ちが終わるまで次の行へ進まない(world):
    client, _, progress = world
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    step = run_script.Runner(client, progress, job_runner=runner)
    try:
        runner.retry_after_failure("言明への分割", "会話 1", error_429("7"), 1)
        step._wait_for_jobs(Line.model_validate(say("2026-04-12", 10, "庭の話です。")))
    finally:
        runner.close()

    assert timer.slept == [7.0]


def test_本線のAIの一時の失敗でも_全体で待って送り直し_発言は増えない(world, claude):
    client, db_path, progress = world
    timer = FakeTimer()
    claude.failures["ツインの会話"] = [(429, {})]
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2, timer=timer)

    assert timer.slept == [run_script.RETRY_SECONDS[0]]
    assert [row["text"] for row in people_utterances(db_path)] == ["庭の話です。"]
    stats = json.loads((progress.path.parent / run_script.STATS_FILE).read_text(encoding="utf-8"))
    assert stats["other_transient"] == 1
    # 送り直しの間に時計が進んでも、発言の時刻は at のまま
    spoken = people_utterances(db_path)[0]["spoken_at"]
    assert spoken.startswith(f"{day}T08:10:00")


def test_待ちが上限を超えたら本線が待つ():
    runner = JobRunner(1, timer=FakeTimer())
    release = threading.Event()
    filled = threading.Event()
    at = datetime.now().astimezone()

    def fill() -> None:
        for number in range(run_script.QUEUE_PER_WORKER + 1):
            runner.submit(f"処理{number}", at, lambda: release.wait(5))
        filled.set()

    thread = threading.Thread(target=fill)
    thread.start()
    try:
        assert not filled.wait(0.3)
        release.set()
        assert filled.wait(5)
        runner.drain()
    finally:
        release.set()
        thread.join(5)
        runner.close()

    assert runner.stats.queue_max <= run_script.QUEUE_PER_WORKER


def test_同じ名札の処理が終わるまで待てる():
    runner = JobRunner(2, timer=FakeTimer())
    finished = []
    try:
        runner.submit("便り:1", datetime.now().astimezone(), lambda: (time.sleep(0.1), finished.append(1)))
        runner.wait_for("便り:1")
        assert finished == [1]
    finally:
        runner.close()


def test_ずっと失敗するときはStopRunで止まり_同じ命令の続きで全部入る(world, claude, monkeypatch):
    _, db_path, progress = world
    monkeypatch.setattr(run_script, "JOB_RETRY_SECONDS", (0, 0))
    claude.always_fail["言明への分割"] = (503, {})
    rows = many_days(3)

    with pytest.raises(StopRun, match="一時の失敗が続きました"):
        flow(world, rows, workers=2)

    # 発言は欠けていない。送った行の分だけ残っている
    before = len(people_utterances(db_path))
    assert before >= 1
    assert len(progress.digests) < len(rows)

    claude.always_fail.clear()
    resume(world, rows, workers=2)

    assert_all_split(db_path)
    assert len(progress.digests) == len(rows)
    texts = [row["text"] for row in people_utterances(db_path)]
    assert len(texts) == len(set(texts))
    assert len(texts) == 9


def test_要約がずっと失敗するときも止まり_続きで要約が入る(world, claude, monkeypatch):
    _, db_path, _ = world
    monkeypatch.setattr(run_script, "JOB_RETRY_SECONDS", (0, 0))
    claude.always_fail["会話の要約"] = (429, {})
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day), say("2026-04-13", 10, "次の日の話です。"), close("2026-04-13")]

    with pytest.raises(StopRun, match="会話の要約"):
        flow(world, rows, workers=2)

    claude.always_fail.clear()
    resume(world, rows, workers=2)

    summaries = [row["summary"] for row in query(db_path, "SELECT summary FROM conversations ORDER BY rowid")]
    assert summaries == ["要約です。", "要約です。"]
    assert_all_split(db_path)


@pytest.mark.parametrize("status", [400, 401])
def test_待っても直らない失敗はすぐに止まる(world, claude, status):
    timer = FakeTimer()
    claude.always_fail["言明への分割"] = (status, {})
    day = "2026-04-12"

    with pytest.raises(StopRun, match=str(status)):
        flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2, timer=timer)

    assert timer.slept == []


def test_APIキーがないときはすぐに止まる():
    runner = JobRunner(2, timer=FakeTimer())
    try:
        assert runner.retry_after_failure("言明への分割", "会話 1", config.MissingApiKeyError("x"), 1) is False
        assert "API キー" in runner.stop_reason
    finally:
        runner.close()


def test_長すぎるretry_afterは待たずに止める():
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    try:
        wait = str(run_script.MAX_RETRY_AFTER_SECONDS + 1)
        assert runner.retry_after_failure("言明への分割", "会話 1", error_429(wait), 1) is False
        assert runner.stop_reason is not None
        assert timer.slept == []
    finally:
        runner.close()


def test_断られた答えはやり直さず_記録して続ける(world, claude):
    _, db_path, _ = world
    timer = FakeTimer()
    claude.refused.add("言明への分割")
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2, timer=timer)

    assert timer.slept == []
    assert [row["job"] for row in query(db_path, "SELECT job FROM job_failures")] == ["言明への分割"]
    # 発言は残るので、あとから作り直せる
    assert [row["text"] for row in people_utterances(db_path)] == ["庭の話です。"]


# ---- 落ちたあとの続き ----


class Killed(BaseException):
    """プロセスが落ちたことの代わり。止めるときの後片づけに拾われない例外にする。"""


def crash_rows() -> list[dict]:
    first, second = "2026-04-12", "2026-04-13"
    return [
        {"at": f"{first}T07:00", "kind": "人", "by": "誠一さん", "name": "健太くん", "role": "家族", "relation": "孫"},
        {"at": f"{first}T07:01", "kind": "段階", "by": "誠一さん", "stage": "要支援"},
        say(first, 10, "庭のつつじの話です。"),
        say(first, 11, "和子の話は今はしません。"),
        close(first),
        {"at": f"{first}T08:31", "kind": "確かめ", "by": "誠一さん", "match": "庭のつつじ"},
        {"at": f"{first}T08:32", "kind": "本人だけにする", "by": "誠一さん", "text": "和子の話は今はしません。"},
        {"at": f"{first}T08:33", "kind": "推測", "by": "誠一さん", "match": "庭のつつじ", "text": "つつじに思い出があるのかもしれません。"},
        {"at": f"{first}T08:34", "kind": "便り", "by": "誠一さん", "ref": "つつじ", "body": "つつじの話です。", "share_with": "家族"},
        comment(first, "つつじ", "また帰ります。", 40),
        comment(first, "つつじ", "健太も行きます。", 41),
        {"at": f"{first}T17:00", "kind": "今の様子", "by": "美穂さん", "text": "電話の声は元気そうでした。"},
        say(second, 10, "次の日の話です。"),
        say(second, 11, "縁側にいました。"),
        close(second),
    ]


def crash_at(world, rows, number: int, *, after: bool, monkeypatch) -> None:
    """number 行目でプロセスが落ちたことにする。待ちの中の処理は失われ、最後の保存も行われない。"""
    client, db_path, progress = world
    state = {"dead": False}
    # 待ちの中の処理。待つ行では流れ、落ちた瞬間に残っていた分だけが失われる
    held: list[tuple] = []

    def flush() -> None:
        while held:
            at, work = held.pop(0)
            with clock.frozen(at):
                work()

    def save(self) -> None:
        if not state["dead"]:
            real_save(self)

    def run_line(self, line_number, line, key, at=None) -> None:
        if line_number == number and not after:
            state["dead"] = True
            raise Killed
        real_run_line(self, line_number, line, key, at)
        if line_number == number:
            state["dead"] = True
            raise Killed

    def submit(self, _key, at, work) -> bool:
        held.append((at, work))
        return True

    def drain(self) -> None:
        flush()
        real_drain(self)

    def wait_for(self, _key) -> None:
        flush()

    real_save = Progress.save
    real_run_line = run_script.Runner.run_line
    real_submit = JobRunner.submit
    real_drain = JobRunner.drain
    real_wait_for = JobRunner.wait_for
    monkeypatch.setattr(Progress, "save", save)
    monkeypatch.setattr(run_script.Runner, "run_line", run_line)
    monkeypatch.setattr(JobRunner, "submit", submit)
    monkeypatch.setattr(JobRunner, "drain", drain)
    monkeypatch.setattr(JobRunner, "wait_for", wait_for)
    with pytest.raises(Killed):
        run_lines(client, lines_of(rows), progress, db_path=db_path, workers=2)
    monkeypatch.setattr(Progress, "save", real_save)
    monkeypatch.setattr(run_script.Runner, "run_line", real_run_line)
    monkeypatch.setattr(JobRunner, "submit", real_submit)
    monkeypatch.setattr(JobRunner, "drain", real_drain)
    monkeypatch.setattr(JobRunner, "wait_for", real_wait_for)


@pytest.mark.parametrize("after", [False, True], ids=["送る前", "送ったあと"])
@pytest.mark.parametrize("number", range(1, 16))
def test_待ちの中の処理を失って流し直しても_重ならず欠けない(world, claude, monkeypatch, number, after):
    client, db_path, progress = world
    claude.answers["スレッドの問い"] = {"question": "つつじの話を、もう少し聞かせていただけますか？"}
    rows = crash_rows()

    crash_at(world, rows, number, after=after, monkeypatch=monkeypatch)
    reloaded = Progress.load(progress.path)
    world = (client, db_path, reloaded)
    resume(world, rows, workers=2)

    assert len(reloaded.digests) == len(rows)
    spoken = people_utterances(db_path)
    # 発言は重ならず、欠けない
    expected = [row.get("text") for row in rows if row["kind"] in ("発言", "コメント", "今の様子")]
    texts = [row["text"] for row in spoken]
    assert sorted(texts) == sorted(expected)
    # 言明も重ならず、欠けない。今の様子の言明は別の道で入る
    current = query(
        db_path,
        """
        SELECT body, source, confirm_status FROM statements
        WHERE id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
        """,
    )
    bodies = [row["body"] for row in current]
    assert sorted(bodies) == sorted([*expected, "つつじに思い出があるのかもしれません。"])
    states = {row["body"]: row["confirm_status"] for row in current}
    assert states["庭のつつじの話です。"] == "確認済み"
    assert states["和子の話は今はしません。"] == "お休み"
    # スレッドの問いも1つだけ入る
    assert len(query(db_path, "SELECT 1 FROM comments WHERE is_twin_question = 1")) == 1
    assert len(query(db_path, "SELECT 1 FROM letters")) == 1
    # 人生の段階の行も重ならない
    assert len(query(db_path, "SELECT 1 FROM conversations WHERE trigger = ?", run_script.STAGE_TRIGGER)) == 1
    # 1つの会話が別れていない
    assert len(query(db_path, "SELECT 1 FROM conversations WHERE room_id IS NOT NULL")) == 2
    assert all(row["closed_at"] for row in query(db_path, "SELECT closed_at FROM conversations WHERE room_id IS NOT NULL"))
    assert_story_turns_once(reloaded)


def assert_story_turns_once(progress: Progress) -> None:
    """書き出しのログにも、同じ発言を2回入れない。"""
    for entry in progress.story.values():
        spoken = [turn["text"] for turn in entry["turns"] if turn["speaker"] == "誠一さん"]
        assert len(spoken) == len(set(spoken))
        comments = [item["text"] for item in entry["comments"]]
        assert len(comments) == len(set(comments))


def test_失われたスレッドの問いは流し直しで入り_日付はコメントの日のまま(world, claude, monkeypatch):
    client, db_path, progress = world
    claude.answers["スレッドの問い"] = {"question": "つつじの話を、もう少し聞かせていただけますか？"}
    first, second = "2026-04-12", "2026-04-20"
    # コメントを1つだけにする。次のコメントがあると、その処理が問いを書いてしまい、失われたことが分からない
    rows = [
        say(first, 10, "つつじの話です。"),
        close(first),
        letter(first, "つつじ"),
        comment(first, "つつじ", "また帰ります。"),
        say(second, 10, "次の日の話です。"),
        close(second),
    ]

    crash_at(world, rows, 5, after=True, monkeypatch=monkeypatch)
    assert query(db_path, "SELECT 1 FROM comments WHERE is_twin_question = 1") == []
    resume((client, db_path, Progress.load(progress.path)), rows, workers=2)

    question = query(db_path, "SELECT commented_at FROM comments WHERE is_twin_question = 1")
    assert len(question) == 1
    assert question[0]["commented_at"].startswith(first)


def test_保存より先の日の会話の途中で落ちても_会話は分かれない(world, claude, monkeypatch):
    client, db_path, progress = world
    first, second = "2026-04-12", "2026-04-13"
    # 発言と閉じるだけが続くので、全部の保存は入らない。落ちたときの続きは、前の日の行から始まる
    rows = [
        say(first, 10, "前の日の話です。"),
        close(first),
        say(second, 10, "次の日の話です。"),
        say(second, 11, "縁側にいました。"),
        close(second),
    ]

    crash_at(world, rows, 3, after=True, monkeypatch=monkeypatch)
    reloaded = Progress.load(progress.path)
    assert reloaded.digests == []
    resume((client, db_path, reloaded), rows, workers=2)

    conversations = query(db_path, "SELECT conversation_date, closed_at FROM conversations ORDER BY rowid")
    assert [row["conversation_date"] for row in conversations] == [first, second]
    assert all(row["closed_at"] for row in conversations)
    assert_all_split(db_path)


def test_続きから流すときは_未処理の会話だけをAIに送る(world, claude):
    client, db_path, progress = world
    rows = many_days(6)
    flow(world, rows, workers=2)
    conversation = query(db_path, "SELECT id FROM conversations WHERE room_id IS NOT NULL ORDER BY rowid LIMIT 1")[0]["id"]
    # 1件だけ未処理に戻す。印は書き換えないテストの中だけの操作
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM split_marks WHERE target_kind = ? AND target_id = ?",
                (SplitTargetKind.CONVERSATION.value, conversation),
            )
    finally:
        conn.close()
    claude.requests.clear()

    catch_up_for_resume(client, lines_of(rows), progress, workers=2)

    assert len(claude.bodies("言明への分割")) == 1
    assert len(claude.bodies("人の候補")) == 1
    assert claude.bodies("会話の要約") == []


def test_項目を足しても_これまでの行の印は変わらない():
    """段階の項目を足す前の道具で作った印。変わると、流し始めた分を続きから流せなくなり、発言の印もずれる。"""
    row = {"at": "2026-04-12T08:10", "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": "庭が好きなんです。"}

    assert run_script.line_digest(Line.model_validate(row)) == "7b615e21c5f2083c"


def test_前の版で止まった進み具合も_送ったかもしれない行が分かる(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps({"digests": ["a"], "sending": 2}), encoding="utf-8")

    assert Progress.load(path).sent_upto == 2


def test_送り始めた番号のファイルから_送ったかもしれない行が分かる(tmp_path):
    progress = Progress(tmp_path / "progress.json")
    progress.note_sending(3)
    progress.note_sending(4)
    (tmp_path / "progress.sending").open("a", encoding="utf-8").write("5")

    loaded = Progress.load(tmp_path / "progress.json")

    assert loaded.sent_upto == 5
    assert loaded.digests == []


def test_進み具合の全部の保存は_まとめて行う(world, monkeypatch):
    _, _, progress = world
    saves = []
    real_save = Progress.save

    def count_save(self) -> None:
        saves.append(len(self.digests))
        real_save(self)

    monkeypatch.setattr(Progress, "save", count_save)
    day = "2026-04-12"
    rows = [say(day, minute, f"{minute}分の話です。") for minute in range(0, 30)]
    rows.append(close(day))

    flow(world, rows)

    assert len(saves) == 1
    assert saves == [31]


def test_費用の確かめは毎行行わない(world, monkeypatch):
    calls = []
    real = run_script.spent_usd

    def count(db_path, progress):
        calls.append(1)
        return real(db_path, progress)

    monkeypatch.setattr(run_script, "spent_usd", count)
    day = "2026-04-12"
    rows = [say(day, minute, f"{minute}分の話です。") for minute in range(0, 40)]
    rows.append(close(day))

    flow(world, rows)

    assert len(calls) == 3


def test_整理の前には費用を確かめる(world, monkeypatch):
    _, _, progress = world
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day), {"at": f"{day}T09:00", "kind": "整理", "by": "誠一さん"}]
    flow(world, rows[:2])
    progress.answer_usd = 99.0

    with pytest.raises(StopRun, match="費用の枠"):
        flow(world, rows)


# ---- 順に流すときと比べる ----


def _snapshot(db_path: Path) -> dict:
    """裏の処理の順番に左右されない形で、データベースの中身を取り出す。"""
    words = sorted(
        (row["source"], row["body"], row["effective_date"], row["confirm_status"], row["share_scope"])
        for row in query(
            db_path,
            """
            SELECT source, body, effective_date, confirm_status, share_scope FROM statements
            WHERE id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
            """,
        )
    )
    spoken = sorted((row["text"], row["spoken_at"][:16]) for row in people_utterances(db_path))
    thread = [(row["body"], row["commented_at"][:16]) for row in query(db_path, "SELECT body, commented_at FROM comments ORDER BY rowid")]
    summaries = sorted(row["summary"] or "" for row in query(db_path, "SELECT summary FROM conversations"))
    return {
        "statements": words,
        "spoken": spoken,
        "thread": thread,
        "summaries": summaries,
        "marks": len(query(db_path, "SELECT 1 FROM split_marks")),
    }


def _parity_rows() -> list[dict]:
    rows = [
        {"at": "2026-03-31T07:00", "kind": "託す", "by": "誠一さん", "delegates": ["美穂さん"], "themes": ["好きなこと"]},
        {"at": "2026-03-31T07:01", "kind": "段階", "by": "誠一さん", "stage": "元気な時期"},
    ]
    for number in range(4):
        day = day_of(number)
        rows += [
            say(day, 10, f"{number}日目の庭の話です。"),
            close(day),
            letter(day, f"便り{number}"),
            comment(day, f"便り{number}", f"{number}日目もまた帰ります。", 40),
            comment(day, f"便り{number}", f"{number}日目は健太も一緒です。", 41),
        ]
    day = day_of(4)
    rows += [
        say(day, 10, "庭が好きなんです。"),
        close(day),
        {"at": f"{day}T08:31", "kind": "確かめ", "by": "誠一さん", "match": "庭が好き"},
        {"at": f"{day}T08:32", "kind": "託す", "by": "誠一さん", "delegates": ["美穂さん"], "themes": ["好きなこと", "家族"]},
    ]
    return rows


def test_本数が0でも2でも_同じ入力なら発言と言明の数と日付が同じ(tmp_path, monkeypatch):
    results = {}
    for workers in (0, 2):
        fake = EchoClaude()
        fake.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
        fake.delay["言明への分割"] = 0.01
        monkeypatch.setattr(ai, "_make_client", fake.client)
        folder = tmp_path / f"流し込み{workers}"
        folder.mkdir()
        deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
        app = create_app(deployment=deployment)
        world = (TestClient(app), deployment.db_path, Progress(folder / "progress.json"))

        flow(world, _parity_rows(), workers=workers)

        results[workers] = _snapshot(deployment.db_path)
        assert query(deployment.db_path, "SELECT * FROM job_failures") == []

    assert results[0] == results[2]
    assert results[0]["statements"]


def test_本数が0なら受け手を入れない(world, monkeypatch):
    client, _, _ = world
    seen = []
    real = run_script.Runner.run_line

    def spy(self, *args, **kwargs):
        seen.append(client.app.state.job_runner)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(run_script.Runner, "run_line", spy)
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=0)

    assert seen == [None, None]


def test_流したあとは受け手がはずれる(world):
    client, _, _ = world
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=2)

    assert client.app.state.job_runner is None
    assert ledger.write_wait_watcher is None


def test_stop_afterで止まるときも裏の処理を終えてから戻る(world, claude):
    _, db_path, _ = world
    claude.delay["言明への分割"] = 0.1
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day), say("2026-04-13", 10, "次の日の話です。"), close("2026-04-13")]

    assert flow(world, rows, stop_after=2, workers=2) == 2

    assert_all_split(db_path)


def test_untilで止まるときも裏の処理を終えてから戻る(world, claude):
    _, db_path, _ = world
    claude.delay["言明への分割"] = 0.1
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day), say("2026-04-13", 10, "次の日の話です。"), close("2026-04-13")]

    assert flow(world, rows, until=datetime(2026, 4, 12).date(), workers=2) == 2

    assert_all_split(db_path)


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "確かめ", "match": "庭の話"},
        {"kind": "訂正", "match": "庭の話", "text": "縁側の話です。"},
        {"kind": "本人だけにする", "text": "庭の話です。"},
        {"kind": "推測", "match": "庭の話", "text": "庭が大切なのかもしれません。"},
        {"kind": "整理"},
        {"kind": "段階", "stage": "要支援"},
        {"kind": "託す", "delegates": ["美穂さん"], "themes": ["好きなこと"]},
        {"kind": "人", "name": "健太くん", "role": "家族"},
    ],
    ids=lambda row: row["kind"],
)
def test_記録を読み書きする行の前で_裏の処理を全部待つ(world, claude, row):
    _, db_path, _ = world
    # 分割を遅らせる。待たなければ、確かめる記録がまだない
    claude.delay["言明への分割"] = 0.2
    seen = []
    day = "2026-04-12"
    real_call = run_script.Runner._wait_for_jobs

    def after_wait(self, line):
        real_call(self, line)
        if line.kind == row["kind"]:
            seen.append(len(query(db_path, "SELECT 1 FROM statements")))

    run_script.Runner._wait_for_jobs = after_wait
    try:
        flow(world, [say(day, 10, "庭の話です。"), close(day), {"at": f"{day}T09:00", "by": "誠一さん", **row}], workers=2)
    finally:
        run_script.Runner._wait_for_jobs = real_call

    assert seen == [1]


# ---- 報告の数字 ----


def test_同時の数の材料をjobs_statsに残し_本文は書かない(world, claude):
    _, _, progress = world
    timer = FakeTimer()
    claude.failures["言明への分割"] = [(429, {"retry-after": "7"})]
    words = "誰にも見せない本文です。"
    day = "2026-04-12"

    flow(world, [say(day, 10, words), close(day)], workers=2, timer=timer)

    path = progress.path.parent / run_script.STATS_FILE
    stats = json.loads(path.read_text(encoding="utf-8"))
    assert words not in path.read_text(encoding="utf-8")
    assert stats["workers_limit"] == 2
    assert stats["workers_now"] == 1
    assert stats["workers_lowered"] == 1
    assert stats["rate_limited"] == 1
    assert stats["waited_seconds"] == 7.0
    assert stats["lock_failures"] == 0
    assert stats["jobs_done"] == 1
    for key in ("queue_max", "write_waits", "write_wait_seconds", "job_seconds", "queue_wait_seconds", "barrier_wait_seconds"):
        assert key in stats


def test_続きから流しても_数字は足していく(world, claude):
    _, _, progress = world
    timer = FakeTimer()
    claude.failures["言明への分割"] = [(429, {"retry-after": "7"})]
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day), say("2026-04-13", 10, "次の日の話です。"), close("2026-04-13")]

    flow(world, rows[:2], workers=2, timer=timer)
    claude.failures["言明への分割"] = [(429, {"retry-after": "3"})]
    flow(world, rows, workers=2, timer=timer)

    stats = json.loads((progress.path.parent / run_script.STATS_FILE).read_text(encoding="utf-8"))
    assert stats["rate_limited"] == 2
    assert stats["waited_seconds"] == 10.0
    assert stats["jobs_done"] == 2


def test_データベースのロックの失敗を数えて待ってやり直す(world):
    client, _, _ = world
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    client.app.state.job_runner = runner
    tries = []

    def work(_conn) -> None:
        tries.append(1)
        if len(tries) == 1:
            raise sqlite3.OperationalError("database is locked")

    try:
        jobs._guarded(client.app, "言明への分割", "会話 1", work)
        assert len(tries) == 2
        assert runner.stats.lock_failures == 1
        assert timer.slept == [run_script.LOCK_RETRY_SECONDS]
    finally:
        client.app.state.job_runner = None
        runner.close()


def test_受け手がなければ_失敗はやり直さず記録する(world, claude):
    client, db_path, _ = world
    tries = []

    def work(_conn) -> None:
        tries.append(1)
        raise ai.AIUnavailableError("x")

    jobs._guarded(client.app, "言明への分割", "会話 1", work)

    assert len(tries) == 1
    assert [row["message"] for row in query(db_path, "SELECT message FROM job_failures")] == ["AIUnavailableError"]


def test_受け手がなければ_会話を閉じた処理は今までの順に流れる(world, claude):
    client, db_path, _ = world
    order = []
    claude.before_call = order.append
    day = "2026-04-12"

    flow(world, [say(day, 10, "庭の話です。"), close(day)], workers=0)

    assert [role for role in order if role in ("言明への分割", "会話の要約", "人の候補")] == ["言明への分割", "会話の要約", "人の候補"]
    assert_all_split(db_path)


def test_main_はworkersを受け取り_流し終えると数字を残す(tmp_path, claude):
    script = tmp_path / "script.jsonl"
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day)]
    script.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    folder = tmp_path / "流し込み"

    assert run_script.main([str(script), "--folder", str(folder), "--workers", "3"]) == 0

    stats = json.loads((folder / run_script.STATS_FILE).read_text(encoding="utf-8"))
    assert stats["workers_limit"] == 3
    assert stats["jobs_done"] == 1


def test_main_はworkers0なら数字のファイルを作らない(tmp_path, claude):
    script = tmp_path / "script.jsonl"
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day)]
    script.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    folder = tmp_path / "流し込み"

    assert run_script.main([str(script), "--folder", str(folder), "--workers", "0"]) == 0

    assert not (folder / run_script.STATS_FILE).exists()


def test_止まったときはmainが2を返し_続きを流せる(tmp_path, claude, monkeypatch, capsys):
    monkeypatch.setattr(run_script, "JOB_RETRY_SECONDS", (0,))
    claude.always_fail["言明への分割"] = (503, {})
    script = tmp_path / "script.jsonl"
    rows = many_days(2)
    script.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    folder = tmp_path / "流し込み"

    assert run_script.main([str(script), "--folder", str(folder)]) == 2
    assert "止めました" in capsys.readouterr().out

    claude.always_fail.clear()
    assert run_script.main([str(script), "--folder", str(folder)]) == 0
    assert_all_split(folder / "narrative_twin.db")


def test_時刻を止める文脈は_受け手の処理の中だけに効く(world):
    client, _, _ = world
    runner = JobRunner(2, timer=FakeTimer())
    seen = []
    moment = datetime(2026, 4, 12, 8, 10).astimezone()
    try:
        runner.submit("a", moment, lambda: seen.append(clock.now(client.app)))
        runner.drain()
    finally:
        runner.close()

    assert seen == [moment]
    assert clock.now(client.app).date() != moment.date()


# ---- 待ちを減らす直し ----


def asks_not(row: dict) -> dict:
    return {**row, "ask": False}


def ops_of(progress: Progress) -> list[str]:
    log = progress.path.parent / "log.jsonl"
    return [json.loads(raw).get("op", "") for raw in log.read_text(encoding="utf-8").splitlines()]


def test_問わないコメントのあとは_同じ便りの処理を待たない(world, claude):
    _, db_path, _ = world
    first, second = "2026-04-12", "2026-04-13"
    # 次の日の AI が呼ばれるまで分割を止めておく。コメントの前で待つと、止めたまま先へ進めず、待ちが解けない
    claude.hold = {"言明への分割"}
    claude.release_word = "次の日の話"
    rows = [
        letter(first, "庭"),
        asks_not(comment(first, "庭", "一つ目のコメントです。", 40)),
        asks_not(comment(first, "庭", "二つ目のコメントです。", 41)),
        asks_not(comment(first, "庭", "三つ目のコメントです。", 42)),
        say(second, 10, "次の日の話です。"),
        close(second),
    ]

    flow(world, rows, workers=2)

    assert_all_split(db_path)
    assert query(db_path, "SELECT 1 FROM comments WHERE is_twin_question = 1") == []
    words = {row["body"]: row for row in query(db_path, "SELECT * FROM statements")}
    assert words["三つ目のコメントです。"]["effective_date"] == first
    thread = [row["body"] for row in query(db_path, "SELECT body FROM comments ORDER BY rowid")]
    assert thread == ["一つ目のコメントです。", "二つ目のコメントです。", "三つ目のコメントです。"]


def test_問わないコメントがまとめて分けられても_言明は欠けず重ならない(world, claude):
    _, db_path, _ = world
    first, second = "2026-04-12", "2026-04-13"
    # 1本にして、前の会話の分割で止めておく。解けたとき、最初のコメントの処理が残りのコメントも一緒に読む
    claude.hold = {"言明への分割"}
    claude.release_word = "次の日の話"
    rows = [
        say(first, 10, "庭の話です。"),
        close(first),
        letter(first, "庭"),
        asks_not(comment(first, "庭", "一つ目のコメントです。", 40)),
        asks_not(comment(first, "庭", "二つ目のコメントです。", 41)),
        say(second, 10, "次の日の話です。"),
        close(second),
    ]

    flow(world, rows, workers=1)

    assert_all_split(db_path)


def test_別の日のコメントをまとめて分けても_記録の日は話した日のまま(world, claude):
    _, db_path, _ = world
    first, second = "2026-04-12", "2026-04-13"
    # 1本にして、前の会話の分割の間にコメントの処理を待たせる。前の日の時刻で止めた処理が、次の日のコメントも一緒に読む
    claude.delay["言明への分割"] = 0.3
    rows = [
        say(first, 10, "庭の話です。"),
        close(first),
        letter(first, "庭"),
        asks_not(comment(first, "庭", "前の日のコメントです。", 40)),
        asks_not(comment(second, "庭", "次の日のコメントです。", 40)),
    ]

    flow(world, rows, workers=1)

    assert_all_split(db_path)
    words = {row["body"]: row for row in query(db_path, "SELECT * FROM statements")}
    assert words["次の日のコメントです。"]["effective_date"] == second
    assert words["次の日のコメントです。"]["created_at"].startswith(second)


def test_受け答えの前は_同じ便りの分割の終わりを待ち_人のコメントを飛ばさない(world, claude):
    _, db_path, _ = world
    day = "2026-04-12"
    claude.answers["スレッドの問い"] = {"question": "庭の話を、もう少し聞かせていただけますか？"}
    claude.answers["架空の受け答え"] = {"text": "また今度たい。"}
    claude.answers["架空の受け答えの確かめ"] = {"new_people": [], "new_events": [], "new_numbers": [], "not_chikugo": None}
    # 1本にして、別の便りの分割の間に、問わないコメントの処理を待たせる。受け答えが待たないと、
    # 分割の印が問わないコメントの後ろへ進み、そのコメントが言明にならない
    claude.delay["言明への分割"] = 0.3
    rows = [
        letter(day, "庭", 31),
        letter(day, "花", 32),
        comment(day, "庭", "問うコメントです。", 40),
        asks_not(comment(day, "花", "別の便りのコメントです。", 41)),
        asks_not(comment(day, "庭", "問わないコメントです。", 42)),
        {"at": f"{day}T08:43", "kind": "受け答え", "by": "誠一さん", "letter": "庭", "hint": "庭の話", "limit": 25},
    ]

    flow(world, rows, workers=1)

    bodies = [row["body"] for row in query(db_path, "SELECT body FROM statements")]
    assert "問わないコメントです。" in bodies
    assert "問うコメントです。" in bodies
    # 足した文は言明にならない
    assert "また今度たい。" not in bodies


def test_問わないコメントの処理を失って流し直しても_問いは出ない(world, claude, monkeypatch):
    client, db_path, progress = world
    claude.answers["スレッドの問い"] = {"question": "つつじの話を、もう少し聞かせていただけますか？"}
    first, second = "2026-04-12", "2026-04-20"
    rows = [
        say(first, 10, "つつじの話です。"),
        close(first),
        letter(first, "つつじ"),
        asks_not(comment(first, "つつじ", "また帰ります。")),
        say(second, 10, "次の日の話です。"),
        close(second),
    ]

    crash_at(world, rows, 5, after=True, monkeypatch=monkeypatch)
    resume((client, db_path, Progress.load(progress.path)), rows, workers=2)

    assert query(db_path, "SELECT 1 FROM comments WHERE is_twin_question = 1") == []
    assert_all_split(db_path)


def test_日付の変更とログインはHTTPを通らず_発言の時刻は行のとおり(world, claude):
    _, db_path, progress = world
    day = "2026-04-12"
    rows = [say(day, 10, "庭の話です。"), close(day)]

    flow(world, rows, workers=2)

    ops = ops_of(progress)
    assert "POST /api/dev/date" not in ops
    assert "POST /api/login" not in ops
    assert people_utterances(db_path)[0]["spoken_at"].startswith(f"{day}T08:10")


def test_知らない名前の行は止まる(world, claude):
    with pytest.raises(StopRun, match="ログインできません"):
        flow(world, [{**say("2026-04-12", 10, "庭の話です。"), "by": "知らない人さん"}], workers=2)


def test_人だけの部屋の発言は読み直さなくても_同じ記録が残る(world, claude):
    _, _, progress = world
    at = "2026-04-12T08:"
    rows = [
        {"at": f"{at}00", "kind": "部屋", "by": "誠一さん", "room": "将棋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": f"{at}01", "kind": "発言", "room": "将棋", "by": "正雄さん", "text": "今度の日曜はどげんね。"},
        {"at": f"{at}02", "kind": "発言", "room": "将棋", "by": "誠一さん", "text": "行くたい。"},
    ]

    flow(world, rows, workers=2)

    assert "GET /api/rooms/{room_id}/messages" not in ops_of(progress)
    said = [(item["speaker"], item["text"]) for item in progress.extra["2026"] if item["kind"] == "部屋の発言"]
    assert said == [("正雄さん", "今度の日曜はどげんね。"), ("誠一さん", "行くたい。")]
    assert progress.room_seq["将棋"] >= 2


def test_ツインを入れた部屋の発言は読み直して_ツインの言葉も記録する(world, claude):
    _, _, progress = world
    at = "2026-04-12T08:"
    claude.answers["グループのツイン"] = {"reply": "そうでしたか。", "question": None}
    rows = [
        {"at": f"{at}00", "kind": "部屋", "by": "誠一さん", "room": "将棋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": f"{at}01", "kind": "ツインを入れる", "by": "誠一さん", "room": "将棋", "present": True},
        {"at": f"{at}02", "kind": "発言", "room": "将棋", "by": "正雄さん", "text": "今度の日曜はどげんね。"},
    ]

    flow(world, rows, workers=0)

    assert "将棋" in progress.twin_rooms
    assert "GET /api/rooms/{room_id}/messages" in ops_of(progress)


def test_100行ごとに速さと費用を残す(world, claude):
    assert run_script.SPEED_EVERY_LINES == 100
    _, _, progress = world

    flow(world, many_days(2), workers=2, speed_every=4)

    raw = (progress.path.parent / run_script.SPEED_FILE).read_text(encoding="utf-8")
    written = [json.loads(item) for item in raw.splitlines()]
    assert [row["line"] for row in written] == [4, 8]
    for name in ("at", "day", "ms_per_line", "usd", "db_mb", "memory_mb", "workers", "waiting", "letter_wait_seconds"):
        assert name in written[0]
    # 本文は入れない
    assert "庭の話" not in raw
    assert "縁側" not in raw


def test_同じ便りを待った秒数を数える():
    timer = FakeTimer()
    runner = JobRunner(2, timer=timer)
    try:
        runner.wait_for("便り:1")
        assert runner.stats.letter_wait_seconds == 0
        assert "letter_wait_seconds" in runner.speed_fields()
    finally:
        runner.close()


def test_年の変わり目に控えを取る(world, claude):
    _, db_path, progress = world
    claude.delay["言明への分割"] = 0.1
    rows = [
        say("2026-12-31", 10, "年の終わりの話です。"),
        close("2026-12-31"),
        say("2027-01-01", 10, "年の始めの話です。"),
        close("2027-01-01"),
    ]

    flow(world, rows, workers=2)

    saved = progress.path.parent / run_script.CHECKPOINT_FOLDER / "before-2027"
    info = json.loads((saved / run_script.CHECKPOINT_INFO).read_text(encoding="utf-8"))
    assert info["lines"] == 2
    assert info["before_day"] == "2027-01-01"
    # 裏の処理を待ってから取るので、前の年の言葉は言明になっていて、次の年の言葉はまだない
    assert [row["body"] for row in query(saved / db_path.name, "SELECT body FROM statements")] == ["年の終わりの話です。"]
    assert len(json.loads((saved / "progress.json").read_text(encoding="utf-8"))["digests"]) == 2


def test_指定した日にも控えを取り_同じ節目を通っても上書きしない(world, claude):
    _, db_path, progress = world
    mark = datetime(2026, 11, 3).date()
    rows = [say("2026-11-02", 10, "前の日の話です。"), close("2026-11-02"), say("2026-11-05", 10, "後の日の話です。"), close("2026-11-05")]

    flow(world, rows, workers=2, checkpoint_days=(mark,))

    saved = progress.path.parent / run_script.CHECKPOINT_FOLDER / "before-2026-11-03"
    assert (saved / db_path.name).exists()
    info = (saved / run_script.CHECKPOINT_INFO).read_text(encoding="utf-8")
    assert run_script.save_checkpoint("before-2026-11-03", Progress.load(progress.path), db_path, None, mark) is not None
    assert (saved / run_script.CHECKPOINT_INFO).read_text(encoding="utf-8") == info
    assert (progress.path.parent / run_script.CHECKPOINT_FOLDER / "before-2026-11-03-2").exists()


@pytest.mark.parametrize(
    ("previous", "day", "name"),
    [
        (None, "2027-01-01", None),
        ("2026-12-31", "2026-12-31", None),
        ("2026-12-31", "2027-01-01", "before-2027"),
        ("2026-12-30", "2028-02-01", "before-2028"),
    ],
)
def test_控えを取る行は年が変わる最初の行(previous, day, name):
    parse = lambda raw: datetime.fromisoformat(raw).date() if raw else None  # noqa: E731
    assert run_script.checkpoint_due(parse(previous), parse(day)) == name


def _write_script(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def test_控えへ戻すと_その年の前から流し直せて_戻す前の分は残る(tmp_path, claude, capsys):
    folder = tmp_path / "流し込み"
    script = tmp_path / "script.jsonl"
    keep = [say("2026-12-31", 10, "残す年の話です。"), close("2026-12-31")]
    middle = [say("2027-01-01", 10, "直す前の年の話です。"), close("2027-01-01")]
    last = [say("2028-01-01", 10, "次の年の話です。"), close("2028-01-01")]
    _write_script(script, keep + middle + last)
    assert run_script.main([str(script), "--folder", str(folder), "--budget", "50"]) == 0
    checkpoints = folder / run_script.CHECKPOINT_FOLDER
    assert (checkpoints / "before-2027").exists()
    assert (checkpoints / "before-2028").exists()

    assert run_script.main([str(script), "--folder", str(folder), "--restore-checkpoint", "before-2027"]) == 0
    assert "before-2027 へ戻しました" in capsys.readouterr().out

    # 戻す前の分は消さず、後の控えと一緒に脇へ移す
    aside = next(folder.glob("replaced-*"))
    moved = [row["body"] for row in query(aside / "narrative_twin.db", "SELECT body FROM statements")]
    assert "直す前の年の話です。" in moved
    assert (aside / run_script.CHECKPOINT_FOLDER / "before-2028").exists()
    assert not (checkpoints / "before-2028").exists()
    db_path = folder / "narrative_twin.db"
    assert [row["body"] for row in query(db_path, "SELECT body FROM statements")] == ["残す年の話です。"]

    # 台本を直して、同じ命令で流し直せる
    fixed = [say("2027-01-01", 10, "直したあとの年の話です。"), close("2027-01-01")]
    _write_script(script, keep + fixed + last)
    assert run_script.main([str(script), "--folder", str(folder), "--budget", "50"]) == 0
    bodies = [row["body"] for row in query(db_path, "SELECT body FROM statements ORDER BY rowid")]
    assert bodies == ["残す年の話です。", "直したあとの年の話です。", "次の年の話です。"]
    assert (checkpoints / "before-2028").exists()
    assert_all_split(db_path)


def test_ない控えへは戻さず_今の分はそのまま(tmp_path, claude, capsys):
    folder = tmp_path / "流し込み"
    script = tmp_path / "script.jsonl"
    _write_script(script, [say("2026-12-31", 10, "年の終わりの話です。"), close("2026-12-31")])
    assert run_script.main([str(script), "--folder", str(folder)]) == 0
    capsys.readouterr()

    assert run_script.main([str(script), "--folder", str(folder), "--restore-checkpoint", "before-2030"]) == 2

    assert "before-2030 がありません" in capsys.readouterr().out
    assert (folder / "narrative_twin.db").exists()
    assert list(folder.glob("replaced-*")) == []


def test_整理の直後に節目の控えを取る(world, claude, monkeypatch):
    _, db_path, progress = world
    monkeypatch.setattr(run_script.Runner, "_do_consolidate", lambda self, number, line, key: None)
    day = "2036-05-05"
    rows = [say(day, 10, "節目の前の話です。"), close(day), {"at": f"{day}T23:59", "kind": "整理", "by": "誠一さん"}, say("2036-05-06", 10, "節目の後の話です。"), close("2036-05-06")]

    flow(world, rows, workers=2)

    saved = progress.path.parent / run_script.CHECKPOINT_FOLDER / "consolidated-2036-05-05"
    assert json.loads((saved / run_script.CHECKPOINT_INFO).read_text(encoding="utf-8"))["lines"] == 3
    assert [row["body"] for row in query(saved / db_path.name, "SELECT body FROM statements")] == ["節目の前の話です。"]


def test_確かめの文が見つからなければ_飛ばして数え_速さの記録にも書く(world, claude):
    _, _, progress = world
    day = "2030-01-04"
    rows = [
        say(day, 10, "庭の話です。"),
        close(day),
        {"at": f"{day}T08:40", "kind": "確かめ", "by": "誠一さん", "match": "庭の話です。"},
        {"at": f"{day}T08:41", "kind": "確かめ", "by": "誠一さん", "match": "ない言葉です。"},
    ]

    flow(world, rows, workers=2, keep_going=True, speed_every=4)

    assert progress.skipped_confirms == 1
    row = json.loads((progress.path.parent / run_script.SPEED_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert row["skipped_confirms"] == 1


def test_確かめの文が見つからないとき_keep_goingがなければ止まる(world, claude):
    day = "2030-01-04"
    rows = [say(day, 10, "庭の話です。"), close(day), {"at": f"{day}T08:41", "kind": "確かめ", "by": "誠一さん", "match": "ない言葉です。"}]

    with pytest.raises(StopRun, match="見つかりません"):
        flow(world, rows, workers=2)


def test_流す間だけ_AIの接続を1つ使い回す(monkeypatch):
    made = []

    class Client:
        def close(self) -> None:
            made.append("閉じた")

    def make() -> Client:
        made.append("作った")
        return Client()

    monkeypatch.setattr(ai, "_make_client", make)
    with run_script.shared_api_client():
        assert ai._make_client() is ai._make_client()
    assert ai._make_client is make
    assert made == ["作った", "閉じた"]
