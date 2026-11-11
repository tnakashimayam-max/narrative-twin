"""つながりの地図、話に出てきた人の候補、家系図、これまでの記録のテスト。

役割：本人だけが開けること、線は本人が選ぶこと、候補は本人が決めるまで足さないことを確かめる。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：1（決めるのは本人）、10（今は本人だけに見せる）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import pytest

from app import ai, ecomap
from app.schemas import FamilyLink, FamilyLinkKind, PersonCandidateDraft
from tests.fake_claude import RoleClaude

OTHERS = ["和子さん", "美穂さん", "正雄さん", "節子さん", "田中さん"]


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


def _talk_and_close(client, text):
    room = client.get("/api/rooms").json()["items"][0]
    client.post(f"/api/rooms/{room['id']}/messages", json={"text": text, "client_key": text[:60]})
    client.post(f"/api/rooms/{room['id']}/close")


# ---- 本人だけが開ける ----


@pytest.mark.parametrize("name", OTHERS)
@pytest.mark.parametrize(
    "path", ["/api/map", "/api/genogram", "/api/archive", "/api/people/candidates"]
)
def test_地図と家系図とこれまでの記録は本人だけが開ける(login, name, path):
    assert login(name).get(path).status_code == 404


@pytest.mark.parametrize("name", OTHERS)
def test_本人以外は地図を変えられない(login, users, name):
    other = login(name)

    assert other.post("/api/map/places", json={"name": "病院"}).status_code == 404
    assert other.put(
        "/api/map/links", json={"user_id": users["節子さん"].id, "strength": "強い", "direction": "お互い"}
    ).status_code == 404
    assert other.post(
        "/api/family-links",
        json={"kind": "親子", "from_user_id": users["誠一さん"].id, "to_user_id": users["美穂さん"].id},
    ).status_code == 404


# ---- つながりの地図 ----


def test_地図には人とつながりが出る(me, users):
    me.post("/api/map/places", json={"name": "ひまわりデイサービス", "kind": "デイサービス"})

    body = me.get("/api/map").json()

    assert body["self"]["name"] == "誠一さん"
    assert {item["person"]["name"] for item in body["people"]} == set(OTHERS)
    assert [item["place"]["name"] for item in body["places"]] == ["ひまわりデイサービス"]


def test_線の種類と支援の向きは本人が選ぶ(me, users):
    response = me.put(
        "/api/map/links", json={"user_id": users["美穂さん"].id, "strength": "ストレス", "direction": "相手から"}
    )

    assert response.status_code == 200
    link = next(item["link"] for item in me.get("/api/map").json()["people"] if item["person"]["name"] == "美穂さん")
    assert (link["strength"], link["direction"]) == ("ストレス", "相手から")


def test_線は選び直せる(me, users):
    target = {"user_id": users["美穂さん"].id}
    me.put("/api/map/links", json={**target, "strength": "弱い", "direction": "なし"})
    me.put("/api/map/links", json={**target, "strength": "強い", "direction": "お互い"})

    link = next(item["link"] for item in me.get("/api/map").json()["people"] if item["person"]["name"] == "美穂さん")
    assert link["strength"] == "強い"


def test_つながりにも線を引けて_はずせる(me):
    place = me.post("/api/map/places", json={"name": "町内会"}).json()

    assert me.put(
        "/api/map/links", json={"place_id": place["id"], "strength": "ふつう", "direction": "本人から"}
    ).status_code == 200
    assert me.post(f"/api/map/places/{place['id']}/remove").status_code == 204
    assert me.get("/api/map").json()["places"] == []


def test_つながりはログインの一覧に出ない(me):
    me.post("/api/map/places", json={"name": "町内会"})

    assert "町内会" not in [item["name"] for item in me.get("/api/users").json()["items"]]


def test_はずした人と亡くなった方は地図に出ない(me, users):
    me.post(f"/api/people/{users['節子さん'].id}/remove")
    me.post("/api/people", json={"name": "父", "role": "家族", "is_deceased": True})

    names = {item["person"]["name"] for item in me.get("/api/map").json()["people"]}

    assert names == {"和子さん", "美穂さん", "正雄さん", "田中さん"}


# ---- 話に出てきた人の候補 ----


def _candidate(name, relation="妻", kind="配偶者", **more):
    return {
        "name": name,
        "relation": relation,
        "relative_kind": kind,
        "related_to": None,
        "is_deceased": False,
        "gender": "女性",
        "utterance_number": 1,
        **more,
    }


def test_話に出てきた人は候補になり_根拠の発言が付く(me, use_claude):
    use_claude(人の候補={"people": [_candidate("光子")]})

    _talk_and_close(me, "光子と縁側でお茶を飲みました。")

    item = me.get("/api/people/candidates").json()["items"][0]
    assert item["name"] == "光子"
    assert item["relation"] == "妻"
    assert item["evidence"] == "光子と縁側でお茶を飲みました。"


def test_決めるまでは地図にも一覧にも足さない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("光子")]})

    _talk_and_close(me, "光子と縁側でお茶を飲みました。")

    assert "光子" not in {item["person"]["name"] for item in me.get("/api/map").json()["people"]}
    assert "光子" not in {item["name"] for item in me.get("/api/people").json()["items"]}
    assert "光子" not in {item["name"] for item in me.get("/api/users").json()["items"]}


def test_発言に出てこない名前は候補にしない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("作られた名前")]})

    _talk_and_close(me, "光子と縁側でお茶を飲みました。")

    assert me.get("/api/people/candidates").json()["items"] == []


def test_登録している人は候補にしない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("美穂さん", "長女", "子")]})

    _talk_and_close(me, "美穂さんが来てくれました。")

    assert me.get("/api/people/candidates").json()["items"] == []


def test_さんの有無だけが違う人を候補にしない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("美穂", "長女", "子")]})

    _talk_and_close(me, "美穂が来てくれました。")

    assert me.get("/api/people/candidates").json()["items"] == []


def test_登録済みの人と同じ名前は候補に出さない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("健太", relation="孫", kind="その他"), _candidate("光子")]})
    _talk_and_close(me, "健太と光子が来ました。")
    assert {item["name"] for item in me.get("/api/people/candidates").json()["items"]} == {"健太", "光子"}

    me.post("/api/people", json={"name": "健太くん", "role": "家族"})

    assert [item["name"] for item in me.get("/api/people/candidates").json()["items"]] == ["光子"]


def test_同じ続柄の人を別の候補にしない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("光子"), _candidate("妻")]})

    _talk_and_close(me, "妻の光子と縁側でお茶を飲みました。")

    assert [item["name"] for item in me.get("/api/people/candidates").json()["items"]] == ["光子"]


def test_候補を作るときに話に出てこない人の名前と続柄を渡さない(me, use_claude):
    fake = use_claude()

    _talk_and_close(me, "今日は妻と庭を見ました。")

    text = fake.bodies("人の候補")[0]["messages"][0]["content"]
    for word in ("美穂さん", "節子さん", "田中さん", "長女", "ケアマネジャー"):
        assert word not in text


def test_候補を抜き出すのは本人の発言だけ(me, use_claude):
    fake = use_claude(ツインの会話={"reply": "ツインが言った佐藤さんの話です。"})

    _talk_and_close(me, "光子と縁側でお茶を飲みました。")

    text = fake.bodies("人の候補")[0]["messages"][0]["content"]
    assert "[1] 光子と縁側でお茶を飲みました。" in text
    assert "佐藤" not in text


def test_候補の項目に人の仲の良さを持たない():
    fields = set(PersonCandidateDraft.model_fields)

    assert not {"strength", "stress", "closeness", "relationship_quality", "direction"} & fields


def test_登録すると一覧と地図に足され_夫婦の関係も入る(me, users, use_claude):
    use_claude(人の候補={"people": [_candidate("光子")]})
    _talk_and_close(me, "光子と縁側でお茶を飲みました。")
    candidate = me.get("/api/people/candidates").json()["items"][0]

    body = me.post(f"/api/people/candidates/{candidate['id']}/decide", json={"decision": "登録する"}).json()

    assert body["person"]["name"] == "光子"
    assert body["person"]["role"] == "家族"
    assert "光子" in {item["person"]["name"] for item in me.get("/api/map").json()["people"]}
    links = me.get("/api/genogram").json()["links"]
    assert [link["kind"] for link in links] == ["夫婦"]
    assert me.get("/api/people/candidates").json()["items"] == []


def test_親の候補を登録すると親子の関係が入る(me, users, use_claude):
    use_claude(人の候補={"people": [_candidate("父", "父", "親", is_deceased=True, gender="男性")]})
    _talk_and_close(me, "父は大工でした。")
    candidate = me.get("/api/people/candidates").json()["items"][0]

    me.post(f"/api/people/candidates/{candidate['id']}/decide", json={"decision": "登録する"})

    body = me.get("/api/genogram").json()
    father = next(person for person in body["people"] if person["name"] == "父")
    assert father["generation"] == -1
    assert father["is_deceased"] is True
    assert "父" not in {item["name"] for item in me.get("/api/users").json()["items"]}


@pytest.mark.parametrize("decision", ["ちがう", "今はいい"])
def test_ちがうと今はいいでは足さない(me, use_claude, decision):
    use_claude(人の候補={"people": [_candidate("光子")]})
    _talk_and_close(me, "光子と縁側でお茶を飲みました。")
    candidate = me.get("/api/people/candidates").json()["items"][0]

    body = me.post(f"/api/people/candidates/{candidate['id']}/decide", json={"decision": decision}).json()

    assert body["person"] is None
    assert "光子" not in {item["name"] for item in me.get("/api/people").json()["items"]}
    assert me.get("/api/people/candidates").json()["items"] == []


def test_ちがうと決めた候補をまた出さない(me, use_claude):
    use_claude(人の候補={"people": [_candidate("光子")]})
    _talk_and_close(me, "光子と縁側でお茶を飲みました。")
    candidate = me.get("/api/people/candidates").json()["items"][0]
    me.post(f"/api/people/candidates/{candidate['id']}/decide", json={"decision": "ちがう"})

    _talk_and_close(me, "光子がお茶をいれてくれました。")

    assert me.get("/api/people/candidates").json()["items"] == []


# ---- 家系図 ----


def _link(kind, from_id, to_id):
    return FamilyLink(id=1, from_user_id=from_id, to_user_id=to_id, kind=kind, created_at="2026-11-02T10:00:00")


def test_親から孫までを本人から求める():
    links = [
        _link(FamilyLinkKind.PARENT_CHILD, 10, 1),
        _link(FamilyLinkKind.SPOUSE, 1, 2),
        _link(FamilyLinkKind.PARENT_CHILD, 1, 3),
        _link(FamilyLinkKind.PARENT_CHILD, 3, 4),
        _link(FamilyLinkKind.PARENT_CHILD, 20, 10),
    ]

    assert ecomap.generations(1, links) == {1: 0, 10: -1, 2: 0, 3: 1, 4: 2}


def test_関係を足すと家系図に出て_消せる(me, users):
    link = me.post(
        "/api/family-links",
        json={"kind": "親子", "from_user_id": users["誠一さん"].id, "to_user_id": users["美穂さん"].id},
    ).json()

    people = me.get("/api/genogram").json()["people"]
    assert next(person for person in people if person["name"] == "美穂さん")["generation"] == 1

    assert me.delete(f"/api/family-links/{link['id']}").status_code == 204
    assert "美穂さん" not in {person["name"] for person in me.get("/api/genogram").json()["people"]}


def test_同じ人どうしの関係は足せない(me, users):
    response = me.post(
        "/api/family-links",
        json={"kind": "夫婦", "from_user_id": users["誠一さん"].id, "to_user_id": users["誠一さん"].id},
    )

    assert response.status_code == 400


# ---- これまでの記録 ----


def test_これまでの記録は日付ごとに会話と便りを読み返せる(me, use_claude):
    use_claude()
    _talk_and_close(me, "紫陽花が咲きました。")
    me.post("/api/letters", json={"body": "便りです。", "share_with": "家族"})
    me.post("/api/dev/date", json={"date": "2031-01-02"})
    _talk_and_close(me, "次の日の話です。")

    days = me.get("/api/archive").json()["days"]

    assert days[0]["date"] == "2031-01-02"
    assert days[0]["conversations"][0]["items"][0]["text"] == "次の日の話です。"
    assert days[1]["conversations"][0]["items"][0]["text"] == "紫陽花が咲きました。"
    assert days[1]["letters"][0]["body"] == "便りです。"


def test_これまでの記録は月を選んでその月の日をすべて読める(me, use_claude):
    use_claude()
    _talk_and_close(me, "紫陽花が咲きました。")
    me.post("/api/dev/date", json={"date": "2031-02-03"})
    _talk_and_close(me, "二月の話です。")
    me.post("/api/letters", json={"body": "二月の便りです。", "share_with": "家族"})

    months = me.get("/api/archive/months").json()["months"]
    february = me.get("/api/archive?month=2031-02").json()

    assert months[0] == "2031-02"
    assert len(months) == 2
    assert [day["date"] for day in february["days"]] == ["2031-02-03"]
    assert february["days"][0]["conversations"][0]["items"][0]["text"] == "二月の話です。"
    assert february["days"][0]["letters"][0]["body"] == "二月の便りです。"
    assert february["next_before"] is None
    assert me.get("/api/archive?month=2031-13").status_code == 400


def test_これまでの記録は30日ずつ読み_次の日付を返す(conn, me, use_claude):
    use_claude()
    for day in range(1, 33):
        me.post("/api/dev/date", json={"date": f"2031-03-{day:02d}" if day <= 31 else "2031-04-01"})
        _talk_and_close(me, f"{day}日目の話です。")

    first = me.get("/api/archive").json()
    second = me.get(f"/api/archive?before={first['next_before']}").json()

    assert len(first["days"]) == 30
    assert first["days"][0]["date"] == "2031-04-01"
    assert first["next_before"] == first["days"][-1]["date"]
    assert [day["date"] for day in second["days"]][:2] == ["2031-03-02", "2031-03-01"]


def test_月の一覧と月の読み込みは本人だけが開ける(login):
    for name in ("美穂さん", "田中さん"):
        client = login(name)
        assert client.get("/api/archive/months").status_code in (403, 404)


def test_これまでの記録に写真が出る(me, use_claude):
    from tests.test_photos import jpeg_bytes

    use_claude(
        ツインの会話={"reply": "写真ですね。", "photo_description": "紫陽花の鉢です。"},
    )
    photo = me.post("/api/photos?kind=会話", content=jpeg_bytes()).json()
    room = me.get("/api/rooms").json()["items"][0]
    me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": photo["id"], "client_key": "p"})

    days = me.get("/api/archive").json()["days"]

    assert days[0]["conversations"][0]["items"][0]["photo_id"] == photo["id"]


def test_人だけの部屋の発言はこれまでの記録に入らない(me, users, use_claude):
    use_claude()
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()
    me.post(f"/api/rooms/{room['id']}/messages", json={"text": "内緒です。", "client_key": "k"})

    assert me.get("/api/archive").json()["days"] == []
