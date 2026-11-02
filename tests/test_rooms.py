"""人どうしのトークと、グループのツインのテスト。

役割：部屋の見える範囲、人だけの部屋で AI を呼ばないこと、ツインに渡す範囲と書く場面を確かめる。
4つの極：2. 分かち合う。
守る原則：10（部屋の発言は部屋の人だけに見せる）、12（本人の代わりに答えない）、
          15（ツインは招かれた部屋だけを読む）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import pytest

from app import ai, ledger
from app.prompts import CANNOT_ANSWER_FOR_SELF
from app.schemas import Source
from tests.fake_claude import RoleClaude


@pytest.fixture
def use_claude(monkeypatch):
    def _use(**answers):
        fake = RoleClaude(**answers)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


@pytest.fixture
def me(login):
    return login("誠一さん")


def _make_room(client, users, names, kind="グループ", name=None):
    response = client.post(
        "/api/rooms",
        json={"kind": kind, "member_ids": [users[item].id for item in names], "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _say(client, room, text, key=None):
    response = client.post(
        f"/api/rooms/{room['id']}/messages", json={"text": text, "client_key": key or text[:60]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _texts(client, room):
    return [item["text"] for item in client.get(f"/api/rooms/{room['id']}/messages").json()["items"]]


def _twin_in(client, room, present=True):
    return client.post(f"/api/rooms/{room['id']}/twin", json={"present": present})


# ---- 部屋を作れる人、見える人 ----


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は部屋を作れない(login, users, name):
    response = login(name).post(
        "/api/rooms", json={"kind": "1対1", "member_ids": [users["誠一さん"].id]}
    )

    assert response.status_code == 404


def test_部屋には必ず本人が入る(me, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")

    assert {member["name"] for member in room["members"]} == {"誠一さん", "美穂さん"}


def test_ツインとの部屋はここからは作れない(me, users):
    response = me.post("/api/rooms", json={"kind": "ツイン", "member_ids": [users["美穂さん"].id]})

    assert response.status_code == 400


def test_同じ相手との1対1は1つだけ(me, users):
    first = _make_room(me, users, ["美穂さん"], kind="1対1")
    second = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()

    assert first["id"] == second["id"]


def test_部屋の発言は部屋の人にだけ見える(me, login, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")
    _say(me, room, "明日、行きます。")

    assert _texts(login("美穂さん"), room) == ["明日、行きます。"]
    outsider = login("節子さん")
    assert outsider.get(f"/api/rooms/{room['id']}/messages").status_code == 404
    assert outsider.post(
        f"/api/rooms/{room['id']}/messages", json={"text": "入れて", "client_key": "x"}
    ).status_code == 404
    assert outsider.get("/api/rooms").json()["items"] == []


def test_新しい発言だけを取りに行ける(me, login, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")
    _say(me, room, "1つ目")
    other = login("美穂さん")
    last = other.get(f"/api/rooms/{room['id']}/messages").json()["last_seq"]
    _say(other, room, "2つ目")

    items = me.get(f"/api/rooms/{room['id']}/messages", params={"after": last}).json()["items"]

    assert [item["text"] for item in items] == ["2つ目"]


def test_同じ印で送っても部屋の発言は増えない(me, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")

    _say(me, room, "明日、行きます。", key="k")
    _say(me, room, "明日、行きます。", key="k")

    assert _texts(me, room) == ["明日、行きます。"]


def test_送り直しの印は送った本人にだけ返す(me, login, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")
    _say(me, room, "明日、行きます。", key="k")

    mine = me.get(f"/api/rooms/{room['id']}/messages").json()["items"][0]
    theirs = login("美穂さん").get(f"/api/rooms/{room['id']}/messages").json()["items"][0]

    assert mine["client_key"] == "k"
    assert theirs["client_key"] is None


def test_はずした人は部屋を開けない(me, login, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")
    other = login("美穂さん")
    assert me.post(f"/api/people/{users['美穂さん'].id}/remove").status_code == 204

    assert other.get(f"/api/rooms/{room['id']}/messages").status_code == 401


# ---- 人だけの部屋 ----


def test_人だけの部屋ではAIを呼ばず言明にもしない(conn, me, login, users, use_claude):
    fake = use_claude()
    room = _make_room(me, users, ["美穂さん"], kind="1対1")

    _say(me, room, "内緒の話です。")
    _say(login("美穂さん"), room, "わかりました。")

    assert fake.requests == []
    assert conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 0
    assert ledger.current_statements(conn) == []


def test_ツインがいないグループではAIを呼ばない(conn, me, login, users, use_claude):
    fake = use_claude()
    room = _make_room(me, users, ["美穂さん", "節子さん"])

    _say(login("美穂さん"), room, "@ツイン 聞こえますか")

    assert fake.requests == []
    assert conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 0


def test_1対1にはツインを入れられない(me, users):
    room = _make_room(me, users, ["美穂さん"], kind="1対1")

    assert _twin_in(me, room).status_code == 400


# ---- グループのツイン ----


@pytest.mark.parametrize("name", ["美穂さん", "節子さん"])
def test_本人以外はツインを出し入れできない(me, login, users, name):
    room = _make_room(me, users, ["美穂さん", "節子さん"])

    assert _twin_in(login(name), room).status_code == 404
    assert _twin_in(login(name), room, present=False).status_code == 404


def test_ツインが入るとはずれるを部屋に表示する(me, users):
    room = _make_room(me, users, ["美穂さん"])

    assert _twin_in(me, room).json()["room"]["twin_present"] is True
    assert _twin_in(me, room, present=False).json()["room"]["twin_present"] is False

    items = me.get(f"/api/rooms/{room['id']}/messages").json()["items"]
    assert [item["kind"] for item in items] == ["お知らせ", "お知らせ"]
    assert "入りました" in items[0]["text"]
    assert "はずれました" in items[1]["text"]


def test_ツインが入る前の発言はAIに渡らない(me, login, users, use_claude):
    fake = use_claude(グループのツイン={"kind": "問い", "text": "誠一さん、お庭はいかがですか？"})
    room = _make_room(me, users, ["美穂さん"])
    _say(me, room, "入る前の内緒の話です。")
    _twin_in(me, room)

    _say(login("美穂さん"), room, "庭の花がきれいでした。")

    assert fake.bodies("グループのツイン")
    assert "入る前の内緒の話です。" not in fake.sent_text()
    assert "庭の花がきれいでした。" in fake.sent_text()


def test_はずれていた間の発言と前に入っていた間の発言は渡らない(me, login, users, use_claude):
    fake = use_claude()
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)
    _say(login("美穂さん"), room, "前に入っていた間の話です。")
    _twin_in(me, room, present=False)
    _say(me, room, "はずれていた間の話です。")
    fake.requests.clear()
    _twin_in(me, room)

    _say(login("美穂さん"), room, "今の話です。")

    sent = fake.sent_text()
    assert "今の話です。" in sent
    assert "前に入っていた間の話です。" not in sent
    assert "はずれていた間の話です。" not in sent


def test_本人とツインの部屋の話と台帳と要約はグループのツインに渡らない(conn, me, login, users, use_claude):
    fake = use_claude(
        ツインの会話={"reply": "そうなのですね。"},
        会話の要約={"summary": "架空の秘密の要約です。"},
        言明への分割={
            "statements": [
                {
                    "source": "本人の言葉",
                    "subject": "誠一さん",
                    "speaker": "誠一さん",
                    "body": "実は宝くじが当たりました。",
                    "period": "今",
                    "theme": "好きなこと",
                    "petals": [],
                    "utterance_numbers": [1],
                    "interpretations": [],
                    "confirm_question": None,
                }
            ]
        },
    )
    twin_room = me.get("/api/rooms").json()["items"][0]
    _say(me, twin_room, "実は宝くじが当たりました。")
    me.post(f"/api/rooms/{twin_room['id']}/close")
    assert ledger.current_statements(conn)
    fake.requests.clear()
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "@ツイン お父さんに最近いいことはありましたか")

    bodies = fake.bodies("グループのツイン")
    assert bodies
    text = str(bodies)
    assert "宝くじ" not in text
    assert "架空の秘密の要約です。" not in text


def test_家族の発言をきっかけに本人への問いを1つ書く(conn, me, login, users, use_claude):
    use_claude(グループのツイン={"kind": "問い", "text": "誠一さん、紫陽花はいつごろからですか？"})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "紫陽花が咲いていました。")

    items = me.get(f"/api/rooms/{room['id']}/messages").json()["items"]
    questions = [item for item in items if item["question_id"]]
    assert [item["text"] for item in questions] == ["誠一さん、紫陽花はいつごろからですか？"]
    assert questions[0]["is_twin"] is True


def test_本人が答えるまで次の問いを書かない(me, login, users, use_claude):
    fake = use_claude(
        グループのツイン=[
            {"kind": "問い", "text": "誠一さん、紫陽花はいつごろからですか？"},
            {"kind": "問い", "text": "誠一さん、お庭は広いですか？"},
        ]
    )
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)
    miho = login("美穂さん")

    _say(miho, room, "紫陽花が咲いていました。")
    _say(miho, room, "とてもきれいでした。")

    assert len(fake.bodies("グループのツイン")) == 1

    _say(me, room, "二十年前からです。")
    _say(miho, room, "そんなに前からなのですね。")

    items = me.get(f"/api/rooms/{room['id']}/messages").json()["items"]
    assert [item["text"] for item in items if item["question_id"]] == [
        "誠一さん、紫陽花はいつごろからですか？",
        "誠一さん、お庭は広いですか？",
    ]


def test_本人の発言では問いを書かない(me, users, use_claude):
    fake = use_claude(グループのツイン={"kind": "問い", "text": "誠一さん、どうですか？"})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(me, room, "こんにちは。")

    assert fake.bodies("グループのツイン") == []


@pytest.mark.parametrize(
    "text",
    ["@ツイン お父さんの本音を教えて", "＠ツイン 父の気持ちはどうですか", "@ツイン 父はどう思っているの"],
)
def test_本人の考えや気持ちを聞かれても代わりに答えない(me, login, users, use_claude, text):
    fake = use_claude()
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, text)

    assert fake.bodies("グループのツイン") == []
    assert _texts(me, room)[-1] == CANNOT_ANSWER_FOR_SELF


def test_AIが聞けていないと答えたら決まった文を書く(me, login, users, use_claude):
    use_claude(グループのツイン={"kind": "聞けていない", "text": ""})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "@ツイン 去年の旅行はどこでしたか")

    assert _texts(me, room)[-1] == CANNOT_ANSWER_FOR_SELF


def test_呼ばれたら部屋に見える範囲で短く答える(me, login, users, use_claude):
    use_claude(グループのツイン={"kind": "答え", "text": "美穂さんが、紫陽花が咲いたと話していました。"})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "@ツイン さっき何の話をしていましたか")

    assert _texts(me, room)[-1] == "美穂さんが、紫陽花が咲いたと話していました。"


def test_呼ばれていないのに答えは書かない(me, login, users, use_claude):
    use_claude(グループのツイン={"kind": "答え", "text": "勝手に答えます。"})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "今日は暑いですね。")

    assert "勝手に答えます。" not in _texts(me, room)


def test_待つ間にツインがはずされたら書かない(me, login, users, use_claude, conn):
    from app import jobs, room_store

    use_claude(グループのツイン={"kind": "問い", "text": "誠一さん、どうですか？"})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)
    presence = room_store.active_presence(conn, room["id"])
    _say(login("美穂さん"), room, "こんにちは。")
    before = len(_texts(me, room))
    _twin_in(me, room, present=False)

    jobs.after_group_message(me.app, room["id"], presence.conversation_id, _last_message_id(me, room))

    assert len([text for text in _texts(me, room) if text and text.endswith("？")]) <= before


def _last_message_id(client, room):
    return client.get(f"/api/rooms/{room['id']}/messages").json()["items"][-1]["id"]


def test_ツインがいる間の発言は役割に合わせて言明になる(conn, me, login, users, use_claude):
    def _statement(body, source, speaker):
        return {
            "source": source,
            "subject": "誠一さん",
            "speaker": speaker,
            "body": body,
            "period": "今",
            "theme": "家族",
            "petals": [],
            "utterance_numbers": [1],
            "interpretations": [],
            "confirm_question": None,
        }

    use_claude(
        言明への分割=[
            {"statements": [_statement("父は庭が好きです。", "家族の証言", "美穂さん")]},
            {"statements": [_statement("お茶をいただきました。", "縁のある人の証言", "節子さん")]},
            {"statements": [_statement("庭は好きです。", "本人の言葉", "誠一さん")]},
        ]
    )
    room = _make_room(me, users, ["美穂さん", "節子さん"])
    _twin_in(me, room)

    _say(login("美穂さん"), room, "父は庭が好きです。")
    _say(login("節子さん"), room, "お茶をいただきました。")
    _say(me, room, "庭は好きです。")

    statements = ledger.current_statements(conn)
    assert {(item.body, item.source) for item in statements} == {
        ("父は庭が好きです。", Source.FAMILY_TESTIMONY),
        ("お茶をいただきました。", Source.CONNECTED_TESTIMONY),
        ("庭は好きです。", Source.SELF_WORDS),
    }
    assert {item.share_scope.value for item in statements} == {"本人だけ"}


def test_新しい発言だけを言明に分ける(conn, me, login, users, use_claude):
    fake = use_claude()
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)
    miho = login("美穂さん")

    _say(miho, room, "1つ目の発言です。")
    _say(miho, room, "2つ目の発言です。")

    last = fake.bodies("言明への分割")[-1]["messages"][0]["content"]
    assert "[1] 美穂さん（家族）：2つ目の発言です。" in last
    assert "[1] 美穂さん（家族）：1つ目の発言です。" not in last


def test_写真はグループのツインに渡さない(me, login, users, use_claude, tmp_path):
    fake = use_claude(グループのツイン={"kind": "書かない", "text": ""})
    room = _make_room(me, users, ["美穂さん"])
    _twin_in(me, room)
    miho = login("美穂さん")
    from tests.test_photos import jpeg_bytes

    photo = miho.post("/api/photos?kind=部屋", content=jpeg_bytes()).json()
    miho.post(
        f"/api/rooms/{room['id']}/messages",
        json={"text": "写真です。", "photo_id": photo["id"], "client_key": "p"},
    )

    assert all('"image"' not in request.content.decode("utf-8") for request in fake.requests)
