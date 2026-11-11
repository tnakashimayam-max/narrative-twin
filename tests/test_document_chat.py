"""資料についてのチャットのテスト。

役割：専門職が渡した資料について問うと、ツインが資料の中身とレビューを材料に代弁で答え、
     問いと答えが資料ごと、聞いた人ごとに残ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：3（資料は書いた人の文として分けて渡す）、16（代弁で答える）、17（健康の問いでも止めない）。

通信だけを差し替える。本物の Claude API は呼ばない。資料は架空の小さな PDF を使う。
"""

from __future__ import annotations

import pytest

from app import ai
from app.schemas import ShareScope
from tests.fake_claude import RoleClaude
from tests.test_professional_records import _plan_read, _upload


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["資料を読む"] = _plan_read()
    fake.answers["資料のレビュー"] = {"overview": "通所の計画は、家で暮らしたい思いとずれます。", "concerns": [{"text": "入浴を通所で行う案", "where": "1ページ"}]}
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def material(add_statement):
    return add_statement("家で米を研ぐのが、わしの役目たい。", scope=ShareScope.DECISION_SUPPORT)


def _ask(client, document_id, question="この計画を、誠一さんはどう受け止めますか", key="c1"):
    return client.post(f"/api/advocacy/documents/{document_id}/chat", json={"question": question, "client_key": key})


def test_資料についての問いには資料の中身とレビューを添えて代弁を作る(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    tanaka.post(f"/api/advocacy/documents/{document['id']}/review")

    turn = _ask(tanaka, document["id"]).json()["turn"]

    assert turn["question"] == "この計画を、誠一さんはどう受け止めますか"
    assert turn["voice"]["claim"]
    assert "ツイン（AI）" in turn["voice"]["label"]
    sent = str(claude.bodies("ツインのアドボカシー"))
    assert "資料を書いた人の文で、本人の言葉ではありません" in sent
    assert "週2回のデイサービスで、人との交流を増やす計画です。" in sent
    assert "通所の計画は、家で暮らしたい思いとずれます。" in sent


def test_資料のチャットは聞いた人ごとに残り_送り直しではAIを呼ばない(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _ask(tanaka, document["id"])
    called = len(claude.bodies("ツインのアドボカシー"))

    again = _ask(tanaka, document["id"]).json()["turn"]
    items = tanaka.get(f"/api/advocacy/documents/{document['id']}/chat").json()["items"]

    assert len(claude.bodies("ツインのアドボカシー")) == called
    assert [item["id"] for item in items] == [again["id"]]


def test_資料を扱えない人は資料について聞けない(login, claude, material):
    document = _upload(login("田中さん")).json()["document"]

    for name in ("美穂さん", "誠一さん"):
        assert _ask(login(name), document["id"]).status_code == 404


def test_名前の確かめ待ちの資料には聞けない(login, claude, material):
    claude.answers["資料を読む"] = _plan_read(person_name="山田　花子")
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]

    assert _ask(tanaka, document["id"]).status_code == 400
    assert claude.bodies("ツインのアドボカシー") == []


def test_健康の問いでも止めず_医療の決まりを添えて答える(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]

    turn = _ask(tanaka, document["id"], question="この資料の病名から、認知症かどうか判断してください").json()["turn"]

    assert turn["voice"]["claim"]
    assert "病気の診断（急ぐかどうかの判定を含む）" in str(claude.bodies("ツインのアドボカシー"))


def test_同じ資料の直前のやり取りを次の答えに渡す(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    first = _ask(tanaka, document["id"]).json()["turn"]

    _ask(tanaka, document["id"], question="さっきの答えを、もう少し詳しく", key="c2")

    sent = str(claude.bodies("ツインのアドボカシー")[-1])
    assert "前に聞かれたことと、ツイン（AI）の答え" in sent
    assert "この計画を、誠一さんはどう受け止めますか" in sent
    assert first["voice"]["claim"][:20] in sent


def test_ほかの資料のやり取りは渡さない(login, claude, material):
    tanaka = login("田中さん")
    one = _upload(tanaka, key="d1").json()["document"]
    other = _upload(tanaka, key="d2").json()["document"]
    _ask(tanaka, one["id"], question="一つ目の資料の問い", key="c1")

    _ask(tanaka, other["id"], question="二つ目の資料の問い", key="c2")

    assert "一つ目の資料の問い" not in str(claude.bodies("ツインのアドボカシー")[-1])


def test_資料の一覧に_この人がその資料について話した数を出す(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _ask(tanaka, document["id"])

    items = tanaka.get("/api/advocacy/documents").json()["items"]

    assert items[0]["chat_count"] == 1


def test_消去の前の確認に_資料についてのチャットの数も出す(login, claude, material):
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    _ask(tanaka, document["id"])

    preview = tanaka.get(f"/api/advocacy/documents/{document['id']}/erase-preview").json()["preview"]

    assert preview["counts"]["資料についてのチャット"] == 1


def test_チャットした資料も消去でき_チャットも一緒に消える(conn, login, claude, material):
    """資料についてのチャットは資料を外部キーで指すので、消去で先に消す。前は外部キーの違反で 500 になっていた。"""
    tanaka = login("田中さん")
    document = _upload(tanaka).json()["document"]
    assert _ask(tanaka, document["id"]).status_code == 200
    logs_before = conn.execute("SELECT COUNT(*) FROM inference_logs").fetchone()[0]

    assert tanaka.post(f"/api/advocacy/documents/{document['id']}/erase").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM document_chats").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM professional_documents").fetchone()[0] == 0
    # チャットの推論の記録も消える（ほかの記録より少なくなる）
    assert conn.execute("SELECT COUNT(*) FROM inference_logs").fetchone()[0] < logs_before
