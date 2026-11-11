"""託す設定と、本人の確認の API のテスト。

役割：範囲を決めるのは本人だけで、確かめ、直し、休ませることが新しい行として残ることを確かめる。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：4（推測に答えられる）、5（元の記録を消さない）、6（話さない自由）、10（範囲は本人が決める）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db, handover, ledger
from app.schemas import ConfirmStatus, ConversationDraft, LifeStage, ShareScope, Source, Theme


def _bodies(client):
    return {item["body"] for item in client.get("/api/statements").json()["items"]}


def _save(client, themes, delegates):
    return client.put("/api/handover", json={"themes": themes, "delegate_ids": delegates})


# ---- 託す設定 ----


def test_託すテーマを決めると_そのテーマの今の言葉に範囲の行が足される(conn, login, users, add_statement):
    garden = add_statement("毎朝庭を見ます。", theme=Theme.LIKES)
    add_statement("兄のことは話したくない。", theme=Theme.HARDSHIP)

    response = _save(login("誠一さん"), ["好きなこと"], [users["美穂さん"].id])

    assert response.status_code == 200
    assert _bodies(login("田中さん")) == {"毎朝庭を見ます。"}
    assert ledger.get_statement(conn, garden.id).share_scope is ShareScope.SELF_ONLY
    assert ledger.head_of(conn, garden.id).share_scope is ShareScope.DECISION_SUPPORT


def test_未確認の言葉も託せて_状態は未確認のまま(conn, login, users, add_statement):
    garden = add_statement("毎朝庭を見ます。", theme=Theme.LIKES)

    _save(login("誠一さん"), ["好きなこと"], [users["美穂さん"].id])

    assert ledger.head_of(conn, garden.id).confirm_status is ConfirmStatus.UNCONFIRMED


def test_託すのをやめると本人だけに戻る(login, users, add_statement):
    add_statement("毎朝庭を見ます。", theme=Theme.LIKES)
    me = login("誠一さん")
    _save(me, ["好きなこと"], [users["美穂さん"].id])

    _save(me, [], [users["美穂さん"].id])

    assert _bodies(login("田中さん")) == set()


def test_あとから入った言葉にも本人の決めた規則が当たる(conn, users, add_statement):
    with conn:
        conn.execute("INSERT INTO handover_themes (theme, decided_on) VALUES (?, ?)", ("好きなこと", "2026-11-03"))
    statement = add_statement("庭に出ました。", theme=Theme.LIKES)

    moved = handover.apply_handover(conn, users["誠一さん"])

    assert moved == 1
    assert ledger.head_of(conn, statement.id).share_scope is ShareScope.DECISION_SUPPORT


def test_専門職だけの記録は_託すテーマに入っても家族には見えない(conn, login, users, add_statement):
    """アセスメントなど専門職の内側の資料から残した記録は、託すテーマに入っても意思決定支援用にしない。"""
    inner = add_statement("夜中に何度も起きている。", theme=Theme.LIKES, scope=ShareScope.PROFESSIONAL_ONLY)
    garden = add_statement("毎朝庭を見ます。", theme=Theme.LIKES)

    assert _save(login("誠一さん"), ["好きなこと"], [users["美穂さん"].id]).status_code == 200

    assert ledger.head_of(conn, inner.id).share_scope is ShareScope.PROFESSIONAL_ONLY
    assert ledger.head_of(conn, garden.id).share_scope is ShareScope.DECISION_SUPPORT
    assert "夜中に何度も起きている。" not in _bodies(login("美穂さん"))
    assert "毎朝庭を見ます。" in _bodies(login("美穂さん"))

    # 託すのをやめても「専門職だけ」は本人だけに変わらず、そのまま残る
    assert _save(login("誠一さん"), [], [users["美穂さん"].id]).status_code == 200
    assert ledger.head_of(conn, inner.id).share_scope is ShareScope.PROFESSIONAL_ONLY


def test_お休みの言葉には規則を当てない(conn, users, add_statement):
    me = users["誠一さん"].id
    with conn:
        conn.execute("INSERT INTO handover_themes (theme, decided_on) VALUES (?, ?)", ("好きなこと", "2026-11-03"))
    statement = add_statement("庭に出ました。", theme=Theme.LIKES)
    rested = ledger.rest_statement(conn, statement.id, actor_user_id=me)

    assert handover.apply_handover(conn, users["誠一さん"]) == 0
    assert ledger.head_of(conn, statement.id).id == rested.id


@pytest.mark.parametrize("name", ["美穂さん", "和子さん", "節子さん", "田中さん"])
def test_託す設定は本人だけが開けて変えられる(login, users, name):
    client = login(name)

    assert client.get("/api/handover").status_code == 404
    assert _save(client, ["家族"], [users["美穂さん"].id]).status_code == 404


def test_託せるのは家族だけ(login, users):
    assert _save(login("誠一さん"), [], [users["節子さん"].id]).status_code == 400


def test_託す人をはずすと意思決定支援用は見えなくなり_招く操作もできなくなる(login, users, add_statement):
    add_statement("毎朝庭を見ます。", theme=Theme.LIKES)
    me = login("誠一さん")
    _save(me, ["好きなこと"], [users["美穂さん"].id])
    assert "毎朝庭を見ます。" in _bodies(login("美穂さん"))

    _save(me, ["好きなこと"], [])

    assert _bodies(login("美穂さん")) == set()
    assert login("美穂さん").get("/api/me").json()["can_invite_professional"] is False


def test_託す設定の日付を返す(login, users):
    view = _save(login("誠一さん"), ["好きなこと"], [users["美穂さん"].id]).json()

    assert view["decided_on"] == date.today().isoformat()
    assert view["themes"] == ["好きなこと"]


# ---- 確認 ----


def test_今日うかがったことは未確認の本人の言葉と推測を出す(login, add_statement):
    add_statement("毎朝庭を見ます。")

    items = login("誠一さん").get("/api/review").json()["items"]

    assert [item["body"] for item in items] == ["毎朝庭を見ます。"]


def test_確かめの一覧は同じテーマが続かない(login, add_statement):
    for number in range(3):
        add_statement(f"家族の話{number}です。", theme=Theme.FAMILY)
    add_statement("庭の話です。", theme=Theme.HOME, effective_date=date(2026, 1, 1))

    items = login("誠一さん").get("/api/review").json()["items"]

    assert any(item["body"] == "庭の話です。" for item in items[:2])


def test_今日うかがったことは今日の話を先に_次に推測を出す(login, add_statement, make_draft, add_utterance, conn):
    old_words = add_statement("去年の話です。", effective_date=date(2020, 1, 1))
    guess_utterance = add_utterance("話の元です。")
    guess = ledger.append_statement(
        conn,
        make_draft(
            [guess_utterance.id],
            "遠慮しているかもしれません。",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
            effective_date=date(2020, 1, 2),
        ),
    )
    today_words = add_statement("今日の話です。", effective_date=date.today())

    ids = [item["id"] for item in login("誠一さん").get("/api/review").json()["items"]]

    assert ids.index(today_words.id) < ids.index(guess.id) < ids.index(old_words.id)


@pytest.mark.parametrize(
    ("speaker", "expected", "named"), [("誠一さん", True, None), ("美穂さん", False, "美穂さん")]
)
def test_確かめの推測のもとの発言は_話したのが本人のときだけ本人の言葉と分かり_ほかの人は名前が付く(
    login, make_draft, add_utterance, conn, speaker, expected, named
):
    origin = add_utterance("庭の話をしていました。", speaker=speaker)
    ledger.append_statement(
        conn,
        make_draft(
            [origin.id],
            "庭が大切なのかもしれません。",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
        ),
    )

    item = login("誠一さん").get("/api/review").json()["items"][0]

    assert item["origin"] == "庭の話をしていました。"
    assert item["origin_is_self"] is expected
    assert item["origin_speaker"] == named


@pytest.mark.parametrize("name", ["美穂さん", "田中さん"])
def test_今日うかがったことは本人だけが開ける(login, name):
    assert login(name).get("/api/review").status_code == 404


def test_そうですと答えると確かめた行が足され_一覧から消える(conn, login, add_statement):
    statement = add_statement("毎朝庭を見ます。")
    me = login("誠一さん")

    assert me.post(f"/api/statements/{statement.id}/confirm").status_code == 200

    assert ledger.head_of(conn, statement.id).confirm_status is ConfirmStatus.CONFIRMED
    assert me.get("/api/review").json()["items"] == []


def test_話したくないと答えると本人だけに戻り_田中さんに届かない(conn, login, users, add_statement):
    statement = add_statement("毎朝庭を見ます。", scope=ShareScope.DECISION_SUPPORT)
    me = login("誠一さん")
    assert "毎朝庭を見ます。" in _bodies(login("田中さん"))

    assert me.post(f"/api/statements/{statement.id}/rest").status_code == 200

    assert ledger.head_of(conn, statement.id).share_scope is ShareScope.SELF_ONLY
    assert _bodies(login("田中さん")) == set()


def test_ちがいますと答えて直すと訂正の行になり_元の推測は履歴に残る(conn, login, users, add_utterance, make_draft):
    utterance = add_utterance("庭を見ています。")
    guess = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            "庭が好きなのかもしれません。",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
        ),
    )
    me = login("誠一さん")

    response = me.post(f"/api/statements/{guess.id}/correct", json={"text": "妻と過ごした場所だから大切です。"})

    assert response.status_code == 200
    head = ledger.head_of(conn, guess.id)
    assert head.source is Source.SELF_CORRECTION
    assert head.body == "妻と過ごした場所だから大切です。"
    assert head.replaces_id == guess.id
    assert ledger.get_statement(conn, guess.id).body == "庭が好きなのかもしれません。"
    assert me.get(f"/api/statements/{guess.id}").json()["body"] == "庭が好きなのかもしれません。"


def test_訂正の会話は人生の段階を引き継ぎ_段階の期間を伸ばさない(conn, login, add_statement):
    statement = add_statement("庭の話です。")
    ledger.add_conversation(conn, ConversationDraft(conversation_date=date.today(), life_stage=LifeStage.SUPPORT))

    login("誠一さん").post(f"/api/statements/{statement.id}/correct", json={"text": "畑の話です。"})

    row = conn.execute("SELECT life_stage FROM conversations WHERE room_id IS NULL AND trigger = ?", ("確認",)).fetchone()
    assert row["life_stage"] == LifeStage.SUPPORT.value


def test_訂正の言葉は裏の分割の対象にならない(conn, login, add_statement):
    statement = add_statement("毎朝庭を見ます。")

    login("誠一さん").post(f"/api/statements/{statement.id}/correct", json={"text": "朝だけではありません。"})

    row = conn.execute(
        "SELECT c.room_id, c.trigger FROM conversations c JOIN utterances u ON u.conversation_id = c.id WHERE u.text = ?",
        ("朝だけではありません。",),
    ).fetchone()
    assert row["room_id"] is None
    assert row["trigger"] == "確認"


@pytest.mark.parametrize("text", ["", "   "])
def test_空の訂正は受け付けない(login, add_statement, text):
    statement = add_statement("毎朝庭を見ます。")

    assert login("誠一さん").post(f"/api/statements/{statement.id}/correct", json={"text": text}).status_code in (400, 422)


def test_2回押しても確かめの行は増えない(conn, login, add_statement):
    statement = add_statement("毎朝庭を見ます。")
    me = login("誠一さん")

    me.post(f"/api/statements/{statement.id}/confirm")
    second = me.post(f"/api/statements/{statement.id}/confirm")

    assert second.status_code == 409
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 2


@pytest.mark.parametrize("name", ["美穂さん", "田中さん", "節子さん"])
def test_本人以外は確かめも直しも休ませもできない(login, add_statement, name):
    statement = add_statement("毎朝庭を見ます。")
    client = login(name)

    assert client.post(f"/api/statements/{statement.id}/confirm").status_code == 404
    assert client.post(f"/api/statements/{statement.id}/rest").status_code == 404
    assert client.post(f"/api/statements/{statement.id}/correct", json={"text": "違います。"}).status_code == 404


def test_確認の日付は物語の日付を渡せる(conn, users, add_statement):
    from datetime import datetime

    statement = add_statement("毎朝庭を見ます。")

    confirmed = ledger.confirm_statement(
        conn, statement.id, actor_user_id=users["誠一さん"].id, at=datetime(2029, 4, 1, 9, 0)
    )

    assert confirmed.created_at.year == 2029
    assert confirmed.effective_date == statement.effective_date


def test_先頭を引く辞書は連鎖の先頭を返す(conn, users, add_statement):
    me = users["誠一さん"].id
    original = add_statement("毎朝庭を見ます。")
    confirmed = ledger.confirm_statement(conn, original.id, actor_user_id=me)

    heads = ledger.head_map(conn)

    assert heads[original.id].id == confirmed.id
    assert heads[confirmed.id].id == confirmed.id
    assert ledger.root_of(conn, confirmed.id) == original.id
    assert db.get_user(conn, me) is not None
