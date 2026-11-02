"""便りとタイムラインのテスト。

役割：共有するまで見えないこと、相手の絞り込み、コメントが証言になること、スレッドのツインを確かめる。
4つの極：2. 分かち合う。
守る原則：3（出どころを分ける）、10（見える範囲はサーバーで絞る）、14（スレッドのツインは問いを1つ）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import pytest

from app import ai, ledger
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


def _talk_and_close(client, text="紫陽花が咲きました。"):
    room = client.get("/api/rooms").json()["items"][0]
    client.post(f"/api/rooms/{room['id']}/messages", json={"text": text, "client_key": text[:60]})
    return client.post(f"/api/rooms/{room['id']}/close").json()["conversation_id"]


def _share(client, body="紫陽花が咲きました。", share_with="家族", **more):
    response = client.post("/api/letters", json={"body": body, "share_with": share_with, **more})
    assert response.status_code == 201, response.text
    return response.json()


def _comment(client, letter, text, key=None):
    return client.post(f"/api/letters/{letter['id']}/comments", json={"text": text, "client_key": key or text[:60]})


def _bodies(client):
    return [item["body"] for item in client.get("/api/timeline").json()["items"]]


# ---- 下書きと共有 ----


def test_下書きは保存されず_共有するまでタイムラインに出ない(conn, me, login, use_claude):
    use_claude(便りの下書き={"body": "紫陽花が咲きました。今年は色が濃いです。"})
    conversation_id = _talk_and_close(me)

    draft = me.post(f"/api/conversations/{conversation_id}/letter-draft")

    assert draft.status_code == 200
    assert draft.json()["body"] == "紫陽花が咲きました。今年は色が濃いです。"
    assert conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0] == 0
    assert _bodies(login("美穂さん")) == []


def test_本人が直した内容で共有される(conn, me, login, use_claude):
    use_claude(便りの下書き={"body": "下書きの文です。"})
    conversation_id = _talk_and_close(me)
    me.post(f"/api/conversations/{conversation_id}/letter-draft")

    _share(me, body="本人が直した文です。", conversation_id=conversation_id)

    assert _bodies(login("美穂さん")) == ["本人が直した文です。"]


def test_下書きは本人の発言だけから作る(me, use_claude):
    fake = use_claude(
        ツインの会話={"reply": "ツインだけが言った文です。"},
        便りの下書き={"body": "下書きです。"},
    )
    conversation_id = _talk_and_close(me)

    me.post(f"/api/conversations/{conversation_id}/letter-draft")

    text = fake.bodies("便りの下書き")[0]["messages"][0]["content"]
    assert "紫陽花が咲きました。" in text
    assert "ツインだけが言った文です。" not in text


def test_下書きが120字を超えたら受け取らない(me, use_claude):
    use_claude(便りの下書き={"body": "あ" * 121})
    conversation_id = _talk_and_close(me)

    assert me.post(f"/api/conversations/{conversation_id}/letter-draft").status_code == 503


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は便りを作れない(me, login, use_claude, name):
    use_claude()
    conversation_id = _talk_and_close(me)
    other = login(name)

    assert other.post(f"/api/conversations/{conversation_id}/letter-draft").status_code == 404
    assert other.post("/api/letters", json={"body": "偽の便り", "share_with": "家族"}).status_code == 404


@pytest.mark.parametrize("scope", ["本人だけ", "意思決定支援用"])
def test_便りの相手は家族か縁のある人までだけ(me, scope):
    assert me.post("/api/letters", json={"body": "便り", "share_with": scope}).status_code == 400


def test_便りを共有しても言明の共有範囲は変わらない(conn, me, use_claude):
    use_claude(
        言明への分割={
            "statements": [
                {
                    "source": "本人の言葉",
                    "subject": "誠一さん",
                    "speaker": "誠一さん",
                    "body": "紫陽花が咲きました。",
                    "period": "今",
                    "theme": "好きなこと",
                    "petals": [],
                    "utterance_numbers": [1],
                    "interpretations": [],
                    "confirm_question": None,
                }
            ]
        }
    )
    conversation_id = _talk_and_close(me)

    _share(me, conversation_id=conversation_id, share_with="縁のある人まで")

    assert {item.share_scope.value for item in ledger.current_statements(conn)} == {"本人だけ"}


# ---- 見える範囲 ----


def test_美穂さんは家族と縁のある人まで_節子さんは縁のある人までだけ_田中さんは見えない(me, login):
    _share(me, body="家族への便り", share_with="家族")
    _share(me, body="縁のある人までの便り", share_with="縁のある人まで")

    assert _bodies(login("美穂さん")) == ["縁のある人までの便り", "家族への便り"]
    assert _bodies(login("節子さん")) == ["縁のある人までの便り"]
    assert _bodies(login("田中さん")) == []


def test_見えない便りにはコメントできない(me, login):
    letter = _share(me, share_with="家族")

    assert _comment(login("節子さん"), letter, "見えないはず").status_code == 404
    assert _comment(login("田中さん"), letter, "見えないはず").status_code == 404


def test_コメントの順番から見えない便りのコメントの数が分からない(me, login, use_claude):
    use_claude()
    hidden = _share(me, body="家族への便り", share_with="家族")
    for index in range(3):
        _comment(login("美穂さん"), hidden, f"家族のコメント{index}")
    shown = _share(me, body="みんなへの便り", share_with="縁のある人まで")

    comment = _comment(login("節子さん"), shown, "きれいですね。").json()
    thread = login("節子さん").get("/api/timeline").json()["items"][0]["thread"]

    assert comment["seq"] == 1
    assert [item["seq"] for item in thread] == [1]


def test_タイムラインに数の項目を持たない(me, login):
    letter = _share(me, share_with="家族")

    item = login("美穂さん").get("/api/timeline").json()["items"][0]

    forbidden = {"likes", "like_count", "read_count", "seen_by", "comment_count", "recommended"}
    assert not forbidden & item.keys()
    assert letter["id"] == item["id"]


# ---- コメント ----


def test_コメントは発言としても保存され_吹き出しと同じ形で返る(conn, me, login, use_claude):
    use_claude()
    letter = _share(me)

    comment = _comment(login("美穂さん"), letter, "きれいですね。").json()

    assert comment["text"] == "きれいですね。"
    assert comment["is_twin"] is False
    assert conn.execute("SELECT text, place FROM utterances").fetchone()["place"] == "スレッド"


def test_同じ印で送ってもコメントは増えない(me, login, use_claude):
    use_claude()
    letter = _share(me)
    miho = login("美穂さん")

    _comment(miho, letter, "きれいですね。", key="k")
    _comment(miho, letter, "きれいですね。", key="k")

    assert len(miho.get("/api/timeline").json()["items"][0]["thread"]) == 1


def _testimony(body, source, speaker, number=1):
    return {
        "source": source,
        "subject": "誠一さん",
        "speaker": speaker,
        "body": body,
        "period": "今",
        "theme": "好きなこと",
        "petals": [],
        "utterance_numbers": [number],
        "interpretations": [],
        "confirm_question": None,
    }


def test_コメントは役割に合わせた証言になり_本人の意思にしない(conn, me, login, use_claude):
    use_claude(言明への分割={"statements": [_testimony("父は紫陽花が好きです。", "家族の証言", "美穂さん")]})
    letter = _share(me)

    _comment(login("美穂さん"), letter, "父は紫陽花が好きです。")

    statements = ledger.current_statements(conn)
    assert [item.source for item in statements] == [Source.FAMILY_TESTIMONY]


def test_家族のコメントを本人の言葉として入れない(conn, me, login, use_claude):
    use_claude(言明への分割={"statements": [_testimony("父は紫陽花が好きです。", "本人の言葉", "誠一さん")]})
    letter = _share(me)

    _comment(login("美穂さん"), letter, "父は紫陽花が好きです。")

    assert ledger.current_statements(conn) == []


def test_本人の答えは本人の言葉になる(conn, me, login, use_claude):
    use_claude(言明への分割={"statements": [_testimony("二十年前からです。", "本人の言葉", "誠一さん")]})
    letter = _share(me)

    _comment(me, letter, "二十年前からです。")

    assert [item.source for item in ledger.current_statements(conn)] == [Source.SELF_WORDS]


def test_言明の共有範囲は本人だけのまま(conn, me, login, use_claude):
    use_claude(言明への分割={"statements": [_testimony("父は紫陽花が好きです。", "家族の証言", "美穂さん")]})
    letter = _share(me, share_with="縁のある人まで")

    _comment(login("美穂さん"), letter, "父は紫陽花が好きです。")

    assert {item.share_scope.value for item in ledger.current_statements(conn)} == {"本人だけ"}


# ---- スレッドのツイン ----


def test_コメントが付いたらツインが本人への問いを1つ書く(me, login, use_claude):
    use_claude(スレッドの問い={"question": "誠一さん、この紫陽花はいつからですか？"})
    letter = _share(me)

    _comment(login("美穂さん"), letter, "きれいですね。")

    thread = me.get("/api/timeline").json()["items"][0]["thread"]
    assert [(item["is_twin"], item["text"]) for item in thread] == [
        (False, "きれいですね。"),
        (True, "誠一さん、この紫陽花はいつからですか？"),
    ]


def test_本人が答えるまで次の問いを書かない(me, login, use_claude):
    fake = use_claude(スレッドの問い={"question": "誠一さん、この紫陽花はいつからですか？"})
    letter = _share(me)
    miho = login("美穂さん")

    _comment(miho, letter, "きれいですね。")
    _comment(miho, letter, "本当にきれいです。")

    assert len(fake.bodies("スレッドの問い")) == 1

    use_claude(スレッドの問い={"question": "誠一さん、どなたと育てていますか？"})
    _comment(me, letter, "二十年前からです。")
    _comment(miho, letter, "長いですね。")

    thread = me.get("/api/timeline").json()["items"][0]["thread"]
    assert [item["text"] for item in thread if item["is_twin"]] == [
        "誠一さん、この紫陽花はいつからですか？",
        "誠一さん、どなたと育てていますか？",
    ]


def test_本人のコメントでは問いを書かない(me, use_claude):
    fake = use_claude(スレッドの問い={"question": "誠一さん、どうですか？"})
    letter = _share(me)

    _comment(me, letter, "咲きました。")

    assert fake.bodies("スレッドの問い") == []


def test_スレッドのツインに渡すのは便りとコメントだけ(conn, me, login, use_claude):
    fake = use_claude(
        ツインの会話={"reply": "架空の秘密を聞きました。"},
        会話の要約={"summary": "宝くじの要約です。"},
        言明への分割={"statements": [_testimony("宝くじが当たりました。", "本人の言葉", "誠一さん")]},
        スレッドの問い={"question": None},
    )
    conversation_id = _talk_and_close(me, "宝くじが当たりました。")
    fake.requests.clear()
    letter = _share(me, body="紫陽花の便りです。", conversation_id=conversation_id)

    _comment(login("美穂さん"), letter, "きれいですね。")

    body = str(fake.bodies("スレッドの問い"))
    assert "紫陽花の便りです。" in body
    assert "きれいですね。" in body
    assert "宝くじ" not in body
    assert "架空の秘密" not in body


def test_問いの形でない答えは書かない(me, login, use_claude):
    use_claude(スレッドの問い={"question": "紫陽花ですね。"})
    letter = _share(me)

    _comment(login("美穂さん"), letter, "きれいですね。")

    thread = me.get("/api/timeline").json()["items"][0]["thread"]
    assert [item["is_twin"] for item in thread] == [False]


def test_健康のことはスレッドでも聞かない(me, login, use_claude):
    use_claude(スレッドの問い={"question": "誠一さん、お薬は飲んでいますか？"})
    letter = _share(me)

    _comment(login("美穂さん"), letter, "お元気そうですね。")

    thread = me.get("/api/timeline").json()["items"][0]["thread"]
    assert [item["is_twin"] for item in thread] == [False]
