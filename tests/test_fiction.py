"""10年分の架空ログの予定と読み込みのテスト。

役割：物語の予定が決まりを守ること、保存したログから AI を呼ばずに記録を作り直せることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：8（人物と記録はすべて架空）、9（本人の言葉は一字も変えない）、10（本人だけの記録を広げない）。
"""

from __future__ import annotations

import re

from app.schemas import LifeStage, Theme
from eval.previous_story import load, plan


def test_予定は同じ結果が毎回できる():
    assert plan.build_plan() == plan.build_plan()


def test_予定は日付の順で重ならず_デモの日を含まない():
    days = [item.day for item in plan.build_plan()]

    assert days == sorted(days)
    assert len(days) == len(set(days))
    assert plan.DEMO_DAY not in days


def test_予定は10年分で約400回ある():
    items = plan.build_plan()

    assert 350 <= len(items) <= 420
    assert items[0].day.year == 2026
    assert items[-1].day.year == 2036
    assert items[-1].day <= plan.LAST_LOG_DAY


def test_決めた言葉は決めた日の予定に入る():
    by_day = {item.day: item for item in plan.build_plan()}

    for fixed in [*plan.FIXED_WORDS, *plan.PRIVATE_WORDS]:
        assert by_day[fixed.day].must_say == fixed.words


def test_本人だけの話はつらかったことのテーマで_便りを付けない():
    private = [item for item in plan.build_plan() if item.private]

    assert private
    assert all(item.theme is Theme.HARDSHIP and not item.commenters for item in private)


def test_つらかったことのテーマは本人だけの話にしか使わない():
    assert all(item.private for item in plan.build_plan() if item.theme is Theme.HARDSHIP)


def test_人生の段階は元気から要支援そして要介護へ進む():
    order = [LifeStage.ACTIVE, LifeStage.SUPPORT, LifeStage.CARE]
    stages = [order.index(item.stage) for item in plan.build_plan()]

    assert stages == sorted(stages)
    assert set(stages) == {0, 1, 2}


def test_予定の言葉に泊まりと診断の話を入れない():
    words = [
        *(item.must_say or "" for item in plan.build_plan()),
        *(item.topic for item in plan.build_plan()),
        *(item.setting for item in plan.build_plan()),
        *(comment.words for comment in plan.FIXED_COMMENTS),
        *(observation.words for observation in plan.FIXED_OBSERVATIONS),
    ]

    assert not [word for word in words if any(bad in word for bad in plan.FORBIDDEN_WORDS)]


def test_2034年以降に施設についての美穂さんの証言がある():
    words = [(c.day.year, c.speaker, c.words) for c in plan.FIXED_COMMENTS]

    assert (2034, "美穂さん", "父は施設だけは嫌だと言っていました。") in words


def test_2036年の今の様子に和子さんの疲れの記録がある():
    observations = [o.words for o in plan.FIXED_OBSERVATIONS if o.day.year == 2036]

    assert any("疲れ" in words and "母" in words for words in observations)


def test_2036年の春につつじ公園の本人の発言がある():
    fixed = [f for f in plan.FIXED_WORDS if f.day.year == 2036]

    assert fixed
    assert "つつじ公園" in fixed[0].words
    assert 3 <= fixed[0].day.month <= 5


def test_決めたコメントは決めた日の予定に入る():
    by_day = {item.day: item for item in plan.build_plan()}

    for comment in plan.FIXED_COMMENTS:
        assert (comment.speaker, comment.words) in by_day[comment.day].must_comments


def test_入れてある架空ログの本文に英字が混ざらない():
    """AI が作った文に英語が混ざることがあった。画面に映るので、入れてあるログを確かめる。"""
    mixed = re.compile(r"[A-Za-z]{2,}")
    logs = load.read_logs()
    words = [turn["text"] for log in logs.values() for turn in log["turns"]]
    words += [log["letter"] for log in logs.values() if log.get("letter")]
    words += [comment["text"] for log in logs.values() for comment in log.get("comments") or []]
    words += [item["body"] for unit in load.read_splits().values() for item in unit.get("statements", [])]

    assert [word for word in words if mixed.search(word)] == []

