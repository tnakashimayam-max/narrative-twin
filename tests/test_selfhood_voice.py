"""セルフを土台にした代弁のテスト。

役割：セルフがあるとき、代弁の土台が人物像とピースに変わること、引ける言葉の範囲、読まないものの漏れを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：9、10、15、16。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import advocacy_voice, ai, db, selfhood, room_store
from app.schemas import EvidenceStance, EvidenceWeightKind, PieceStatus, RoomKind, ShareScope
from tests.fake_claude import RoleClaude

RUN = "試し"
KEY = "B/譲れないこと"
VOICE_KEY = "E/言い回し"
SELFHOOD_BODY = "人物像の本文。家を離れない人かもしれない。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude(問いを読む={"question_kind": "判断", "piece_numbers": [1], "health": False, "path": "芯をたどった"})
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _piece(conn, key, name, section):
    selfhood.add_piece_version(
        conn,
        run=RUN,
        key=key,
        section=section,
        name=name,
        person=None,
        hypotheses=[f"{name}の仮説。"],
        standing=selfhood.Standing(PieceStatus.LIKELY, 0.8, 4.0, 0.0),
        replay_date="2030-01-31",
    )


def _evidence(conn, key, statement_id, day="2030-01-10"):
    selfhood.add_evidence(conn, run=RUN, key=key, stance=EvidenceStance.SUPPORT, kind=EvidenceWeightKind.SELF_WORDS, source_table="statements", source_id=statement_id, day=day)


@pytest.fixture
def board(conn, add_statement):
    _piece(conn, KEY, "譲れないこと", selfhood.SECTION_CORE)
    _piece(conn, VOICE_KEY, "言い回し", selfhood.SECTION_VOICE)
    seen = add_statement("見えてよい言葉。", scope=ShareScope.DECISION_SUPPORT)
    hidden = add_statement("本人だけの言葉。", scope=ShareScope.SELF_ONLY)
    _evidence(conn, KEY, seen.id)
    _evidence(conn, KEY, hidden.id)
    db.insert_row(
        conn,
        "selfhood_documents",
        {"run": RUN, "kind": "セルフ", "as_of": "2030-12-31", "body": SELFHOOD_BODY, "created_at": datetime(2031, 1, 1).astimezone().isoformat()},
    )
    conn.commit()
    return seen, hidden


def test_問いの読みは聞いた人のねらいとして書く段に渡る(conn, users, board, claude):
    claude.answers["問いを読む"] = {
        "question_kind": "判断",
        "asker_need": "通院の付き添いを決めたい",
        "scene": "ケアプラン：通院の付き添い",
        "about": "誠一さん",
        "piece_numbers": [1],
        "health": False,
        "path": "芯をたどった",
    }
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": []}

    material = advocacy_voice.gather(conn, users["美穂さん"], "付き添いを頼んでよいか")
    advocacy_voice.speak(conn, material)

    asked = json.dumps(claude.bodies("問いを読む")[0]["messages"], ensure_ascii=False)
    written = json.dumps(claude.bodies("ツインのアドボカシー")[0]["messages"], ensure_ascii=False)
    assert "聞いた人：美穂さん（家族）" in asked
    assert "問いの読み（AI が読んだ、聞いた人のねらいと場面です。本人の思いではありません）" in written
    assert "聞いた人のねらい：通院の付き添いを決めたい" in written
    assert "決める場面：ケアプラン：通院の付き添い" in written


def test_問いを読めなかったときは問いの読みを渡さない(conn, users, board, claude):
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": []}

    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    advocacy_voice.speak(conn, material)

    assert material.question_reading == []
    assert "問いの読み" not in json.dumps(claude.bodies("ツインのアドボカシー")[0]["messages"], ensure_ascii=False)


def test_セルフがあれば_今の再生が選ばれる(conn, board):
    assert selfhood.active_run(conn) == RUN


def test_セルフがあれば_土台は人物像とピースになる(conn, users, board, claude):
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": []}

    view, _ = advocacy_voice.speak(conn, material)

    system = claude.bodies("ツインのアドボカシー")[0]["system"]
    assert SELFHOOD_BODY in system[-1]["text"]
    assert "譲れないことの仮説。" in system[-1]["text"]
    assert "セルフと、関わるピース" in view["reading"]


def test_話し方のピースは選ばれなくても入る(conn, users, board, claude):
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")

    assert VOICE_KEY in {piece["key"] for piece in material.pieces}


def test_本人だけの言葉は引ける言葉に入らない(conn, users, board, claude):
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    bodies = [item.body for item in material.statements]

    assert "見えてよい言葉。" in bodies
    assert "本人だけの言葉。" not in bodies


def test_人だけの部屋の言葉は引ける言葉に入らない(conn, users, board, claude):
    room = room_store.create_room(conn, kind=RoomKind.PAIR, name="二人", member_ids=[users["誠一さん"].id, users["美穂さん"].id], moment="2030-01-01T00:00:00+09:00")
    message = room_store.insert_message(conn, room_id=room.id, sender_user_id=users["誠一さん"].id, text="二人だけの話。", sent_at="2030-01-01T00:00:00+09:00")
    reading = db.insert_row(
        conn,
        "dialogue_readings",
        {
            "message_id": message.id,
            "room_id": room.id,
            "speaker_user_id": users["誠一さん"].id,
            "effective_date": "2030-01-01",
            "reading": "x",
            "petals": [],
            "confidence": 0.5,
            "model": "試し",
            "created_at": "2030-01-01T00:00:00+09:00",
        },
    )
    selfhood.add_evidence(conn, run=RUN, key=KEY, stance=EvidenceStance.SUPPORT, kind=EvidenceWeightKind.DIALOGUE, source_table="dialogue_readings", source_id=reading, day="2030-01-01")
    conn.commit()

    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")

    assert "二人だけの話。" not in " ".join(material.word_lines)


def test_答え合わせの時点より後の言葉と版は使わない(conn, users, board, claude, add_statement):
    later = add_statement("未来の言葉。", scope=ShareScope.DECISION_SUPPORT)
    _evidence(conn, KEY, later.id, day="2035-01-01")
    conn.commit()

    material = advocacy_voice.gather_from_selfhood(conn, users["美穂さん"], "家のことを聞きたい", RUN, as_of="2031-12-31")

    assert "未来の言葉。" not in " ".join(item.body for item in material.statements)


def test_画面に出すピースの文にかぎかっこの言葉を出さない():
    pieces = [{"section": "C 関係", "hypotheses": ["娘は「同じ話、何回聞いても好きよ」と言う相手のようです。"], "status": "仮説"}]

    body = advocacy_voice._piece_views([1], pieces)[0]["body"]

    assert "同じ話" not in body
    assert "言う相手のようです" in body


def test_理由に渡していないピースの番号があれば捨てる():
    pieces = [{"section": "B 決め方の芯", "hypotheses": ["仮説。"], "status": "仮説"}]

    views = advocacy_voice._piece_views([1, 99], pieces)

    assert [view["body"] for view in views] == ["仮説。"]


def test_セルフがなければ今までの土台のまま(conn, users, wish_statement, claude):
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")

    assert material.selfhood_body is None
    assert claude.bodies("問いを読む") == []


@pytest.fixture
def wish_statement(add_statement):
    return add_statement("家におりたい。", scope=ShareScope.DECISION_SUPPORT)


def test_託された家族の答えには理由ごとに本人の元の発言が付く(conn, users, board, claude):
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    number = next(index for index, piece in enumerate(material.pieces, start=1) if piece["key"] == KEY)
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": [{"text": "家が芯じゃ。", "element_numbers": [number], "evidence_numbers": []}]}

    with_words, _ = advocacy_voice.speak(conn, material, with_own_words=True)
    without, _ = advocacy_voice.speak(conn, material)

    assert [quote["body"] for quote in with_words["reasons"][0]["evidence"]] == ["見えてよい言葉。"]
    assert without["reasons"][0]["evidence"] == []


def test_代弁の土台に心の目次の読みが入る(conn, users, board, claude, monkeypatch):
    monkeypatch.setattr(advocacy_voice.heart, "readings_for_supporters", lambda conn, visible, as_of=None: ["### 和子さん", "これまで：今も隣にいるつもりかもしれません。"])
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": []}

    view, _ = advocacy_voice.speak(conn, material)

    system = claude.bodies("ツインのアドボカシー")[0]["system"]
    assert "今も隣にいるつもり" in system[-1]["text"]
    assert "心の目次の読み1件" in view["reading"]


def test_託された家族の答えに添える元の発言は_ツインにだけ話した言葉も使い_理由ごとに1件まで(conn, users, board, claude, add_statement):
    from app.schemas import Place

    for text in ("ツインにだけ話した言葉。", "もう1つのツインにだけ話した言葉。"):
        _evidence(conn, KEY, add_statement(text, scope=ShareScope.DECISION_SUPPORT, place=Place.DIARY_AND_TALK).id, day="2030-01-20")
    conn.commit()
    material = advocacy_voice.gather(conn, users["美穂さん"], "家のことを聞きたい")
    number = next(index for index, piece in enumerate(material.pieces, start=1) if piece["key"] == KEY)
    claude.answers["ツインのアドボカシー"] = {"claim": "わしは家におる。", "reasons": [{"text": "家が芯じゃ。", "element_numbers": [number], "evidence_numbers": []}]}

    view, _ = advocacy_voice.speak(conn, material, with_own_words=True)

    assert len(view["reasons"][0]["evidence"]) == 1
