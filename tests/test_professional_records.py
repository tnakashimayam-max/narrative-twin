"""専門職がツインに伝えること（チャットと資料）のテスト。

役割：伝えた文と資料が項目に分かれ、専門職が残した項目だけが今の様子として台帳に入ることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：1（残すかは人が決める）、3（今の様子は本人の言葉と分ける）、5（台帳は足すだけ）、
          9（資料の中の「本人：〜」は聞き取りで、本人の言葉にしない）。

通信だけを差し替える。本物の Claude API は呼ばない。資料は架空の小さな PDF を使う。
"""

from __future__ import annotations

import base64
import dataclasses
import sqlite3

import pytest

from app import ai, ledger, professional_records
from app.schemas import ShareScope, Source
from app import consent, db
from tests.fake_claude import RoleClaude

PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF\n"
SEEN = "炊飯器の最後のボタンを押せていなかった（11月6日の訪問）"
HEARD = "本人は、押したと言っていた"


def _items(*items):
    return {"items": [{"kind": kind, "text": text, "about": about} for kind, text, about in items]}


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["伝えを分ける"] = _items(("観察", SEEN, "誠一さん"), ("本人の思いの聞き取り", HEARD, "誠一さん"))
    fake.answers["資料を読む"] = _plan_read()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _plan_read(**changes):
    return {
        "kind": "ケアプラン",
        "document_date": "2036-05-01",
        "author": "田中（居宅介護支援）",
        "person_name": "誠一",
        "title": "居宅サービス計画書",
        "summary": "週2回のデイサービスで、人との交流を増やす計画です。",
        "items": [
            {"kind": "観察", "text": "日中は一人で過ごすことが多い", "about": "誠一さん", "page": 1},
            {"kind": "本人の思いの聞き取り", "text": "デイサービスで友人を作りたい", "about": "誠一さん", "page": 1},
            {"kind": "家族の思いの聞き取り", "text": "一人暮らしが心配", "about": "美穂さん", "page": 1},
        ],
        "care_plan": {
            "self_intent": "デイサービスに通って、友人を作りたい。",
            "family_intent": "一人暮らしが心配なので、日中は人の目があるところにいてほしい。",
            "policy": "通所で交流の機会を作り、閉じこもりを防ぐ。",
            "needs": [
                {
                    "need": "人との交流が少ない",
                    "long_term_goal": "地域の人とつながって暮らす",
                    "short_term_goal": "週2回、通所する",
                    "services": ["通所介護 週2回"],
                }
            ],
        },
        "has_medical": True,
        **changes,
    }


def _observations(conn) -> list:
    return [item for item in ledger.current_statements(conn) if item.source is Source.OBSERVATION]


def _tell(client, text="今日伺ったら、炊飯器のボタンを押せていませんでした。", key="t1"):
    return client.post("/api/advocacy/tell", json={"text": text, "client_key": key})


def _upload(client, data=PDF, key="d1", name="計画書.pdf"):
    return client.post(
        f"/api/advocacy/documents?client_key={key}&file_name={name}",
        content=data,
        headers={"content-type": "application/pdf"},
    )


def test_伝えた文は項目に分かれ残すまで台帳に入らない(conn, login, claude):
    tanaka = login("田中さん")
    tell = _tell(tanaka).json()["tell"]
    assert [item["kind"] for item in tell["items"]] == ["観察", "本人の思いの聞き取り"]
    assert all(item["status"] == "確かめ待ち" for item in tell["items"])
    assert all(item["about"] == "誠一さん" for item in tell["items"])
    assert _observations(conn) == []

    kept = tanaka.post(f"/api/advocacy/items/{tell['items'][0]['id']}/decide", json={"decision": "残す"}).json()["item"]
    dropped = tanaka.post(f"/api/advocacy/items/{tell['items'][1]['id']}/decide", json={"decision": "残さない"}).json()["item"]

    assert kept["status"] == "残した"
    assert dropped["status"] == "残さない"
    statements = _observations(conn)
    assert [item.body for item in statements] == [SEEN]
    assert statements[0].speaker_user_id == 6
    assert statements[0].share_scope is ShareScope.DECISION_SUPPORT
    # どこから来た様子かを、会話のきっかけで残す
    utterance = ledger.get_utterance(conn, statements[0].source_utterance_ids[0])
    trigger = conn.execute("SELECT trigger FROM conversations WHERE id = ?", (utterance.conversation_id,)).fetchone()[0]
    assert trigger == professional_records.TELL_TRIGGER


def test_聞き取りの項目は聞き取りと分かる文で残す(conn, login, claude):
    tanaka = login("田中さん")
    item = _tell(tanaka).json()["tell"]["items"][1]
    tanaka.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す"})
    assert [statement.body for statement in _observations(conn)] == [f"{HEARD}（本人の思いの聞き取り）"]


def test_直して残すと直した文が台帳に入る(conn, login, claude):
    tanaka = login("田中さん")
    item = _tell(tanaka).json()["tell"]["items"][0]
    tanaka.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す", "text": "夕方、炊飯器の最後のボタンを押せていなかった"})
    assert [statement.body for statement in _observations(conn)] == ["夕方、炊飯器の最後のボタンを押せていなかった"]


def test_決めた項目は決め直せない(conn, login, claude):
    tanaka = login("田中さん")
    item = _tell(tanaka).json()["tell"]["items"][0]
    tanaka.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残さない"})
    again = tanaka.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す"}).json()["item"]
    assert again["status"] == "残さない"
    assert _observations(conn) == []


def test_同じ送り直しでは増やさずAIも呼ばない(login, claude):
    tanaka = login("田中さん")
    first = _tell(tanaka).json()["tell"]
    second = _tell(tanaka).json()["tell"]
    assert first["id"] == second["id"]
    assert len(claude.bodies("伝えを分ける")) == 1
    assert len(tanaka.get("/api/advocacy/tells").json()["items"]) == 1


def test_ほかの人の伝えの項目は決められない(login, claude):
    item = _tell(login("田中さん")).json()["tell"]["items"][0]
    response = login("美穂さん").post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す"})
    assert response.status_code == 404


def test_チャットの伝える語は伝える処理へ渡す(login, claude):
    reply = login("田中さん").post(
        "/api/advocacy/chat",
        json={"question": "今日伺ったら、炊飯器のボタンを押せていませんでした。お伝えします。", "client_key": "c1"},
    ).json()
    assert reply["kind"] == "伝える"
    assert len(reply["tell"]["items"]) == 2
    assert claude.bodies("ツインのアドボカシー") == []


def test_資料はPDFの文書のブロックとして文の前に渡る(tmp_path, login, claude):
    document = _upload(login("田中さん")).json()["document"]
    body = claude.bodies("資料を読む")[0]
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"] == {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(PDF).decode("ascii")}
    assert content[1]["type"] == "text"
    assert body["model"] == "claude-sonnet-5-5"
    assert document["status"] == "確かめ待ち"
    assert document["kind"] == "ケアプラン"
    assert document["has_medical"] is True
    assert document["care_plan"]["self_intent"] == "デイサービスに通って、友人を作りたい。"
    assert [item["about"] for item in document["items"]] == ["誠一さん", "誠一さん", "美穂さん"]
    assert [item["page"] for item in document["items"]] == [1, 1, 1]
    stored = list((tmp_path / "documents").glob("*.pdf"))
    assert [path.read_bytes() for path in stored] == [PDF]


def test_資料から残した項目は資料の種類を添えて残す(conn, login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    for item in document["items"]:
        tanaka.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す"})
    bodies = [statement.body for statement in _observations(conn)]
    assert bodies == [
        "日中は一人で過ごすことが多い（ケアプランより）",
        "デイサービスで友人を作りたい（本人の思いの聞き取り）（ケアプランより）",
        "一人暮らしが心配（家族の思いの聞き取り）（ケアプランより）",
    ]
    # 聞き取りは本人の言葉として台帳に入らない
    assert all(statement.source is Source.OBSERVATION for statement in _observations(conn))
    assert tanaka.get(f"/api/advocacy/documents/{document['id']}").json()["document"]["status"] == "確かめ済み"


def test_名前が本人と合わない資料は項目を出さずに止める(login, claude):
    claude.answers["資料を読む"] = _plan_read(person_name="山田 花子")
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert document["status"] == "名前の確かめ待ち"
    assert document["items"] == []

    confirmed = tanaka.post(f"/api/advocacy/documents/{document['id']}/person-ok").json()["document"]
    assert confirmed["status"] == "確かめ待ち"
    assert len(confirmed["items"]) == 3


def test_名前の確かめの前は項目を残せない(conn, login, claude):
    claude.answers["資料を読む"] = _plan_read(person_name="山田 花子")
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    item_id = conn.execute("SELECT id FROM professional_items WHERE document_id = ?", (document["id"],)).fetchone()[0]
    response = tanaka.post(f"/api/advocacy/items/{item_id}/decide", json={"decision": "残す"})
    assert response.status_code == 400
    assert _observations(conn) == []


def test_PDFでないものは受け取らない(login, claude):
    response = _upload(login("田中さん"), data=b"hello")
    assert response.status_code == 400
    assert claude.bodies("資料を読む") == []


def test_資料を扱えるのはケアプランの道具を使える人だけ(login, claude):
    assert _upload(login("美穂さん")).status_code == 404
    assert login("美穂さん").get("/api/advocacy/documents").status_code == 404


def test_自分用の起動では資料を取り込まない(login, claude):
    tanaka = login("田中さん")
    tanaka.app.state.deployment = dataclasses.replace(tanaka.app.state.deployment, mine=True)
    response = _upload(tanaka)
    assert response.status_code == 400
    assert response.json()["detail"] == professional_records.MINE_MESSAGE
    assert claude.bodies("資料を読む") == []


def test_取り込んだケアプランをチェックへ渡す(login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    plan = tanaka.post(f"/api/advocacy/documents/{document['id']}/check").json()["plan"]
    assert plan["first"]["source"] == "取り込んだケアプラン（田中（居宅介護支援）、2036-05-01）"
    assert "本人の意向（第1表）：デイサービスに通って、友人を作りたい。" in plan["first"]["summary"]
    assert plan["first"]["items"][0]["aim"] == "ニーズ（第2表）：人との交流が少ない"
    assert plan["objection"]["claim"] == "ツインの異議です。"
    # 2回押しても、案は増やさない
    again = tanaka.post(f"/api/advocacy/documents/{document['id']}/check").json()["plan"]
    assert again["id"] == plan["id"]


def test_ケアプランでない資料はチェックできない(login, claude):
    claude.answers["資料を読む"] = _plan_read(kind="アセスメント", care_plan=None)
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/check").status_code == 400


def test_名前の比べ方():
    assert professional_records._plain("田中 誠一さん") == "田中誠一"


# ---- 見える範囲：アセスメントは家族に見せない ----


def _now_bodies(client) -> list[str]:
    return [item["body"] for item in client.get("/api/advocacy/overview").json()["now"]]


def test_アセスメントから残した記録は家族に見えない(conn, login, claude):
    claude.answers["資料を読む"] = _plan_read(kind="アセスメント", care_plan=None)
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert document["family_can_see"] is False
    tanaka.post(f"/api/advocacy/items/{document['items'][0]['id']}/decide", json={"decision": "残す"})

    [statement] = _observations(conn)
    assert statement.share_scope is ShareScope.PROFESSIONAL_ONLY
    body = "日中は一人で過ごすことが多い（アセスメントより）"
    assert body in _now_bodies(tanaka)
    assert body not in _now_bodies(login("美穂さん"))
    # 本人には見える（本人が直しながら意思を残す作品のため）
    assert statement.id in {item.id for item in consent.visible_statements(conn, db.self_user(conn))}


def test_ケアプランから残した記録は家族にも見える(conn, login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert document["family_can_see"] is True
    tanaka.post(f"/api/advocacy/items/{document['items'][0]['id']}/decide", json={"decision": "残す"})
    assert "日中は一人で過ごすことが多い（ケアプランより）" in _now_bodies(login("美穂さん"))


# ---- 消去：別の人の資料を取り込んでしまったとき ----


def _keep_all(client, document):
    for item in document["items"]:
        client.post(f"/api/advocacy/items/{item['id']}/decide", json={"decision": "残す"})


def test_消去の前に消えるものを見せる(login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _keep_all(tanaka, document)
    tanaka.post(f"/api/advocacy/documents/{document['id']}/check")
    preview = tanaka.get(f"/api/advocacy/documents/{document['id']}/erase-preview").json()["preview"]
    assert preview["can_erase"] is True
    assert preview["counts"]["資料"] == 1
    assert preview["counts"]["残した記録"] == 3
    assert preview["counts"]["ケアプランのチェック"] == 1


def test_消去すると残した記録も取り込む前に戻る(tmp_path, conn, login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _keep_all(tanaka, document)
    statement_ids = [item.id for item in _observations(conn)]
    assert len(statement_ids) == 3
    tanaka.post(f"/api/advocacy/documents/{document['id']}/check")
    # 残したあとに作ったツインの考えも、この記録を読んだかもしれないので戻す
    reply = tanaka.post("/api/advocacy/chat", json={"question": "これからの暮らしを、どう考えていると思いますか", "client_key": "q1"}).json()
    assert reply["voice"] is not None

    erased = tanaka.post(f"/api/advocacy/documents/{document['id']}/erase").json()

    assert erased["erased"]["残した記録"] == 3
    marks = ", ".join("?" for _ in statement_ids)
    assert conn.execute(f"SELECT COUNT(*) FROM statements WHERE id IN ({marks})", statement_ids).fetchone()[0] == 0
    assert _observations(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM professional_documents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM professional_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM advocacy_plans").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM advocacy_voices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM inference_logs WHERE kind = '資料の読み'").fetchone()[0] == 0
    assert list((tmp_path / "documents").glob("*.pdf")) == []
    assert tanaka.get(f"/api/advocacy/documents/{document['id']}").status_code == 404
    # 問いは残り、考えはもう一度作れる
    assert [turn["voice"] for turn in tanaka.get("/api/advocacy/turns").json()["items"]] == [None]


def test_消去のあとも台帳の削除の決まりは効く(conn, login, claude, add_statement):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _keep_all(tanaka, document)
    tanaka.post(f"/api/advocacy/documents/{document['id']}/erase")
    other = add_statement("飯は炊きたてが一番たい。")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM statements WHERE id = ?", (other.id,))
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"statements_no_delete", "utterances_no_delete"} <= names


def test_名前の確かめ待ちの資料も消去できる(login, claude):
    claude.answers["資料を読む"] = _plan_read(person_name="山田 花子")
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/erase").status_code == 200
    assert tanaka.get("/api/advocacy/documents").json()["items"] == []


def test_期限を過ぎた資料は消去できない(login, claude, monkeypatch):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    monkeypatch.setattr(professional_records, "ERASE_HOURS", -1)
    preview = tanaka.get(f"/api/advocacy/documents/{document['id']}/erase-preview").json()["preview"]
    assert preview["can_erase"] is False
    assert "消去できません" in preview["reason"]
    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/erase").status_code == 400
    assert tanaka.get(f"/api/advocacy/documents/{document['id']}").status_code == 200


def test_消去できるのは資料の道具を使える人だけ(login, claude):
    document = _upload(login("田中さん")).json()["document"]
    assert login("美穂さん").post(f"/api/advocacy/documents/{document['id']}/erase").status_code == 404


# ---- 資料へのツインのレビューと、記録の候補の場所（2026-11-06） ----


def test_資料をセルフと照らしてレビューし_作ってあれば呼ばない(login, claude):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert document["review"] is None

    review = tanaka.post(f"/api/advocacy/documents/{document['id']}/review").json()["review"]
    again = tanaka.post(f"/api/advocacy/documents/{document['id']}/review").json()["review"]

    assert review["overview"] == "ケアプランの案です。"
    assert review["concerns"][0]["where"] == "1ページ・本人の意向"
    assert again == review
    assert len(claude.bodies("資料のレビュー")) == 1
    assert claude.bodies("資料のレビュー")[0]["model"] == "claude-opus-5-5"
    assert "デイサービスで友人を作りたい" in str(claude.bodies("資料のレビュー"))
    assert tanaka.get(f"/api/advocacy/documents/{document['id']}").json()["document"]["review"]["overview"] == "ケアプランの案です。"


def test_レビューには_専門職が入力したアセスメントなども読ませる(login, claude):
    # tyumajinさんの決定：ツインがレビューを書くときに、アセスメントの中身も一緒に読ませる
    tanaka = login("田中さん")
    saved = tanaka.post(
        "/api/advocacy/care-forms/records",
        json={"form_id": "assessment", "record_date": "2036-04-02", "values": {"浴槽への出入り": "何かにつかまればできる"}, "details": {"浴槽への出入り": "浴槽をまたぐときに手すりを使う。"}, "client_key": "a1"},
    )
    assert saved.status_code == 201
    document = _upload(tanaka).json()["document"]

    tanaka.post(f"/api/advocacy/documents/{document['id']}/review")

    sent = str(claude.bodies("資料のレビュー"))
    assert "専門職が入力したアセスメントなど" in sent
    assert "- 浴槽への出入り：何かにつかまればできる（詳細：浴槽をまたぐときに手すりを使う。）" in sent


def test_名前の確かめ待ちの資料はレビューしない(login, claude):
    claude.answers["資料を読む"] = _plan_read(person_name="山田　花子")
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]

    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/review").status_code == 400
    assert claude.bodies("資料のレビュー") == []


def test_ツインが作った資料は_レビューだけで記録の候補にしない(login, claude):
    claude.answers["資料を読む"] = _plan_read(kind="そのほか", author="ツイン（AI）", care_plan=None, made_by_ai=True)
    tanaka = login("田中さん")

    document = _upload(tanaka).json()["document"]

    assert document["made_by_ai"] is True
    assert document["items"] == []
    assert document["status"] == "確かめ済み"
    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/review").status_code == 200


def test_書いた人がツインなら_印がなくてもAIが作った資料とみなす():
    assert professional_records.made_by_ai_of("ツイン（AI）", False) is True
    assert professional_records.made_by_ai_of("田中（居宅介護支援）", False) is False


def test_記録の候補に_資料のどの場面から読んだかと原文を添える(login, claude):
    read = _plan_read()
    read["items"][0] |= {"section": "第2表", "quote": "日中は一人で過ごしている。"}
    claude.answers["資料を読む"] = read

    item = _upload(login("田中さん")).json()["document"]["items"][0]

    assert item["section"] == "第2表"
    assert item["quote"] == "日中は一人で過ごしている。"


def test_消去すると_レビューと項目の場所も消える(conn, login, claude):
    read = _plan_read()
    read["items"][0] |= {"section": "第2表", "quote": "日中は一人。"}
    claude.answers["資料を読む"] = read
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    tanaka.post(f"/api/advocacy/documents/{document['id']}/review")

    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/erase").status_code == 200

    assert conn.execute("SELECT COUNT(*) FROM document_reviews").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM professional_item_places").fetchone()[0] == 0
