"""1枚の紙のテスト。

役割：ツインの代弁、裏付けの本人の言葉、ケアプラン原案、会議の言葉が1枚に載ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）、10（見える範囲）、
          15（ツインのいない部屋の発言は AI に送らない）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import advocacy_sheet, ai, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Theme
from tests.fake_claude import RoleClaude

RICE = "冷や飯なんざ食えるか。飯は炊きたてが一番たい。"
BATH = "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1, 2], "related_numbers": [], "request_kind": "通常"}
    fake.answers["ツインのアドボカシー"] = _draft()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _draft(**changes):
    return {
        "claim": "誠一さんにとって家は建物ではなく、和子さんとの暮らしそのものです。",
        "topics": [{"title": "ご飯", "text": "炊きたては、自分の手で暮らしを保つ証しです。", "evidence_numbers": [1]}],
        "change": {"text": "拒んでいた人の手を、条件つきで受け入れるようになりました。", "evidence_numbers": [1, 2]},
        "self_intent": "この家で、自分の手で炊いたご飯を食べて暮らしたい。",
        "long_term_goal": "人の手を借りながら、台所の主役でいる。",
        "plan_evidence_numbers": [1],
        "requests": ["家か施設かの二択にしないでください。"],
        "ask_back": "美穂さんの負担がいちばん重いのはどこですか？",
        **changes,
    }


@pytest.fixture
def words(conn, add_statement):
    rice = add_statement(RICE, scope=ShareScope.DECISION_SUPPORT, theme=Theme.LIKES, effective_date=date(2026, 10, 11))
    bath = add_statement(BATH, scope=ShareScope.DECISION_SUPPORT, theme=Theme.LIKES, effective_date=date(2026, 10, 18))
    for statement, year in ((rice, 2026), (bath, 2027)):
        model_store.add_element(
            conn,
            kind=ElementKind.VALUE,
            body="暮らしのこだわりです。",
            condition=None,
            petals=[],
            confidence=0.7,
            status=ElementStatus.AI_VIEW,
            valid_from=date(year, 1, 1),
            evidence_statement_ids=[statement.id],
            unit=ConsolidationUnit.YEAR,
        )
    conn.commit()
    return rice, bath


def _meeting(login, users, *lines, twin=True):
    """ケア会議の部屋を作り、発言を順に入れる。lines は (話す人, 文) の組。"""
    tanaka = login("田中さん")
    room = tanaka.post(
        "/api/rooms",
        json={"kind": "グループ", "name": "ケア会議", "member_ids": [users["誠一さん"].id, users["美穂さん"].id]},
    ).json()
    if twin:
        assert login("誠一さん").post(f"/api/rooms/{room['id']}/twin", json={"present": True}).status_code == 200
    for index, (speaker, text) in enumerate(lines):
        response = login(speaker).post(f"/api/rooms/{room['id']}/messages", json={"text": text, "client_key": f"m{index}"})
        assert response.status_code == 200
    return room


def _sheet(client, **body):
    return client.post("/api/advocacy/sheet", json=body)


def test_紙にはツインの代弁と根拠の言葉が本文のまま載る(login, words, claude):
    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert sheet["claim"].startswith("誠一さんにとって家は")
    topic = sheet["topics"][0]
    assert topic["title"] == "ご飯"
    assert topic["evidence"][0]["body"] == RICE
    assert topic["evidence"][0]["date"] == "2026-10-11"
    assert [quote["body"] for quote in sheet["change"]["evidence"]] == [RICE, BATH]
    assert sheet["requests"] == ["家か施設かの二択にしないでください。"]
    assert sheet["ask_back"].startswith("美穂さんの負担")


def test_話題ごとに材料を集めて_ツインの文は1回で作る(login, words, claude):
    _sheet(login("田中さん"))

    assert len(claude.bodies("ツインの答え")) == len(advocacy_sheet.TOPICS)
    assert len(claude.bodies("ツインのアドボカシー")) == 1


def test_渡していない番号の根拠は本文にならない(login, words, claude):
    claude.answers["ツインのアドボカシー"] = _draft(
        topics=[{"title": "ご飯", "text": "読みです。", "evidence_numbers": [99]}], plan_evidence_numbers=[99]
    )

    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert sheet["topics"][0]["evidence"] == []
    assert sheet["plan"]["evidence"] == []
    assert sheet["plan"]["verdict"] == "根拠なし"


@pytest.mark.parametrize(
    "changes",
    [{"claim": "誠一さんは「炊きたて」と言います。"}, {"self_intent": "本人は「炊きたて」と言う。"}, {"requests": ["「帰らん」と言うとります。"]}],
)
def test_鉤括弧の文は作り直しても通らなければ紙にしない(login, words, claude, conn, changes):
    claude.answers["ツインのアドボカシー"] = _draft(**changes)

    assert _sheet(login("田中さん")).status_code == 503
    assert conn.execute("SELECT COUNT(*) FROM advocacy_sheets").fetchone()[0] == 0


def test_言い切りの文は止めない(login, words, claude):
    claude.answers["ツインのアドボカシー"] = _draft(claim="誠一さんの本心は、この家で暮らし続けることです。")

    assert _sheet(login("田中さん")).json()["sheet"]["claim"] == "誠一さんの本心は、この家で暮らし続けることです。"


def test_原案は原案の表に残り_判定は根拠の出どころからプログラムが決める(conn, login, words, claude):
    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert sheet["plan"]["verdict"] == "本人の言葉で裏付けあり"
    row = conn.execute("SELECT * FROM care_plan_drafts").fetchone()
    assert row["self_intent"] == sheet["plan"]["self_intent"]
    assert row["verdict"] == "本人の言葉で裏付けあり"
    assert len(json.loads(row["evidence_statement_ids"])) == 1


def test_作り直すと原案は前の版を指す(conn, login, words, claude):
    client = login("田中さん")
    _sheet(client)
    _sheet(client, again=True)

    rows = conn.execute("SELECT id, previous_version_id FROM care_plan_drafts ORDER BY id").fetchall()
    assert rows[1]["previous_version_id"] == rows[0]["id"]


def test_作ってあればAIを呼ばずに返す(login, words, claude):
    client = login("田中さん")
    first = _sheet(client).json()["sheet"]
    calls = len(claude.bodies("ツインのアドボカシー"))

    second = _sheet(client).json()["sheet"]
    read = client.get("/api/advocacy/sheet").json()["sheet"]

    assert second["id"] == first["id"]
    assert read["id"] == first["id"]
    assert len(claude.bodies("ツインのアドボカシー")) == calls


def test_まだ紙がなければ空を返す(login, words, claude):
    assert login("田中さん").get("/api/advocacy/sheet").json() == {"sheet": None}


def test_会議の言葉は問いかけと組で載り_本文は部屋の発言のまま(login, users, words, claude):
    _meeting(
        login,
        users,
        ("田中さん", "2026年10月11日のつぶやきを読みます。今も同じお気持ちですか。"),
        ("誠一さん", "同じたい。"),
    )

    pair = _sheet(login("田中さん")).json()["sheet"]["meeting"][0]

    assert pair["asked"]["speaker"] == "田中さん"
    assert pair["asked"]["text"].endswith("今も同じお気持ちですか。")
    assert pair["answer"]["text"] == "同じたい。"


def test_会議のあとの言葉は紙を作り直さなくても載り_AIを呼ばない(login, users, words, claude):
    client = login("田中さん")
    _sheet(client)
    calls = len(claude.bodies("ツインのアドボカシー"))

    _meeting(login, users, ("田中さん", "続けたいですか。"), ("誠一さん", "炊きたてなら、よか。"))

    read = client.get("/api/advocacy/sheet").json()["sheet"]
    assert read["meeting"][0]["answer"]["text"] == "炊きたてなら、よか。"
    assert len(claude.bodies("ツインのアドボカシー")) == calls


def test_大切なものの欄は問いへの本人の返事をそのまま載せる(login, users, words, claude):
    _meeting(
        login,
        users,
        ("田中さん", "声は一回でよいですか。"),
        ("誠一さん", "声は一回。"),
        ("田中さん", "計画書に「大切なもの」を書く欄があります。一つ教えてください。"),
        ("誠一さん", "親父の弁当箱。蓋に凹みがある。"),
    )

    assert _sheet(login("田中さん")).json()["sheet"]["treasure"]["text"] == "親父の弁当箱。蓋に凹みがある。"


def test_聞いていなければ大切なものの欄は空(login, words, claude):
    assert _sheet(login("田中さん")).json()["sheet"]["treasure"] is None


def test_ツインのいない部屋の発言はAIに送らない(login, users, words, claude):
    _meeting(login, users, ("田中さん", "人だけの問いかけです。"), ("誠一さん", "人だけの返事です。"), twin=False)

    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert "人だけの返事です。" not in json.dumps(claude.bodies("ツインのアドボカシー"), ensure_ascii=False)
    assert sheet["meeting"][0]["answer"]["text"] == "人だけの返事です。"


def test_ツインのいる部屋の発言はAIも読む(login, users, words, claude):
    _meeting(login, users, ("田中さん", "続けたいですか。"), ("誠一さん", "炊きたてなら、よか。"))

    _sheet(login("田中さん"))

    assert "本人：炊きたてなら、よか。" in json.dumps(claude.bodies("ツインのアドボカシー"), ensure_ascii=False)


def test_入っていない部屋は指定できない(login, users, words, claude):
    room = login("誠一さん").post(
        "/api/rooms", json={"kind": "1対1", "member_ids": [users["正雄さん"].id]}
    ).json()

    assert _sheet(login("田中さん"), meeting_room_id=room["id"]).status_code == 404


def test_託されていない人は開けない(login, words, claude):
    client = login("節子さん")

    assert _sheet(client).status_code == 404
    assert client.get("/api/advocacy/sheet").status_code == 404


def test_今の様子を読み直して載せる(login, words, claude):
    client = login("田中さん")
    _sheet(client)
    client.post("/api/observations", json={"text": "縁側で庭を見ておられました。"})

    assert client.get("/api/advocacy/sheet").json()["sheet"]["now"][0]["body"] == "縁側で庭を見ておられました。"


def test_紙の画面の部品を配る(login):
    client = login("田中さん")

    assert client.get("/sheet.html").status_code == 200
    assert client.get("/sheet.js").status_code == 200
    assert client.get("/sheet-print.css").status_code == 200


def test_紙に決める人の文と読んだ記録を出す(login, words, claude):
    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert sheet["decide"] == "決めるのは、ご本人とご家族と専門職です。"
    assert sheet["reading"].startswith("読んだ記録：")


def test_本人の意向の欄は本人の言葉を原文で載せ_ツインの案と分ける(login, words, claude):
    sheet = _sheet(login("田中さん")).json()["sheet"]

    assert [item["body"] for item in sheet["plan"]["own_words"]] == [RICE]
    assert sheet["plan"]["self_intent"] == "この家で、自分の手で炊いたご飯を食べて暮らしたい。"


def test_紙のツインの文に服薬の指示が残れば紙を出さない(login, words, claude):
    claude.answers["ツインのアドボカシー"] = _draft(claim="ツインは、薬の量を減らしてくださいと考えます。")

    assert _sheet(login("田中さん")).status_code == 503


def test_頼んだ専門職は1枚の紙を削除でき_読み直しても出ない(login, words, claude):
    tanaka = login("田中さん")
    sheet_id = _sheet(tanaka).json()["sheet"]["id"]

    assert tanaka.delete(f"/api/advocacy/made/sheet/{sheet_id}").status_code == 204

    assert tanaka.get("/api/advocacy/sheet").json()["sheet"] is None
