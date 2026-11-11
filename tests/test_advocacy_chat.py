"""チャットからアドボカシーを呼ぶテスト。

役割：チャットの言葉が正しい処理へ渡り、ボタンと同じものが返ることを確かめる。当てはまらない言葉はふだんの問いになる。
4つの極：4. 10年後に代弁する。
守る原則：1（呼び分けるだけで判断しない）、9（本人の言葉は記録の本文のまま）、10（見える範囲）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import advocacy_chat as chat, advocacy_voice, ai, model_store, professions
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, Role, ShareScope, Theme
from tests.fake_claude import RoleClaude

RICE = "冷や飯なんざ食えるか。飯は炊きたてが一番たい。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    fake.answers["根拠チェック"] = {
        "claim": "ツインは配食弁当に反対します。",
        "points": [{"target": "配食弁当", "objection": "暮らしの柱です。", "evidence_numbers": [1]}],
        "requests": [],
        "ask_back": None,
    }
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


def _sheet_draft():
    return {
        "claim": "誠一さんにとって家は暮らしそのものです。",
        "topics": [],
        "change": None,
        "self_intent": "この家で暮らしたい。",
        "long_term_goal": "台所の主役でいる。",
        "plan_evidence_numbers": [1],
        "requests": [],
        "ask_back": None,
    }


def _say(client, text, key="c1"):
    return client.post("/api/advocacy/chat", json={"question": text, "client_key": key})


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ご飯のことを聞かせてください", chat.ASK),
        ("ツインはどう思いますか？", chat.VOICE),
        ("ツインの考えを聞かせて", chat.VOICE),
        ("夕食を配食弁当にしたいのですが、誠一さんに合いますか。ツインはどう思いますか", chat.ASK_AND_VOICE),
        ("1枚の紙を作ってください", chat.SHEET),
        ("一枚の紙を作り直して", chat.SHEET),
        ("週2回のデイサービスと配食弁当という案をどう思いますか", chat.PLAN),
        ("このケアプラン案を見てください", chat.PLAN),
        ("第2版を作ってください", chat.REVISE),
        ("第2版にします", chat.CHOOSE),
        ("第1版を使います", chat.CHOOSE),
        ("どちらも使わないことにします", chat.CHOOSE),
    ],
)
def test_チャットの言葉を処理ごとに見分ける(text, kind):
    assert chat.chat_intent(text) == kind


def test_当てはまらない言葉には代弁で答え_言葉の一覧を応答に入れない(login, words, claude):
    reply = _say(login("田中さん"), "ご飯のことを聞かせてください").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["claim"]
    for key in ("self_words", "testimony", "guesses", "estimate", "elements"):
        assert key not in reply["turn"]
    assert len(claude.bodies("ツインのアドボカシー")) == 1


def test_問いに合う本人の言葉があれば_記録にないことの1文は付けない(login, words, claude):
    reply = _say(login("田中さん"), "ご飯のことを聞かせてください").json()

    assert reply["voice"]["off_record"] is None


def test_問いに合う本人の言葉がなければ_代弁の前に記録にないことの1文を付け_読み直しても残る(login, words, claude):
    # 記録にない問い（犬や猫）に、ツインが本人として言い切っていた。言葉と推測の境目を、決まった1文で見せる
    claude.answers["ツインの答え"] = {"direct_numbers": [], "related_numbers": [], "request_kind": "通常"}
    client = login("田中さん")

    reply = _say(client, "犬や猫を飼いたいと話していましたか").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["off_record"] == advocacy_voice.OFF_RECORD
    assert reply["voice"]["claim"]
    assert client.get("/api/advocacy/turns").json()["items"][0]["voice"]["off_record"] == advocacy_voice.OFF_RECORD


def test_託された家族にも代弁で答える(login, words, claude):
    reply = _say(login("美穂さん"), "ご飯のことを聞かせてください").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["claim"]
    assert "self_words" not in reply["turn"]


def test_託された主治医にも代弁で答える(conn, login, words, claude):
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name, relation, residence) VALUES (?, ?, ?, ?, ?, ?)",
            (77, "井ノ口先生", Role.PROFESSIONAL.value, "井ノ口先生", professions.DOCTOR, "近く"),
        )
        conn.execute("INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (77, ?, '2036-01-01')", (ShareScope.DECISION_SUPPORT.value,))
    client = login("田中さん")
    client.headers["X-NT-User"] = "77"

    reply = _say(client, "ご飯のことを聞かせてください").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["claim"]


def test_読み直しても言葉の一覧は返らず_代弁が添えられる(login, words, claude):
    client = login("田中さん")
    made = _say(client, "ご飯のことを聞かせてください").json()

    items = client.get("/api/advocacy/turns").json()["items"]

    assert items[0]["voice"]["id"] == made["voice"]["id"]
    assert "self_words" not in items[0]


def test_健康や病気の問いでも代弁を作り_断りの1文は出さない(login, words, claude):
    # 原則17：健康の語があるだけで止めない。診断と服薬の指示をしない決まりを添えて代弁を作る
    reply = _say(login("田中さん"), "認知症かどうか教えてください").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["turn"]["notice"] is None
    assert reply["turn"]["can_voice"] is True
    assert reply["voice"]["claim"]
    assert "病気の診断（急ぐかどうかの判定を含む）" in str(claude.bodies("ツインのアドボカシー"))


def test_AIだけが健康の問いと読んだ問いも_医療の決まりを添えて代弁を作る(login, words, claude):
    # 体の話が出ても、連絡や備えを聞く問い。AI が健康の判断と読み違えても、空の答えにしない（2026-11-06）
    claude.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "健康の判断"}

    reply = _say(login("田中さん"), "何かあったときの連絡方法は？たとえば、急に心臓が苦しくなったときとか。").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["claim"]
    assert reply["turn"]["can_voice"] is True
    sent = str(claude.bodies("ツインのアドボカシー"))
    assert "病気の診断（急ぐかどうかの判定を含む）" in sent


def test_ツインの答えの決まりは_連絡や備えの問いを健康の判断にしない():
    from app import prompts

    system = prompts.twin_answer_system("誠一さん")

    assert "連絡の方法" in system
    assert "止めずに答えます" in system


def test_短いどう思いますかは_前の問いに続けてツインの考えを返す(login, words, claude):
    client = login("田中さん")
    first = _say(client, "ご飯のことを聞かせてください").json()["turn"]

    reply = _say(client, "ツインはどう思いますか？", key="c2").json()

    assert reply["kind"] == chat.VOICE
    assert reply["voice"]["turn_id"] == first["id"]


def test_ボタンで作った考えがあれば_チャットからはAIを呼ばずに同じものが返る(login, words, claude):
    client = login("田中さん")
    turn = _say(client, "ご飯のことを聞かせてください").json()["turn"]
    made = client.post(f"/api/advocacy/turns/{turn['id']}/advocate", json={}).json()["voice"]
    calls = len(claude.bodies("ツインのアドボカシー"))

    reply = _say(client, "ツインはどう思いますか？", key="c2").json()

    assert reply["voice"]["id"] == made["id"]
    assert len(claude.bodies("ツインのアドボカシー")) == calls


def test_問いと一緒なら_答えとツインの考えを両方返す(login, words, claude):
    reply = _say(login("田中さん"), "夕食を配食弁当にしたいのですが、誠一さんに合いますか。ツインはどう思いますか").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert "self_words" not in reply["turn"]
    assert reply["voice"]["turn_id"] == reply["turn"]["id"]


def test_前の問いがないどう思いますかは_言葉を問いにして考えも添える(login, words, claude):
    reply = _say(login("田中さん"), "ツインはどう思いますか？").json()

    assert reply["kind"] == chat.ASK_AND_VOICE
    assert reply["voice"]["claim"]


def test_チャットから1枚の紙を作り_作ってあればAIを呼ばない(login, words, claude):
    client = login("田中さん")
    claude.answers["ツインのアドボカシー"] = _sheet_draft()
    first = _say(client, "1枚の紙を作ってください").json()
    calls = len(claude.bodies("ツインのアドボカシー"))

    second = _say(client, "1枚の紙を見せてください", key="c2").json()

    assert first["kind"] == chat.SHEET
    assert first["sheet"]["claim"].startswith("誠一さんにとって家は")
    assert second["sheet"]["id"] == first["sheet"]["id"]
    assert len(claude.bodies("ツインのアドボカシー")) == calls


def test_作り直してと言うと紙を作り直す(login, words, claude):
    client = login("田中さん")
    claude.answers["ツインのアドボカシー"] = _sheet_draft()
    first = _say(client, "1枚の紙を作ってください").json()

    second = _say(client, "1枚の紙を作り直してください", key="c2").json()

    assert second["sheet"]["id"] != first["sheet"]["id"]


def test_チャットの案は言葉のまま第1版になり_続けてツインの異議が返る(login, words, claude):
    text = "週2回のデイサービスと配食弁当という案をどう思いますか"

    reply = _say(login("田中さん"), text).json()

    assert reply["kind"] == chat.PLAN
    assert reply["plan"]["first"]["summary"] == text
    assert reply["plan"]["objection"]["points"][0]["evidence"][0]["body"] == RICE
    assert reply["plan"]["second"] is None
    assert claude.bodies("原案") == []


def test_第2版と言うと_いちばん新しい案を書き直す(login, words, claude):
    client = login("田中さん")
    _say(client, "週2回のデイサービスという案をどう思いますか")
    claude.answers["原案"] = {
        "summary": "夕方に一緒に炊く案です。",
        "self_intent": "この家で暮らしたい。",
        "long_term_goal": "台所の主役でいる。",
        "items": [],
    }

    reply = _say(client, "第2版を作ってください", key="c2").json()

    assert reply["kind"] == chat.REVISE
    assert reply["plan"]["second"]["summary"] == "夕方に一緒に炊く案です。"


def test_第2版にしますと言うと_人の選択として記録しAIを呼ばない(login, words, claude):
    client = login("田中さん")
    _say(client, "週2回のデイサービスという案をどう思いますか")
    claude.answers["原案"] = {"summary": "案です。", "self_intent": "意向です。", "long_term_goal": "目標です。", "items": []}
    _say(client, "第2版を作ってください", key="c2")
    calls = len(claude.requests)

    reply = _say(client, "第2版にします", key="c3").json()

    assert reply["kind"] == chat.CHOOSE
    assert reply["plan"]["choice"] == {**reply["plan"]["choice"], "version": "第2版", "chooser": "田中さん"}
    assert len(claude.requests) == calls


def test_案がないのに版を選ぶと_分かる言葉で返す(login, words, claude):
    assert _say(login("田中さん"), "第2版にします").status_code == 400


def test_案がないのに第2版と言うと_分かる言葉で返す(login, words, claude):
    response = _say(login("田中さん"), "第2版を作ってください")

    assert response.status_code == 400
    assert "案をツインに見せて" in response.json()["detail"]


def test_託されていない人は使えない(login, words, claude):
    assert _say(login("節子さん"), "ご飯のことを聞かせてください").status_code == 404


def test_空の言葉は分かる言葉で返す(login, words, claude):
    assert _say(login("田中さん"), "   ").status_code == 400
