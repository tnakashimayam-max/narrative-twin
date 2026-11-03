"""ケア会議のテスト。

役割：会議の画面から入れた今の様子が、会議の場所で記録され、いちばん上に出ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：2（今の様子を最初に出す）、3（出どころを分ける）、10（見える範囲）。

答えは「ツインに聞く」と同じ処理なので、本人の言葉の扱いは tests/test_advocacy.py で確かめている。
本物の Claude API は呼ばない。
"""

from __future__ import annotations

import re
from pathlib import Path

from app import db, ledger
from app.schemas import Place, Source


def _utterance_of(conn, statement_id):
    statement = ledger.get_statement(conn, statement_id)
    return statement, ledger.get_utterance(conn, statement.source_utterance_ids[0])


def test_会議の画面から入れた今の様子は_発言の場所が会議になる(conn, login):
    response = login("田中さん").post("/api/observations", json={"text": "言葉を聞いて、うなずかれました。", "at_meeting": True})

    assert response.status_code == 201
    statement, utterance = _utterance_of(conn, response.json()["statement_id"])
    assert utterance.place is Place.MEETING
    assert statement.source is Source.OBSERVATION


def test_ふだんの今の様子は_発言の場所がチャットのまま(conn, login):
    response = login("田中さん").post("/api/observations", json={"text": "朝は庭を見ていました。"})

    _, utterance = _utterance_of(conn, response.json()["statement_id"])
    assert utterance.place is Place.CHAT


def test_会議で入れた今の様子は_いちばん上の今の様子に出る(login):
    tanaka = login("田中さん")
    tanaka.post("/api/observations", json={"text": "言葉を聞いて、うなずかれました。", "at_meeting": True})

    overview = tanaka.get("/api/advocacy/overview").json()

    assert overview["now"][0]["body"] == "言葉を聞いて、うなずかれました。"
    assert overview["now"][0]["source"] == "今の様子（観察）"


def test_同じ日の今の様子は_新しく入れたものが先に出る(login):
    tanaka = login("田中さん")
    tanaka.post("/api/observations", json={"text": "先に入れた様子です。", "at_meeting": True})
    tanaka.post("/api/observations", json={"text": "あとから入れた様子です。", "at_meeting": True})

    now = tanaka.get("/api/advocacy/overview").json()["now"]

    assert [item["body"] for item in now[:2]] == ["あとから入れた様子です。", "先に入れた様子です。"]


def test_今の様子を書いたのは入れた人で_本人の言葉にならない(conn, login, users):
    response = login("田中さん").post("/api/observations", json={"text": "言葉を聞いて、うなずかれました。", "at_meeting": True})

    statement, _ = _utterance_of(conn, response.json()["statement_id"])
    assert statement.speaker_user_id == users["田中さん"].id
    assert statement.subject_user_id == db.self_user(conn).id
    assert statement.source is not Source.SELF_WORDS


def test_託されていない人は会議の今の様子を入れられない(login):
    assert login("節子さん").post("/api/observations", json={"text": "元気でした。", "at_meeting": True}).status_code == 404


def test_会議の画面の部品を配る(login):
    client = login("田中さん")

    assert client.get("/meeting.js").status_code == 200
    assert client.get("/speech.js").status_code == 200


STATIC = Path(__file__).resolve().parent.parent / "static"
_IMPORT = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"\./([\w.]+)"')
_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)")


def test_画面の部品が読み込む名前は_相手のファイルに実在する():
    """読み込む名前が1つでもないと、画面全体が開かなくなる。ブラウザを開かなくても気づけるようにする。"""
    missing = []
    for path in STATIC.glob("*.js"):
        for names, target in _IMPORT.findall(path.read_text(encoding="utf-8")):
            exported = set(_EXPORT.findall((STATIC / target).read_text(encoding="utf-8")))
            for name in (part.strip() for part in names.split(",")):
                if name and name not in exported:
                    missing.append(f"{path.name} が {target} の {name} を読み込む")

    assert missing == []
