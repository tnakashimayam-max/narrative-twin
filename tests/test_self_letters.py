"""本人が送る手紙のテスト。

役割：下書きがその期間のツインとの会話から作られ、ほかの人の話や別の部屋の話が入らないこと、
     直す、校正、戻すで版が足され、前の版に戻せること、送ると直せず相手が読めること、本人のほかは作れないことを確かめる。
     手紙は標準語で書き、校正は文を消さずに整えるだけであること、下書きを捨てられること、
     挿絵がいつも入り、ツインが手紙の文に合わせて描けること（2026-11-07 の直し）も確かめる。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：1（送るかは本人が決める）、5（版を残す）、12（「ツイン（AI）」と出す）、15（人だけの部屋の言葉を引かない）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import ai, db, professional_prompts, self_letter_prompts, self_letters
from app.schemas import MessageKind, Place, RoomKind
from tests.fake_claude import RoleClaude

TALK = "庭の紫陽花が、今朝ひとつ色づいた。和子が植えたやつたい。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def talk(add_utterance):
    """2026年11月2日の、本人とツインの会話。"""
    add_utterance(TALK, place=Place.DIARY_AND_TALK)
    add_utterance("紫陽花の色、楽しみですね。", is_twin=True, place=Place.DIARY_AND_TALK)


def _draft(client, key="k1", **changes):
    body = {"period": "期間", "start": "2026-11-01", "end": "2026-11-03", "recipient_id": None, "outline": "", "client_key": key, **changes}
    return client.post("/api/self-letters", json=body)


def test_期間は今週ひと月選ぶの3つで_選ぶときは確かめる():
    today = date(2036, 5, 6)

    def period(**values):
        return self_letters.period_of(self_letters.DraftInput(recipient_id=1, client_key="k", **values), today)

    assert period(period="今週") == (date(2036, 4, 30), today)
    assert period(period="ひと月") == (date(2036, 4, 7), today)
    assert period(period="期間", start=date(2036, 4, 1), end=date(2036, 4, 20)) == (date(2036, 4, 1), date(2036, 4, 20))
    for wrong in (
        {"period": "期間"},
        {"period": "期間", "start": date(2036, 4, 20), "end": date(2036, 4, 1)},
        {"period": "期間", "start": date(2036, 5, 1), "end": date(2036, 5, 7)},
        {"period": "期間", "start": date(2035, 1, 1), "end": date(2036, 5, 1)},
    ):
        with pytest.raises(Exception):
            period(**wrong)


def test_下書きはその期間のツインとの会話から作り_ほかの人の話は渡さない(login, users, add_utterance, talk, claude):
    add_utterance("美穂がツインに話した内緒の話。", speaker="美穂さん", place=Place.DIARY_AND_TALK)
    add_utterance("チャットで節子さんに話したこと。", place=Place.CHAT)
    seiichi = login("誠一さん")

    response = _draft(seiichi, recipient_id=users["美穂さん"].id, outline="紫陽花のことを書きたい")

    assert response.status_code == 201
    letter = response.json()["letter"]
    assert letter["status"] == "下書き"
    assert letter["content"]["salutation"] == "美穂へ"
    assert letter["recipient"]["relation"] == "長女"
    assert [item["kind"] for item in letter["versions"]] == ["下書き"]
    assert "ツイン（AI）が下書きを手伝っています" in letter["label"]
    sent = str(claude.bodies("本人の手紙"))
    assert TALK in sent
    assert "紫陽花のことを書きたい" in sent
    assert "美穂さん（長女）" in sent
    assert "内緒の話" not in sent
    assert "チャットで節子さんに話したこと" not in sent
    assert claude.bodies("本人の手紙")[0]["model"] == "claude-opus-5-5"


def test_送る相手との1対1のやり取りだけを渡す(conn, login, users, talk, claude):
    def room(kind, members, sender, text):
        room_id = db.insert_row(conn, "rooms", {"kind": kind, "name": None, "created_at": "2026-10-01T00:00:00"})
        for name in members:
            conn.execute("INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?, ?, ?)", (room_id, users[name].id, "2026-10-01T00:00:00"))
        db.insert_row(
            conn,
            "room_messages",
            {"room_id": room_id, "sender_user_id": users[sender].id, "is_twin": 0, "kind": MessageKind.SAY.value, "text": text, "sent_at": "2026-11-02T12:00:00"},
        )
        conn.commit()

    room(RoomKind.PAIR.value, ["誠一さん", "美穂さん"], "美穂さん", "健太が将棋で三番勝ったよ。")
    room(RoomKind.GROUP.value, ["誠一さん", "美穂さん", "節子さん"], "節子さん", "回覧板をまわしました。")
    room(RoomKind.PAIR.value, ["誠一さん", "正雄さん"], "正雄さん", "雨やけん、サッシ閉めとくぞ。")

    _draft(login("誠一さん"), recipient_id=users["美穂さん"].id)

    sent = str(claude.bodies("本人の手紙"))
    assert "健太が将棋で三番勝ったよ。" in sent
    assert "回覧板" not in sent
    assert "サッシ" not in sent


def test_期間に会話がなければ断り_同じclient_keyならAIを呼ばない(login, users, talk, claude):
    seiichi = login("誠一さん")
    empty = _draft(seiichi, key="none", start="2026-10-01", end="2026-10-10", recipient_id=users["美穂さん"].id)
    assert empty.status_code == 400
    assert empty.json()["detail"] == self_letters.NO_TALK_MESSAGE

    first = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    again = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]

    assert again["id"] == first["id"]
    assert len(claude.bodies("本人の手紙")) == 1


def test_直す_校正_戻すで版が増え_前の版に戻せる(login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    path = f"/api/self-letters/{letter['id']}"
    edited = {"salutation": "美穂へ", "body": "紫陽花が色づいた。\n\n飯は今日も炊いた。", "closing": "またな。", "signature": "父"}

    after_edit = seiichi.post(f"{path}/edit", json=edited).json()["letter"]
    same = seiichi.post(f"{path}/edit", json=edited).json()["letter"]
    proofed = seiichi.post(f"{path}/proofread").json()["letter"]
    reverted = seiichi.post(f"{path}/revert", json={"to_version": 2}).json()["letter"]

    assert after_edit["version"] == 2 and after_edit["kind"] == "直し"
    # 同じ文面なら版を足さない
    assert same["version"] == 2
    assert proofed["version"] == 3 and proofed["kind"] == "校正"
    assert proofed["notes"] == ["読点を足しました。"]
    assert proofed["proofread_changed"] is True
    assert "紫陽花が色づいた。" in str(claude.bodies("手紙の校正"))
    assert claude.bodies("手紙の校正")[0]["model"] == "claude-sonnet-5-5"
    assert reverted["version"] == 4 and reverted["kind"] == "戻す"
    assert reverted["content"] == edited
    assert [item["kind"] for item in reverted["versions"]] == ["下書き", "直し", "校正", "戻す"]
    assert seiichi.post(f"{path}/revert", json={"to_version": 9}).status_code == 400


def test_送ると直せず_相手は読めて_ほかの人は読めない(login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    path = f"/api/self-letters/{letter['id']}"
    miho = login("美穂さん")
    assert miho.get(path).status_code == 404

    sent = seiichi.post(f"{path}/send").json()["letter"]

    assert sent["status"] == "送った"
    assert "誠一さんが確かめて送りました" in sent["label"]
    assert seiichi.post(f"{path}/edit", json={"salutation": "美穂へ", "body": "直す"}).status_code == 409
    assert seiichi.post(f"{path}/send").status_code == 409
    read = miho.get(path).json()["letter"]
    assert read["content"]["salutation"] == "美穂へ"
    assert "versions" not in read and "outline" not in read
    inbox = miho.get("/api/self-letters/inbox").json()["items"]
    assert [item["id"] for item in inbox] == [letter["id"]]
    # 知らせの行の頭に、書いた人の顔を出すための情報
    assert inbox[0]["author_id"] == users["誠一さん"].id
    assert login("節子さん").get(path).status_code == 404
    assert [item["status"] for item in seiichi.get("/api/self-letters").json()["items"]] == ["送った"]


def test_本人のほかは作れず_送る相手は本人のほかの人(login, users, talk, claude):
    assert _draft(login("美穂さん"), recipient_id=users["正雄さん"].id).status_code == 404
    seiichi = login("誠一さん")
    names = [item["name"] for item in seiichi.get("/api/self-letters/recipients").json()["items"]]
    assert "誠一さん" not in names and "美穂さん" in names and "和子さん" in names
    assert _draft(seiichi, recipient_id=users["誠一さん"].id).status_code == 400


def test_版と送った印は書き換えも消しもできない(conn, login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    seiichi.post(f"/api/self-letters/{letter['id']}/send")

    with pytest.raises(Exception, match="手紙の版は追記だけです"):
        conn.execute("UPDATE self_letter_versions SET content = '{}'")
    with pytest.raises(Exception, match="手紙を送った印は追記だけです"):
        conn.execute("DELETE FROM self_letter_sends")


def test_手紙は標準語で書き_校正は文を消さずに整える():
    # 2026-11-07：手紙は方言にしない。専門職が頼む手紙も同じ。校正は作り直さない
    draft = self_letter_prompts.draft_system("誠一さん", "美穂さん", "長女", False)
    assert "標準語で書きます" in draft
    assert "方言で書きません" in draft
    assert "方言を生かします" not in draft
    proof = self_letter_prompts.proofread_system("誠一さん")
    assert "作り直したりしません" in proof
    assert "1つも消しません" in proof
    assert "標準語に直します" in proof
    assert "標準語や丁寧すぎる言葉に直しません" not in proof
    letter = professional_prompts.letter_system("誠一さん", "田中さん")
    assert "手紙は標準語で書きます" in letter
    assert "方言、短さ、照れ方をまねます" not in letter


def test_直す欄の文を渡して校正すると_先に直しとして残してから校正する(login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    typed = {"salutation": "美穂へ", "body": "庭の紫陽花が色づいてきた。\n\n今日も飯は炊いた。\n\n健太の絵を壁に貼った。", "closing": "体に気をつけて。", "signature": "父より"}
    claude.answers["手紙の校正"] = {**typed, "body": typed["body"].replace("今日も飯", "今日も、飯"), "notes": ["読点を足しました。"]}

    proofed = seiichi.post(f"/api/self-letters/{letter['id']}/proofread", json=typed).json()["letter"]

    assert [item["kind"] for item in proofed["versions"]] == ["下書き", "直し", "校正"]
    assert "健太の絵を壁に貼った。" in str(claude.bodies("手紙の校正"))
    assert "健太の絵を壁に貼った。" in proofed["content"]["body"]
    # 校正の前に戻すと、本人が書いた文に戻る
    back = seiichi.post(f"/api/self-letters/{letter['id']}/revert", json={"to_version": 2}).json()["letter"]
    assert back["content"] == typed


def test_校正で段落が減ったら使わず_文面はそのまま(login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    claude.answers["手紙の校正"] = {"salutation": "美穂へ", "body": "庭の紫陽花が色づいてきた。", "closing": "体に気をつけて。", "signature": "父より", "notes": ["まとめました。"]}

    proofed = seiichi.post(f"/api/self-letters/{letter['id']}/proofread").json()["letter"]

    assert proofed["proofread_dropped"] is True
    assert proofed["proofread_changed"] is False
    assert proofed["version"] == 1
    assert proofed["content"] == letter["content"]


def test_送っていない下書きは捨てられ_一覧と画面から消える(conn, login, users, talk, claude):
    seiichi = login("誠一さん")
    first = _draft(seiichi, key="a", recipient_id=users["美穂さん"].id).json()["letter"]
    second = _draft(seiichi, key="b", recipient_id=users["美穂さん"].id).json()["letter"]
    assert login("美穂さん").delete(f"/api/self-letters/{first['id']}").status_code == 404

    assert seiichi.delete(f"/api/self-letters/{first['id']}").json() == {"discarded": first["id"]}

    assert [item["id"] for item in seiichi.get("/api/self-letters").json()["items"]] == [second["id"]]
    assert seiichi.get(f"/api/self-letters/{first['id']}").status_code == 404
    assert seiichi.post(f"/api/self-letters/{first['id']}/send").status_code == 404
    # 送った手紙は捨てられない
    seiichi.post(f"/api/self-letters/{second['id']}/send")
    assert seiichi.delete(f"/api/self-letters/{second['id']}").status_code == 409
    # 行も版も残り、捨てた印は消せない
    assert conn.execute("SELECT COUNT(*) FROM self_letter_versions WHERE letter_id = ?", (first["id"],)).fetchone()[0] == 1
    with pytest.raises(Exception, match="手紙を捨てた印は追記だけです"):
        conn.execute("DELETE FROM self_letter_discards")


def test_挿絵は描く前も_前もって作った水彩の絵を出し_挿絵なしにしない(login, users, talk, claude):
    # 下書きの AI が絵を選ばなくても（illustration_number が空）、通年の絵を入れる
    letter = _draft(login("誠一さん"), recipient_id=users["美穂さん"].id).json()["letter"]
    assert letter["illustration"]["src"] == "letter_art/01.webp"
    assert letter["illustration"]["drawn"] is False


def test_挿絵はツインが手紙の文に合わせてSVGで描き_見本の絵も渡す(conn, login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]

    drawn = seiichi.post(f"/api/self-letters/{letter['id']}/art").json()["letter"]

    assert drawn["illustration"]["drawn"] is True
    assert drawn["illustration"]["title"] == "庭の紫陽花"
    assert drawn["illustration"]["src"].startswith("data:image/svg+xml;base64,")
    sent = claude.bodies("手紙の挿絵")[0]
    assert sent["model"] == "claude-sonnet-5-5"
    blocks = sent["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/webp"
    assert "庭の紫陽花が色づいてきた。" in blocks[-1]["text"]
    # 一覧には絵の中身を載せない（重くしないため）
    assert "illustration" not in seiichi.get("/api/self-letters").json()["items"][0]
    with pytest.raises(Exception, match="手紙の挿絵は追記だけです"):
        conn.execute("UPDATE self_letter_arts SET svg = ''")


def test_使えない部品が入った挿絵は使わず_前の絵のまま(conn, login, users, talk, claude):
    seiichi = login("誠一さん")
    letter = _draft(seiichi, recipient_id=users["美穂さん"].id).json()["letter"]
    claude.answers["手紙の挿絵"] = {"title": "危ない絵", "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script></svg>'}

    response = seiichi.post(f"/api/self-letters/{letter['id']}/art")

    assert response.status_code == 503
    assert response.json()["detail"] == self_letters.ART_FAILED_MESSAGE
    assert conn.execute("SELECT COUNT(*) FROM self_letter_arts").fetchone()[0] == 0
    assert seiichi.get(f"/api/self-letters/{letter['id']}").json()["letter"]["illustration"]["drawn"] is False


def test_呼び方は敬称を重ねない():
    # 「健太くんさん」「井ノ口先生さん」にしない
    assert self_letters.called("健太くん") == "健太くん"
    assert self_letters.called("井ノ口先生") == "井ノ口先生"
    assert self_letters.called("美穂さん") == "美穂さん"
    assert self_letters.called("誠一") == "誠一さん"
