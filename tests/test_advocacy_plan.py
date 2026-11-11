"""ケアプラン案へのツインの異議のテスト。

役割：第1版、ツインの異議、第2版が順に作られ、根拠の本人の言葉が本文のまま添えられることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、5（前の版を消さない）、9（本人の言葉は記録の本文のまま）、10（見える範囲）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import ai, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Theme
from tests.fake_claude import RoleClaude

RICE = "冷や飯なんざ食えるか。飯は炊きたてが一番たい。"
PRIVATE = "兄貴、俺が悪かった。"
PLAN = "週2回のデイサービスと配食弁当"


def _objection(**changes):
    return {
        "claim": "ツインは配食弁当に反対します。誠一さんに合いません。",
        "points": [{"target": "配食弁当", "objection": "炊きたてのご飯は暮らしの柱です。", "evidence_numbers": [1]}],
        "requests": ["夕方に一緒に炊く形を考えてください。"],
        "ask_back": "美穂さんの負担がいちばん重いのはどこですか？",
        **changes,
    }


def _second(**changes):
    return {
        "summary": "夕方に人が入り、一緒に炊く案です。",
        "self_intent": "この家で、炊きたてのご飯を食べて暮らしたい。",
        "long_term_goal": "人の手を借りながら、台所の主役でいる。",
        "items": [
            {"aim": "夕方のご飯", "support": "同じヘルパーが来て一緒に炊く。", "why": "役割を奪わないためです。", "evidence_numbers": [1]}
        ],
        **changes,
    }


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    fake.answers["根拠チェック"] = _objection()
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


def _first(client, key="p1", text=PLAN):
    return client.post("/api/advocacy/plans", json={"client_key": key, "text": text})


def _step(client, plan_id, step, **body):
    return client.post(f"/api/advocacy/plans/{plan_id}/{step}", json=body)


def test_田中さんが入れた案はAIを呼ばず第1版になる(login, words, claude):
    plan = _first(login("田中さん")).json()["plan"]

    assert plan["first"]["summary"] == PLAN
    assert plan["first"]["source"] == "田中さんが入れた案"
    assert plan["objection"] is None
    assert plan["second"] is None
    assert claude.bodies("原案") == []


def test_案を入れなければ_本人の言葉を読まずにAIが一般的な案を作る(login, words, claude):
    client = login("田中さん")
    client.post("/api/observations", json={"text": "縁側で庭を見ておられました。"})
    claude.answers["原案"] = {"summary": "デイサービスと配食の案です。", "items": [{"aim": "食事", "support": "配食を使う。"}]}

    plan = _first(client, text=None).json()["plan"]

    assert plan["first"]["source"] == "本人の言葉を読まずに AI が作った一般的な案"
    assert plan["first"]["items"] == [{"aim": "食事", "support": "配食を使う。"}]
    sent = json.dumps(claude.bodies("原案"), ensure_ascii=False)
    assert RICE not in sent
    assert "縁側で庭を見ておられました。" in sent


def test_同じ印で送り直しても案は増えない(conn, login, words, claude):
    client = login("田中さん")
    first = _first(client).json()["plan"]
    second = _first(client).json()["plan"]

    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM advocacy_plans").fetchone()[0] == 1


def test_ツインの異議には根拠の言葉が本文のまま添えられる(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    objection = _step(client, plan["id"], "objection").json()["plan"]["objection"]

    assert objection["claim"].startswith("ツインは配食弁当に反対します")
    assert objection["points"][0]["evidence"][0]["body"] == RICE
    assert objection["points"][0]["evidence"][0]["date"] == "2026-10-11"
    assert objection["verdict"] == "本人の言葉で裏付けあり"
    assert objection["ask_back"].startswith("美穂さんの負担")


def test_異議の問いには案の文を渡す(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    _step(client, plan["id"], "objection")

    assert PLAN in json.dumps(claude.bodies("根拠チェック"), ensure_ascii=False)


def test_渡していない番号の根拠は本文にならず_判定は根拠なしになる(login, words, claude):
    claude.answers["根拠チェック"] = _objection(points=[{"target": "案", "objection": "合いません。", "evidence_numbers": [99]}])
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    objection = _step(client, plan["id"], "objection").json()["plan"]["objection"]

    assert objection["points"][0]["evidence"] == []
    assert objection["verdict"] == "根拠なし"


def test_鉤括弧の異議は通らず_案は残る(conn, login, words, claude):
    claude.answers["根拠チェック"] = _objection(claim="誠一さんは「弁当は食わん」と言います。")
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    assert _step(client, plan["id"], "objection").status_code == 503
    assert client.get("/api/advocacy/plans").json()["plan"]["first"]["summary"] == PLAN


def test_異議の前には書き直せない(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    assert _step(client, plan["id"], "revise").status_code == 400


def test_第2版は第1版と異議を読んで作り_版を並べて返す(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]
    _step(client, plan["id"], "objection")
    claude.answers["原案"] = _second()

    full = _step(client, plan["id"], "revise").json()["plan"]

    assert full["first"]["summary"] == PLAN
    assert full["objection"]["claim"].startswith("ツインは配食弁当に反対します")
    assert full["second"]["summary"] == "夕方に人が入り、一緒に炊く案です。"
    assert full["second"]["items"][0]["evidence"][0]["body"] == RICE
    sent = json.dumps(claude.bodies("原案"), ensure_ascii=False)
    assert PLAN in sent
    assert "ツインは配食弁当に反対します" in sent


def test_第2版は原案の表に残り_判定はプログラムが決める(conn, login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]
    _step(client, plan["id"], "objection")
    claude.answers["原案"] = _second()

    _step(client, plan["id"], "revise")

    row = conn.execute("SELECT * FROM care_plan_drafts").fetchone()
    assert row["self_intent"] == "この家で、炊きたてのご飯を食べて暮らしたい。"
    assert row["verdict"] == "本人の言葉で裏付けあり"


def test_作ってあればAIを呼ばず_作り直すと前の版は記録に残る(conn, login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]
    _step(client, plan["id"], "objection")
    calls = len(claude.bodies("根拠チェック"))

    _step(client, plan["id"], "objection")
    assert len(claude.bodies("根拠チェック")) == calls

    claude.answers["根拠チェック"] = _objection(claim="2回目の異議です。")
    again = _step(client, plan["id"], "objection", again=True).json()["plan"]
    assert again["objection"]["claim"] == "2回目の異議です。"
    assert conn.execute("SELECT COUNT(*) FROM inference_logs WHERE kind = '根拠チェック'").fetchone()[0] == 2


def test_今は話したくないにした記録は異議の材料に入らない(conn, login, users, words, add_statement, claude):
    hidden = add_statement(PRIVATE, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    ledger.rest_statement(conn, hidden.id, actor_user_id=users["誠一さん"].id)
    conn.commit()
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    _step(client, plan["id"], "objection")

    assert PRIVATE not in json.dumps(claude.bodies("根拠チェック"), ensure_ascii=False)


def _ready_plan(client, claude):
    plan = _first(client).json()["plan"]
    _step(client, plan["id"], "objection")
    claude.answers["原案"] = _second()
    return _step(client, plan["id"], "revise").json()["plan"]


def test_どの版を使うかは人が選び_選んだ人が記録される(login, words, claude):
    client = login("田中さん")
    plan = _ready_plan(client, claude)

    chosen = client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第2版"}).json()["plan"]

    assert chosen["choice"]["version"] == "第2版"
    assert chosen["choice"]["chooser"] == "田中さん"
    assert client.get("/api/advocacy/plans").json()["plan"]["choice"]["version"] == "第2版"


def test_ツインが異議を唱えても_人が第1版を選べる(login, words, claude):
    client = login("田中さん")
    plan = _ready_plan(client, claude)

    chosen = client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第1版"}).json()["plan"]

    assert chosen["choice"]["version"] == "第1版"
    assert chosen["second"] is not None


def test_人が選ぶまで版は選ばれない(login, words, claude):
    assert _ready_plan(login("田中さん"), claude)["choice"] is None


def test_版を選ぶときはAIを呼ばない(login, words, claude):
    client = login("田中さん")
    plan = _ready_plan(client, claude)
    calls = len(claude.requests)

    client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "どちらも使わない"})

    assert len(claude.requests) == calls


def test_選び直しても前の選択は残り_新しい選択が出る(conn, login, words, claude):
    client = login("田中さん")
    plan = _ready_plan(client, claude)
    client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第1版"})

    chosen = client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第2版"}).json()["plan"]

    assert chosen["choice"]["version"] == "第2版"
    rows = conn.execute("SELECT version FROM advocacy_plan_choices ORDER BY rowid").fetchall()
    assert [row["version"] for row in rows] == ["第1版", "第2版"]


def test_第2版がないのに第2版は選べない(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    assert client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第2版"}).status_code == 400


def test_決めた言葉のほかは版として選べない(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]

    assert client.post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第3版"}).status_code == 422


def test_ほかの人の案の版は選べない(login, words, claude):
    plan = _first(login("田中さん")).json()["plan"]

    assert login("美穂さん").post(f"/api/advocacy/plans/{plan['id']}/choose", json={"version": "第1版"}).status_code == 404


def test_案の画面には_決めるのは人と書いた選ぶ場所がある(login):
    script = login("田中さん").get("/plan.js").text

    assert "ツイン（AI）は選びません" in script
    assert "どちらも使わない" in script


def test_ほかの人の案は開けない(login, words, claude):
    plan = _first(login("田中さん")).json()["plan"]

    assert _step(login("美穂さん"), plan["id"], "objection").status_code == 404


def test_託されていない人は作れない(login, words, claude):
    client = login("節子さん")

    assert _first(client).status_code == 404
    assert client.get("/api/advocacy/plans").status_code == 404


def test_まだ案がなければ空を返す(login, words, claude):
    assert login("田中さん").get("/api/advocacy/plans").json() == {"plan": None}


def test_長すぎる案は分かる言葉で止める(login, words, claude):
    response = _first(login("田中さん"), text="案" * 2001)

    assert response.status_code == 400


def test_案の画面の部品を配る(login):
    client = login("田中さん")

    assert client.get("/plan.html").status_code == 200
    assert client.get("/plan.js").status_code == 200


def test_案の画面には例の案からすぐ始める入口がある(login):
    client = login("田中さん")

    assert "「週2回のデイサービスと配食弁当」で始める" in client.get("/plan.html").text
    assert f'EXAMPLE_PLAN = "{PLAN}"' in client.get("/plan.js").text


def test_第2版の本人の意向は本人の言葉を原文で載せ_ツインの案と分ける(login, words, claude):
    plan = _ready_plan(login("田中さん"), claude)

    own = plan["second"]["plan"]["own_words"]
    assert own
    assert all(item["source"] == "本人の言葉" for item in own)
    assert plan["second"]["plan"]["self_intent"] == _second()["self_intent"]


def test_異議のツインの文に診断が残れば異議を出さない(login, words, claude):
    client = login("田中さん")
    plan = _first(client).json()["plan"]
    claude.answers["根拠チェック"] = _objection(claim="ツインは、血圧が高い方ですねと見ます。")

    assert _step(client, plan["id"], "objection").status_code == 503
    assert len(claude.bodies("根拠チェック")) == 2


def test_異議に読んだ記録の期間と件数を添える(login, words, claude):
    plan = _ready_plan(login("田中さん"), claude)

    assert plan["objection"]["reading"].startswith("読んだ記録：")


def test_頼んだ専門職はケアプラン案を削除でき_ほかの人は消せない(login, words, claude):
    tanaka = login("田中さん")
    plan_id = _first(tanaka).json()["plan"]["id"]

    assert login("美穂さん").delete(f"/api/advocacy/made/plan/{plan_id}").status_code == 404
    assert tanaka.delete(f"/api/advocacy/made/plan/{plan_id}").status_code == 204

    assert tanaka.get("/api/advocacy/plans").json()["plan"] is None
    assert _step(tanaka, plan_id, "objection").status_code == 404
