"""架空ログの試しのコードのテスト。

役割：会話の予定が筋書きどおりで、データが ledger を通って入ることを確かめる。本物の API は呼ばない。
4つの極：3. 読み解き、積み上げる。
守る原則：6（お休みの話題を聞かない）、8（架空）、9（一字も変えない）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import ledger
from scripts import trial_log
from scripts.trial_log import LogComment, LogConversation, LogTurn, PlanItem


def _log(text="毎朝この庭を見るのが楽しみなんです。", comments=()):
    return LogConversation(
        trigger="庭の写真",
        turns=[
            LogTurn(speaker="ツイン", text="誠一さん、庭はいかがですか？"),
            LogTurn(speaker="誠一さん", text=text),
        ],
        confirmation="合っています",
        letter="庭の便りです。" if comments else None,
        comments=list(comments),
    )


def _plan(**changes):
    values = {
        "index": 0,
        "day": "2026-04-12",
        "theme": "好きなこと",
        "confirmation": "合っています",
        "must_say": None,
        "commenters": [],
        "must_comment": None,
    }
    values.update(changes)
    return PlanItem(**values)


def test_会話の数は1年に30から50回():
    assert 30 <= len(trial_log.plan_conversations()) <= 50


def test_決めた日と決めた言葉が入る():
    items = {item.day: item for item in trial_log.plan_conversations()}

    assert items["2026-04-12"].must_say == "毎朝この庭を見るのが楽しみなんです"
    assert items["2026-05-10"].must_say == "最初の職場は製材所だった"
    assert items["2026-09-20"].must_comment == "夏休みに、父と縁側でスイカを食べた"


def test_デモの日はログに入らない():
    days = {item.day for item in trial_log.plan_conversations()}

    assert trial_log.DEMO_DAY.isoformat() not in days


def test_日付は重ならず2026年の中にある():
    days = [date.fromisoformat(item.day) for item in trial_log.plan_conversations()]

    assert len(days) == len(set(days))
    assert all(day.year == 2026 for day in days)


def test_会話は4月から12月まで広がる():
    days = sorted(date.fromisoformat(item.day) for item in trial_log.plan_conversations())

    assert days[0] == date(2026, 4, 12)
    assert days[-1].month == 12
    assert any(day.month == 8 for day in days)


def test_お休みの話題は予定に入らない():
    assert all(item.theme != "つらかったこと" for item in trial_log.plan_conversations())


def test_確認はほとんど合っていますで_ときどき直した():
    results = [item.confirmation for item in trial_log.plan_conversations()]

    assert results.count("直した") >= 1
    assert results.count("合っています") > results.count("直した") * 3


def test_毎回同じ予定になる():
    assert trial_log.plan_conversations() == trial_log.plan_conversations()


def test_決めた言葉がなければ問題として数える():
    plan = _plan(must_say="最初の職場は製材所だった")

    assert trial_log.plan_problems(plan, _log("最初は別の仕事でした。"))
    assert not trial_log.plan_problems(plan, _log("最初の職場は製材所だった。"))


def test_決めた言葉を本人以外が言っても数えない():
    plan = _plan(must_say="庭はいかがですか")

    assert trial_log.plan_problems(plan, _log())


def test_会話は台帳の関数を通って入る(conn):
    units = trial_log.load_log(conn, _plan(), _log())

    assert list(units) == ["conv-0"]
    rows = conn.execute("SELECT * FROM utterances").fetchall()
    assert [row["text"] for row in rows][1] == "毎朝この庭を見るのが楽しみなんです。"
    assert rows[0]["speaker_is_twin"] == 1


def test_節子さんのコメントがある便りは縁のある人まで(conn):
    units = trial_log.load_log(
        conn, _plan(index=3), _log(comments=[LogComment(speaker="節子さん", text="きれいな庭です。")])
    )

    assert "letter-3" in units
    assert conn.execute("SELECT share_with FROM letters").fetchone()[0] == "縁のある人まで"


def test_美穂さんだけのコメントの便りは家族(conn):
    trial_log.load_log(
        conn, _plan(index=3), _log(comments=[LogComment(speaker="美穂さん", text="父の庭です。")])
    )

    assert conn.execute("SELECT share_with FROM letters").fetchone()[0] == "家族"


def test_読み込みで言明は作られない(conn):
    """言明は分ける処理からだけ入る。"""
    trial_log.load_log(conn, _plan(), _log())

    assert ledger.current_statements(conn) == []


def test_10年分の見込みは回数に比例する():
    usage = [{"cost_usd": 1.0}, {"cost_usd": 3.0}]

    forecast = trial_log.estimate_forecast(40, usage, 120.0)

    assert forecast["conversations_in_estimate"] == 400
    assert forecast["trial_cost_usd"] == 4.0
    assert forecast["estimate_cost_usd"] == 40.0


def test_上限を超えそうなら送らない():
    with pytest.raises(SystemExit):
        trial_log._guard_budget({"spent_usd": 4.9}, 0.5)

    trial_log._guard_budget({"spent_usd": 1.0}, 0.5)


def test_1回の試しの費用の上限は今夜の上限に収まる():
    log = trial_log._worst_case_usd("架空ログの作成", trial_log.CONVERSATION_COUNT, 1500)
    split = trial_log._worst_case_usd("言明への分割", trial_log.CONVERSATION_COUNT + 10, 6000)

    assert log + split < trial_log.TONIGHT_LIMIT_USD
