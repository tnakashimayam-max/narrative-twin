"""花びらの読みのテスト。

役割：専門職の「記録」の上に出す5つの花びらの読みが、セルフに保存した読みのまま、見える根拠の言葉だけを添えて、
     専門職にだけ返ることを確かめる。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：9（本人の言葉は記録の本文のまま）、10（見えない言葉は出さない）、13（花びらは専門職の画面だけ、数字を出さない）。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import ai, db, selfhood
from app.schemas import EvidenceStance, EvidenceWeightKind, Petal, PieceStatus, ShareScope, Source
from tests.fake_claude import RoleClaude

RUN = "試し"
KEY = "A/くつろぎ/何で満たされるか"


def _piece(conn, key, reading, status=PieceStatus.LIKELY):
    petal, face = key.split("/")[1:]
    selfhood.add_piece_version(
        conn,
        run=RUN,
        key=key,
        section=selfhood.SECTION_PETALS,
        name=f"{petal}：{face}",
        person=None,
        hypotheses=[reading],
        standing=selfhood.Standing(status, 0.9, 4.0, 0.0),
        replay_date="2026-01-31",
    )


def _evidence(conn, statement_id, day="2026-01-10", kind=EvidenceWeightKind.SELF_WORDS):
    selfhood.add_evidence(conn, run=RUN, key=KEY, stance=EvidenceStance.SUPPORT, kind=kind, source_table="statements", source_id=statement_id, day=day)


@pytest.fixture
def board(conn, add_statement):
    _piece(conn, KEY, "夜の長風呂で満たされるようです。")
    _piece(conn, "A/結びつき/今どうか", "家に人の気配があると落ち着くのかもしれません。")
    seen = add_statement("風呂は命の洗濯たい。", scope=ShareScope.DECISION_SUPPORT)
    hidden = add_statement("本人だけの風呂の話。", scope=ShareScope.SELF_ONLY)
    _evidence(conn, seen.id)
    _evidence(conn, hidden.id, day="2026-01-20")
    db.insert_row(
        conn,
        "selfhood_documents",
        {"run": RUN, "kind": "セルフ", "as_of": "2026-01-31", "body": "人物像。", "created_at": datetime(2031, 1, 1).astimezone().isoformat()},
    )
    conn.commit()
    return seen, hidden


@pytest.fixture
def no_ai(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def test_専門職には5つの花びらを3つの面ずつ返す(login, board, no_ai):
    body = login("田中さん").get("/api/twin/petals").json()

    assert [petal["name"] for petal in body["petals"]] == [petal.value for petal in Petal]
    assert [petal["order"] for petal in body["petals"]] == [0, 1, 2, 3, 4]
    assert all([face["face"] for face in petal["faces"]] == list(selfhood.THREE_FACES) for petal in body["petals"])
    first = body["petals"][0]["faces"][0]
    assert first["readings"] == ["夜の長風呂で満たされるようです。"]
    assert first["status"] == PieceStatus.LIKELY.value
    assert "ツイン（AI）" in body["label"]
    assert no_ai.requests == []


def test_読みのない面は空きで返す(login, board, no_ai):
    face = login("田中さん").get("/api/twin/petals").json()["petals"][4]["faces"][2]

    assert face == {"face": "今どうか", "status": "空き", "readings": [], "words": [], "latest_evidence_date": None}


def test_根拠の本人の言葉は本文のまま_見えるものだけを添える(login, board, no_ai):
    seen, hidden = board

    words = login("田中さん").get("/api/twin/petals").json()["petals"][0]["faces"][0]["words"]

    assert [word["body"] for word in words] == ["風呂は命の洗濯たい。"]
    assert words[0]["statement_id"] == seen.id
    assert words[0]["source"] == Source.SELF_WORDS.value
    assert words[0]["effective_date"]


def test_見える記録にないかぎかっこの文は読みから落とす(login, conn, board, no_ai):
    # 人だけの部屋の言葉を引いた読みが、セルフにあった（原則15）
    _piece(conn, KEY, "夜の長風呂で満たされるようです。引き出しは「まだ開けられん」ようです。")
    conn.commit()

    readings = login("田中さん").get("/api/twin/petals").json()["petals"][0]["faces"][0]["readings"]

    assert readings == ["夜の長風呂で満たされるようです。"]


def test_見える記録にあるかぎかっこの文は残す(login, conn, board, no_ai):
    _piece(conn, KEY, "「風呂は命の洗濯たい。」と話す人です。")
    conn.commit()

    readings = login("田中さん").get("/api/twin/petals").json()["petals"][0]["faces"][0]["readings"]

    assert readings == ["「風呂は命の洗濯たい。」と話す人です。"]


def test_スラッシュでつないだ読みは段落に分けて返す(login, conn, board, no_ai):
    _piece(conn, KEY, "夜の長風呂で満たされるようです。／庭を見て座るのも好きなようです。")
    conn.commit()

    readings = login("田中さん").get("/api/twin/petals").json()["petals"][0]["faces"][0]["readings"]

    assert readings == ["夜の長風呂で満たされるようです。", "庭を見て座るのも好きなようです。"]


def test_面ごとに_いちばん新しい根拠の日を返す(login, board, no_ai):
    face = login("田中さん").get("/api/twin/petals").json()["petals"][0]["faces"][0]

    assert face["latest_evidence_date"] == "2026-01-20"


def test_確からしさの数は返さない(login, board, no_ai):
    text = login("田中さん").get("/api/twin/petals").text

    assert "confidence" not in text
    assert "support" not in text


def test_家族には花びらの読みを返さない(login, board, no_ai):
    assert login("美穂さん").get("/api/twin/petals").status_code == 404


def test_本人も自分の花びらの読みを見られる(login, board, no_ai):
    # 本人のホームの「5つの花びら」から読む（ご本人の依頼、2026-11-07）
    body = login("誠一さん").get("/api/twin/petals").json()

    assert [petal["name"] for petal in body["petals"]] == [petal.value for petal in Petal]
    assert body["petals"][0]["faces"][0]["readings"] == ["夜の長風呂で満たされるようです。"]
    assert "ツイン（AI）" in body["label"]


def test_セルフがまだなければ空の一覧を返す(login, no_ai):
    body = login("田中さん").get("/api/twin/petals").json()

    assert body["petals"] == []
