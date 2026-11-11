"""ツインのアドボカシーの確認の道具のテスト。

役割：道具が元のデータベースに書き込まないこと、AI を勝手に呼ばないこと、漏れと届き方を正しく数えることを確かめる。
4つの極：4. 10年後に代弁する（確かめるためだけの道具）。
守る原則：8（架空のデータだけを使う）、10（見える範囲）。

通信だけを差し替える。本物の Claude API は呼ばない。紙のページ数を確かめる段は Chrome が要るので、ここでは流さない。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app import ai, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Theme
from scripts import check_advocacy as tool
from tests.fake_claude import RoleClaude

KEY = "美穂が決めてよか"
WORDS = "家におれんようになったら、美穂が決めてよか。"
SECRET = "兄貴、俺が悪かった。一言謝りたかった。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def record(conn, add_statement):
    statement = add_statement(WORDS, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2033, 11, 17))
    model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="判断を美穂さんに任せる。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2033, 1, 1),
        evidence_statement_ids=[statement.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    return statement


@pytest.fixture
def cases_file(tmp_path):
    data = {
        "words": [{"date": "2033-11-17", "key": KEY}, {"date": "2030-01-04", "key": "記録にない言葉"}],
        "questions": [{"question": "家のことを聞きます", "expect": [KEY]}],
        "plan": "週2回のデイサービスと配食弁当",
        "meeting": [{"by": "田中さん", "text": "「大切なもの」を教えてください。"}, {"by": "誠一さん", "text": "親父の弁当箱。"}],
        "hidden": [SECRET],
        "flag_words": ["減塩"],
        "secret_words": ["認知症"],
    }
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_一覧は物語の問いと18行の会議と本人だけの8行を持つ():
    cases = tool.load_cases()

    assert len(cases["meeting"]) == 18
    assert len(cases["hidden"]) == 8
    assert any(word["key"] == KEY for word in cases["words"])
    assert any(case.get("expect_not_yet") for case in cases["questions"])
    # 問いで確かめる言葉は、記録の確認の一覧にも必ずある
    keys = {word["key"] for word in cases["words"]}
    assert {key for case in cases["questions"] for key in case["expect"]} <= keys


@pytest.mark.parametrize("path", ["data/mine/narrative_twin.db", "C:/dev/x/MINE/a.db"])
def test_自分用の記録は開かない(path):
    with pytest.raises(tool.RefusedPath):
        tool.refuse_private(Path(path))


def test_自分用の記録を渡すと_写さずに止まる(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    source = mine / "narrative_twin.db"
    source.write_bytes(b"real")

    assert tool.main(["--db", str(source), "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out" / "work").exists()


def test_控えがなければ分かる言葉で止まる(tmp_path):
    assert tool.main(["--db", str(tmp_path / "ない.db"), "--out", str(tmp_path / "out")]) == 2


def test_元のデータベースに書き込まない(db_path, conn, record, tmp_path, cases_file):
    conn.close()
    before = db_path.read_bytes()

    tool.run(db_path, tmp_path / "out", use_ai=False, budget=1.0, cases_path=cases_file)

    assert db_path.read_bytes() == before
    assert (tmp_path / "out" / "work" / "narrative_twin.db").exists()


def test_AIを呼ぶ指定がなければAIを呼ばず_費用は0(db_path, conn, record, tmp_path, cases_file, claude):
    results = tool.run(db_path, tmp_path / "out", use_ai=False, budget=1.0, cases_path=cases_file)

    assert claude.requests == []
    assert results["spent"] == 0
    assert "questions" not in results
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert KEY in report


def test_挙げた言葉の有無と見える人と要素の根拠を見分ける(conn, record, add_statement):
    add_statement("本人だけの美穂が決めてよか。", scope=ShareScope.SELF_ONLY, theme=Theme.HOME, effective_date=date(2030, 1, 4))
    words = [
        {"date": "2033-11-17", "key": KEY},
        {"date": "2030-01-04", "key": "本人だけの美穂"},
        {"date": "2030-01-05", "key": "記録にない言葉"},
    ]

    shared, private, absent = tool.check_words(conn, words)

    assert (shared["found"], shared["asker_sees"], shared["in_model"]) == (True, True, True)
    assert (private["found"], private["asker_sees"], private["family_sees"], private["in_model"]) == (True, False, False, False)
    assert absent["found"] is False


def test_本人だけの言葉が田中さんに見えたら漏れとして数える(conn, add_statement):
    add_statement(SECRET, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)

    leaks = tool.check_hidden(conn, [SECRET])

    assert {"viewer": "田中さん", "fragment": "一言謝りたかった"} in leaks


def test_今は話したくないにした言葉は漏れにならない(conn, users, add_statement):
    statement = add_statement(SECRET, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    ledger.rest_statement(conn, statement.id, actor_user_id=users["誠一さん"].id)
    conn.commit()

    assert tool.check_hidden(conn, [SECRET]) == []


def test_AIの読む記録の中の病名の語を数える(conn, add_statement):
    add_statement("認知症と聞いた。", scope=ShareScope.SELF_ONLY)

    counts = tool.check_secret_words(conn, ["認知症", "アルツハイマー"])

    assert counts["認知症"] == 2  # 言明と発言
    assert counts["アルツハイマー"] == 0


def test_意思モデルのまとめは年ごとの数と確かめた数を返す(conn, users, record):
    ledger.confirm_statement(conn, record.id, actor_user_id=users["誠一さん"].id)
    conn.commit()

    summary = tool.model_summary(conn)

    assert summary["by_year"] == {2033: 1}
    assert summary["confirmed"] == 1
    assert summary["last_confirmed_on"] == "2033-11-17"
    assert summary["confirmed_elements"] == 1


def test_ツインの文だけから気になる語を探し_記録の本文では知らせない():
    voice = {
        "claim": "減塩の話をします。",
        "reasons": [{"text": "理由です。", "evidence": [{"body": "血圧が高か。"}]}],
        "requests": ["お願いです。"],
    }

    flags = tool.scan_flags(voice, ["減塩", "血圧"], [])

    assert flags["flag_words"] == ["減塩"]


def test_ツインの文に本人だけの言葉があれば知らせる():
    flags = tool.scan_flags({"claim": "一言謝りたかったと話しています。"}, [], [SECRET])

    assert flags["hidden_leaks"] == ["一言謝りたかった"]


def test_届いたと読んだと根拠に使ったを分けて数える():
    turn = {"self_words": [{"body": "言葉Aです。"}], "testimony": []}
    voice = {"reasons": [{"text": "理由", "evidence": [{"body": "言葉Bです。"}]}], "objections": []}

    hits = {hit["key"]: hit for hit in tool.word_hits(["言葉A", "言葉B", "言葉C"], turn, "言葉Aです。言葉Cです。", voice)}

    assert (hits["言葉A"]["delivered"], hits["言葉A"]["read"], hits["言葉A"]["used"]) == (True, True, False)
    assert (hits["言葉B"]["delivered"], hits["言葉B"]["read"], hits["言葉B"]["used"]) == (False, False, True)
    assert (hits["言葉C"]["delivered"], hits["言葉C"]["read"], hits["言葉C"]["used"]) == (False, True, False)


def test_AIを呼ぶ指定なら問いと紙と案を流し_報告に主張が載る(db_path, conn, record, tmp_path, cases_file, claude):
    claude.answers["ツインのアドボカシー"] = {
        "claim": "ツインは本心を家と考えます。",
        "reasons": [{"text": "理由です。", "element_numbers": [1], "evidence_numbers": [1]}],
        "objections": [],
        "requests": [],
        "ask_back": None,
    }

    results = tool.run(db_path, tmp_path / "out", use_ai=True, budget=100.0, page=False, cases_path=cases_file, only="1")

    item = results["questions"][0]
    assert item["voice_status"] == 200
    assert item["hits"] == [{"key": KEY, "delivered": True, "read": True, "used": True}]
    assert results["spent"] > 0
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "ツインは本心を家と考えます。" in report


def test_写しに会議の18行にあたる言葉を入れても_AIは呼ばず_元にも入らない(db_path, conn, record, tmp_path, cases_file, claude):
    results = tool.run(db_path, tmp_path / "out", use_ai=False, budget=1.0, cases_path=cases_file)

    assert results["meeting"] == "入れた"
    assert claude.requests == []
    assert conn.execute("SELECT COUNT(*) FROM rooms WHERE name = 'ケア会議'").fetchone()[0] == 0


def test_費用の上限を超えそうなら流さず_理由を残す(db_path, conn, record, tmp_path, cases_file, claude):
    results = tool.run(db_path, tmp_path / "out", use_ai=True, budget=0.0, page=False, cases_path=cases_file)

    assert "費用の上限" in results["questions"][0]["skipped"]
    assert "費用の上限" in results["sheet"]["skipped"]
    assert "費用の上限" in results["plan"]["skipped"]
    assert claude.requests == []
