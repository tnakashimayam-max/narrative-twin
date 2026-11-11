"""生活まとめプランのテスト。

役割：ツイン（AI）の下書きを Opus で作り、紙の行に当てはめること、専門職が直すと版が足されること、
     本人と家族には作れも見えもしないこと、入力した記録が材料に入ること、札にツイン（AI）と出ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：1（決めるのは人）、5（直しても前の版は残す）、12（紙に必ず「ツイン（AI）」と出す）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import ai, life_plan, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Theme
from tests.fake_claude import RoleClaude

RICE = "冷や飯なんざ食えるか。飯は炊きたてが一番たい。"


def _draft(rows=None):
    return {
        "rows": rows
        if rows is not None
        else [
            {"area": "就寝・起床", "part": None, "past": "雨戸を開けて朝を迎えてきた。", "can": "自分で起きる。", "cannot": "", "support": "朝の様子を、短い便りで見てほしか。", "who": "長女"},
            {"area": "食事", "part": "朝食", "past": "炊きたての飯が芯。", "can": "米を研ぐ。", "cannot": "炊飯器の最後のボタンが抜ける。", "support": "手順の紙で、自分で炊き続けたい。", "who": "本人、長女"},
        ],
        "piece_numbers": [],
    }


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    fake.answers["生活まとめプラン"] = _draft()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def words(conn, add_statement):
    rice = add_statement(RICE, scope=ShareScope.DECISION_SUPPORT, theme=Theme.LIKES, effective_date=date(2026, 10, 11))
    model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="炊きたてのご飯を大事にしている。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=[rice.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    return rice


def _make(client, key="p1"):
    return client.post("/api/advocacy/life-plans", json={"client_key": key})


def test_下書きをOpusで作り_紙の行に当てはめる(login, words, claude):
    response = _make(login("田中さん"))

    assert response.status_code == 201
    plan = response.json()["plan"]
    assert len(plan["rows"]) == len(life_plan.slots()) == 18
    assert plan["rows"][0] == {"area": "就寝・起床", "part": None, "past": "雨戸を開けて朝を迎えてきた。", "can": "自分で起きる。", "cannot": "", "support": "朝の様子を、短い便りで見てほしか。", "who": "長女"}
    breakfast = next(row for row in plan["rows"] if row["part"] == "朝食")
    assert breakfast["cannot"] == "炊飯器の最後のボタンが抜ける。"
    # 書かれなかった場面は空の行で残る
    assert next(row for row in plan["rows"] if row["area"] == "住環境") == {"area": "住環境", "part": None, **dict.fromkeys(life_plan.COLUMNS, "")}
    assert claude.bodies("生活まとめプラン")[0]["model"] == "claude-opus-5-5"
    assert "ツイン（AI）" in plan["label"]


def test_紙にない場面は捨て_書かれなかった場面は空の行にする():
    rows = life_plan.fit_rows(
        [
            {"area": "宇宙旅行", "past": "行った。"},
            {"area": "朝食", "part": None, "can": "米を研ぐ。"},
            {"area": "服 薬", "who": "長女"},
        ]
    )

    assert len(rows) == 18
    assert all(row["area"] != "宇宙旅行" for row in rows)
    assert next(row for row in rows if row["part"] == "朝食")["can"] == "米を研ぐ。"
    assert next(row for row in rows if row["area"] == "服薬")["who"] == "長女"
    assert next(row for row in rows if row["area"] == "入浴")["past"] == ""


def test_札にツインと直した人を書く():
    assert life_plan.label_for([]).startswith("ツイン（AI）が記録から下書きしました。")
    assert life_plan.label_for(["田中"]).endswith("田中さんが修正しました。")


def test_直すと新しい版になり_前の版は残る(conn, login, words, claude):
    tanaka = login("田中さん")
    plan = _make(tanaka).json()["plan"]
    rows = plan["rows"]
    rows[0]["support"] = "朝、一言だけ便りを送る。"

    edited = tanaka.post(f"/api/advocacy/life-plans/{plan['id']}/edit", json={"rows": rows}).json()["plan"]

    assert edited["rows"][0]["support"] == "朝、一言だけ便りを送る。"
    assert edited["version"] == 2
    assert edited["edited"] is True
    assert "田中さんが修正しました" in edited["label"]
    assert conn.execute("SELECT COUNT(*) FROM life_plan_versions WHERE plan_id = ?", (plan["id"],)).fetchone()[0] == 2


def test_同じclient_keyならAIを呼ばない(login, words, claude):
    tanaka = login("田中さん")
    _make(tanaka)
    _make(tanaka)

    assert len(claude.bodies("生活まとめプラン")) == 1


def test_本人と家族には作れず_見えない(login, words, claude):
    plan = _make(login("田中さん")).json()["plan"]

    for name in ("誠一さん", "美穂さん"):
        client = login(name)
        assert _make(client, key=f"x-{name}").status_code == 404
        assert client.get("/api/advocacy/life-plans").status_code == 404
        assert client.get(f"/api/advocacy/life-plans/{plan['id']}").status_code == 404


def test_入力した記録を下書きの材料に渡す(login, words, claude):
    tanaka = login("田中さん")
    tanaka.post(
        "/api/advocacy/care-forms/records",
        json={"form_id": "assessment", "record_date": "2036-04-02", "values": {"調理": "一部介助"}, "client_key": "r1"},
    )

    _make(tanaka)

    sent = str(claude.bodies("生活まとめプラン"))
    assert "調理：一部介助" in sent
    assert "本人と家族には見せない記録です" in sent


def test_白紙の書式はログインなしで返す(login):
    client = login("田中さん")
    del client.headers["X-NT-User"]

    form = client.get("/api/life-plan-form").json()

    assert form["form"]["title"] == "生活まとめプラン"
    assert len(form["form"]["rows"]) == 15
    assert "ツイン（AI）" in form["label"]


def test_画面の部品を配る(login):
    client = login("田中さん")
    for name in ("life_plan.html", "life_plan.js", "life-plan.css", "care_form.html", "care_form.js"):
        assert client.get(f"/{name}").status_code == 200


def test_紙のご利用者名は_入力したフェイスシートの氏名を先に使う(login, words, claude):
    tanaka = login("田中さん")
    assert _make(tanaka).json()["plan"]["person_name"] == "誠一"
    tanaka.post(
        "/api/advocacy/care-forms/records",
        json={"form_id": "face_sheet", "record_date": "2036-04-02", "values": {"氏名": "鈴木　誠一"}, "client_key": "f1"},
    )

    assert tanaka.get("/api/advocacy/life-plans").json()["items"][0]["person_name"] == "鈴木　誠一"


def test_頼んだ専門職は生活まとめプランを削除でき_一覧と中身から消える(login, words, claude):
    tanaka = login("田中さん")
    plan_id = _make(tanaka).json()["plan"]["id"]

    assert tanaka.delete(f"/api/advocacy/made/life_plan/{plan_id}").status_code == 204

    assert tanaka.get("/api/advocacy/life-plans").json()["items"] == []
    assert tanaka.get(f"/api/advocacy/life-plans/{plan_id}").status_code == 404


def test_作成者は作った時点ではツインで_直すと直した人を添える(login, words, claude):
    tanaka = login("田中さん")
    plan = _make(tanaka).json()["plan"]
    assert plan["author"] == "ツイン（AI）"
    assert plan["editors"] == []

    rows = plan["rows"]
    rows[0]["who"] = "本人"
    edited = tanaka.post(f"/api/advocacy/life-plans/{plan['id']}/edit", json={"rows": rows}).json()["plan"]

    assert edited["author"] == "ツイン（AI）"
    assert edited["editors"] == ["田中"]
