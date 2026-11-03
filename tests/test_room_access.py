"""登場人物と部屋の権限のテスト。

役割：招く操作、日付の切り替え、だれでも作れる部屋、ツインの権限、はずす、出る、未読と既読を確かめる。
4つの極：2. 分かち合う、4. 10年後に代弁する。
守る原則：10（託す人と見せる範囲は本人が決める）、14（タイムラインに既読を作らない）、
          15（ツインは本人がいる招かれた部屋だけを読む）。

通信だけを差し替える。本物の Claude API は呼ばない。設計は docs/specs/2026-11-03-登場人物とチャット.md にある。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai, config, db
from app.main import create_app
from app.schemas import PhotoKind, ShareScope
from tests.fake_claude import RoleClaude
from tests.test_photos import jpeg_bytes

ROOT = Path(__file__).resolve().parent.parent
PERSON = {"name": "山田さん", "relation": "ケアマネジャー"}


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _pair(client, other_id):
    return client.post("/api/rooms", json={"kind": "1対1", "member_ids": [other_id]})


def _group(client, *others, name="部屋"):
    response = client.post("/api/rooms", json={"kind": "グループ", "member_ids": [o.id for o in others], "name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _say(client, room_id, text):
    response = client.post(f"/api/rooms/{room_id}/messages", json={"text": text, "client_key": text[:60]})
    assert response.status_code == 200
    return response


def _view(client, room_id):
    return client.get(f"/api/rooms/{room_id}/messages").json()


def _summary(client, room_id):
    return next(item for item in client.get("/api/rooms").json()["items"] if item["id"] == room_id)


def _read(client, room_id, seq):
    return client.post(f"/api/rooms/{room_id}/read", json={"seq": seq})


def _twin(client, room_id, present=True):
    return client.post(f"/api/rooms/{room_id}/twin", json={"present": present})


# ---- 招く ----


@pytest.mark.parametrize("name", ["誠一さん", "美穂さん"])
def test_本人と立場が家族の託された人は専門職を招ける(login, name):
    response = login(name).post("/api/people/professional", json=PERSON)

    assert response.status_code == 201
    assert response.json()["role"] == "専門職"


@pytest.mark.parametrize("name", ["和子さん", "正雄さん", "節子さん"])
def test_託されていない家族と縁のある人は招けない(login, name):
    assert login(name).post("/api/people/professional", json=PERSON).status_code == 404


def test_託された人は家族と縁のある人を登録できない(login):
    response = login("美穂さん").post("/api/people", json={"name": "新しい人", "role": "家族"})

    assert response.status_code == 404


def test_招かれた専門職は次の専門職を招けない(login):
    assert login("田中さん").post("/api/people/professional", json=PERSON).status_code == 404


def test_招くと人の登録と託す設定が両方入る(conn, login):
    person_id = login("美穂さん").post("/api/people/professional", json=PERSON).json()["id"]

    person = db.get_user(conn, person_id)
    assert person.role.value == "専門職"
    row = conn.execute("SELECT scope FROM delegations WHERE delegate_user_id = ?", (person_id,)).fetchone()
    assert row["scope"] == ShareScope.DECISION_SUPPORT.value


def test_同じ名前の人は招けず_託す設定も増えない(conn, login):
    miho = login("美穂さん")
    before = conn.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]

    assert miho.post("/api/people/professional", json={"name": "節子さん"}).status_code == 400

    assert conn.execute("SELECT COUNT(*) FROM delegations").fetchone()[0] == before


def test_招かれた人はログインでき_意思決定支援用の記録だけが見える(db_path, login, add_statement):
    for scope in ShareScope:
        add_statement(f"{scope.value}の記録です。", scope=scope)
    person_id = login("誠一さん").post("/api/people/professional", json=PERSON).json()["id"]

    invited = TestClient(create_app(db_path))
    assert invited.post("/api/login", json={"user_id": person_id}).status_code == 200
    invited.headers["X-NT-User"] = str(person_id)

    bodies = {item["body"] for item in invited.get("/api/statements").json()["items"]}
    assert bodies == {"意思決定支援用の記録です。"}


def test_招けるかを本人と託された人にだけ真で返す(login):
    assert login("誠一さん").get("/api/me").json()["can_invite_professional"] is True
    assert login("美穂さん").get("/api/me").json()["can_invite_professional"] is True
    assert login("和子さん").get("/api/me").json()["can_invite_professional"] is False
    assert login("田中さん").get("/api/me").json()["can_invite_professional"] is False


# ---- 日付の切り替え ----


@pytest.fixture
def dated(db_path, conn, users, monkeypatch):
    """1つのアプリを、日付を動かしながら別の人として使う。日付はアプリごとに持つため。"""
    monkeypatch.setattr(config, "PROFESSIONAL_FROM", date(2036, 1, 1))
    app = create_app(db_path)
    return app, lambda name: TestClient(app, headers={"X-NT-User": str(users[name].id)})


def test_切り替えの日付より前は招けない(dated):
    _, person = dated

    assert person("美穂さん").post("/api/people/professional", json=PERSON).status_code == 404
    assert person("美穂さん").get("/api/me").json()["can_invite_professional"] is False


def test_切り替えの日付以降は招ける(dated):
    app, person = dated
    TestClient(app).post("/api/dev/date", json={"date": "2036-05-01"})

    assert person("美穂さん").post("/api/people/professional", json=PERSON).status_code == 201


def test_日付を戻すと専門職はログイン一覧と相手の一覧から消え_ログインできない(dated, users):
    app, person = dated
    tanaka = users["田中さん"].id

    def names():
        return [item["name"] for item in TestClient(app).get("/api/users").json()["items"]]

    TestClient(app).post("/api/dev/date", json={"date": "2036-05-01"})
    assert "田中さん" in names()
    assert person("田中さん").get("/api/me").status_code == 200
    assert tanaka in [item["id"] for item in person("美穂さん").get("/api/people/choices").json()["items"]]

    TestClient(app).post("/api/dev/date", json={"date": "2030-05-01"})
    assert "田中さん" not in names()
    assert person("田中さん").get("/api/me").status_code == 401
    assert TestClient(app).post("/api/login", json={"user_id": tanaka}).status_code == 404
    assert tanaka not in [item["id"] for item in person("美穂さん").get("/api/people/choices").json()["items"]]
    room = person("美穂さん").post("/api/rooms", json={"kind": "1対1", "member_ids": [tanaka]})
    assert room.status_code == 400


def test_自分用の起動は日付で切り替えない(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFESSIONAL_FROM", date(2036, 1, 1))
    client = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))
    self_id = client.post("/api/self", json={"name": "山田一郎"}).json()["id"]
    client.headers["X-NT-User"] = str(self_id)

    person_id = client.post("/api/people/professional", json=PERSON).json()["id"]

    assert person_id in [item["id"] for item in client.get("/api/users").json()["items"]]
    assert client.post("/api/login", json={"user_id": person_id}).status_code == 200


# ---- 部屋を作る ----


def test_本人以外も部屋を作れて_自分が必ず入る(login, users):
    response = _pair(login("美穂さん"), users["和子さん"].id)

    assert response.status_code == 201
    assert {member["name"] for member in response.json()["members"]} == {"美穂さん", "和子さん"}


def test_本人がいない部屋は本人の一覧に出ず_本人が開くと404になる(login, users):
    room_id = _pair(login("美穂さん"), users["和子さん"].id).json()["id"]
    me = login("誠一さん")

    assert room_id not in [item["id"] for item in me.get("/api/rooms").json()["items"]]
    assert me.get(f"/api/rooms/{room_id}/messages").status_code == 404


def test_本人がいない部屋の本文はAIに渡らず言明にならない(conn, login, users, claude):
    miho = login("美穂さん")
    room_id = _pair(miho, users["和子さん"].id).json()["id"]

    _say(miho, room_id, "お父さんの様子が気になります。")

    assert claude.requests == []
    assert conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0


def test_1対1の部屋は同じ2人なら増えない(login, users):
    first = _pair(login("美穂さん"), users["和子さん"].id).json()["id"]

    assert _pair(login("美穂さん"), users["和子さん"].id).json()["id"] == first
    assert _pair(login("和子さん"), users["美穂さん"].id).json()["id"] == first


def test_本人の1対1と別の人どうしの1対1を取り違えない(login, users):
    with_self = _pair(login("誠一さん"), users["和子さん"].id).json()["id"]

    assert _pair(login("美穂さん"), users["和子さん"].id).json()["id"] != with_self


def test_部屋の相手の一覧は生まれた年とメモを返さない(login, users):
    login("誠一さん").put(f"/api/people/{users['和子さん'].id}", json={"birth_year": 1950, "memo": "目印のメモ"})

    items = login("美穂さん").get("/api/people/choices").json()["items"]

    assert users["美穂さん"].id not in [item["id"] for item in items]
    kazuko = next(item for item in items if item["name"] == "和子さん")
    assert set(kazuko) == {"id", "name", "called_name", "role", "photo_id"}


# ---- ツイン ----


def test_立場が家族の託された人はツインを入れてはずせる(login, users, claude):
    miho = login("美穂さん")
    room_id = _group(miho, users["誠一さん"], users["節子さん"])

    assert _twin(miho, room_id).json()["room"]["twin_present"] is True
    assert _twin(miho, room_id, present=False).json()["room"]["twin_present"] is False


@pytest.mark.parametrize("name", ["和子さん", "節子さん"])
def test_託されていない人はツインを入れられない(login, users, name):
    room_id = _group(login("誠一さん"), users["和子さん"], users["節子さん"])

    assert _twin(login(name), room_id).status_code == 404


def test_招かれた専門職はツインを入れることもはずすこともできない(login, users, claude):
    me = login("誠一さん")
    room_id = _group(me, users["美穂さん"], users["田中さん"])
    tanaka = login("田中さん")

    assert _twin(tanaka, room_id).status_code == 404
    _twin(me, room_id)
    assert _twin(tanaka, room_id, present=False).status_code == 404
    assert _view(me, room_id)["room"]["twin_present"] is True


def test_本人がいない部屋にはツインを入れられない(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["和子さん"], users["節子さん"])

    assert _twin(miho, room_id).status_code == 400


def test_can_manage_twinは本人と立場が家族の託された人だけ真になる(login, users):
    me = login("誠一さん")
    room_id = _group(me, users["美穂さん"], users["和子さん"], users["田中さん"])

    assert _summary(me, room_id)["can_manage_twin"] is True
    assert _summary(login("美穂さん"), room_id)["can_manage_twin"] is True
    assert _summary(login("和子さん"), room_id)["can_manage_twin"] is False
    assert _summary(login("田中さん"), room_id)["can_manage_twin"] is False


def test_本人がいない部屋ではcan_manage_twinが偽になる(login, users):
    miho = login("美穂さん")

    assert _summary(miho, _group(miho, users["和子さん"], users["節子さん"]))["can_manage_twin"] is False


# ---- はずす、出る ----


def _remove(client, room_id, user):
    return client.post(f"/api/rooms/{room_id}/members/{user.id}/remove")


def test_作った人と本人はグループから人をはずせる(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["誠一さん"], users["和子さん"], users["節子さん"])

    assert _remove(miho, room_id, users["節子さん"]).status_code == 204
    assert _remove(login("誠一さん"), room_id, users["和子さん"]).status_code == 204
    assert {member["name"] for member in _summary(miho, room_id)["members"]} == {"誠一さん", "美穂さん"}


def test_作った人でも本人でもない人ははずせない(login, users):
    room_id = _group(login("美穂さん"), users["和子さん"], users["節子さん"])

    assert _remove(login("和子さん"), room_id, users["節子さん"]).status_code == 404


def test_部屋の人でない人ははずせない(login, users):
    room_id = _group(login("美穂さん"), users["和子さん"], users["節子さん"])

    assert _remove(login("誠一さん"), room_id, users["節子さん"]).status_code == 404


def test_本人はほかの人にはずされない(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["誠一さん"], users["和子さん"])

    assert _remove(miho, room_id, users["誠一さん"]).status_code == 400
    assert "誠一さん" in {member["name"] for member in _summary(miho, room_id)["members"]}


def test_はずせる人にだけcan_remove_membersが真になる(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["誠一さん"], users["和子さん"])

    assert _summary(miho, room_id)["can_remove_members"] is True
    assert _summary(login("誠一さん"), room_id)["can_remove_members"] is True
    assert _summary(login("和子さん"), room_id)["can_remove_members"] is False


def test_はずれた人は部屋の発言と写真を開けない(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["節子さん"], users["和子さん"])
    photo_id = miho.post(f"/api/photos?kind={PhotoKind.ROOM.value}", content=jpeg_bytes()).json()["id"]
    miho.post(f"/api/rooms/{room_id}/messages", json={"text": "庭です。", "photo_id": photo_id, "client_key": "k"})
    setsuko = login("節子さん")
    assert setsuko.get(f"/api/photos/{photo_id}").status_code == 200

    _remove(miho, room_id, users["節子さん"])

    assert setsuko.get(f"/api/rooms/{room_id}/messages").status_code == 404
    assert setsuko.get(f"/api/photos/{photo_id}").status_code == 404
    assert room_id not in [item["id"] for item in setsuko.get("/api/rooms").json()["items"]]
    # 記録は残る
    assert "庭です。" in [item["text"] for item in _view(miho, room_id)["items"]]


def test_自分でグループを出られる(login, users):
    room_id = _group(login("美穂さん"), users["和子さん"], users["節子さん"])
    kazuko = login("和子さん")

    assert kazuko.post(f"/api/rooms/{room_id}/leave").status_code == 204
    assert kazuko.get(f"/api/rooms/{room_id}/messages").status_code == 404


def test_部屋の人でない人は出られない(login, users):
    room_id = _group(login("美穂さん"), users["和子さん"], users["節子さん"])

    assert login("誠一さん").post(f"/api/rooms/{room_id}/leave").status_code == 404


def test_1対1とツインとの部屋からは出られず_はずせない(login, users):
    me = login("誠一さん")
    pair_id = _pair(me, users["和子さん"].id).json()["id"]
    twin_id = me.get("/api/rooms").json()["items"][0]["id"]

    assert me.post(f"/api/rooms/{pair_id}/leave").status_code == 400
    assert me.post(f"/api/rooms/{twin_id}/leave").status_code == 400
    assert _remove(me, pair_id, users["和子さん"]).status_code == 400


def test_本人が出るとツインもはずれ_会話が閉じる(conn, login, users, claude):
    me = login("誠一さん")
    room_id = _group(me, users["美穂さん"], users["節子さん"])
    _twin(me, room_id)

    assert me.post(f"/api/rooms/{room_id}/leave").status_code == 204

    miho = login("美穂さん")
    assert _view(miho, room_id)["room"]["twin_present"] is False
    assert _summary(miho, room_id)["can_manage_twin"] is False
    row = conn.execute("SELECT closed_at FROM conversations WHERE room_id = ?", (room_id,)).fetchone()
    assert row["closed_at"] is not None
    assert _twin(miho, room_id).status_code == 400


def test_はずしたことと出たことが部屋に表示される(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["和子さん"], users["節子さん"])

    _remove(miho, room_id, users["節子さん"])
    login("和子さん").post(f"/api/rooms/{room_id}/leave")

    notices = [item["text"] for item in _view(miho, room_id)["items"] if item["kind"] == "お知らせ"]
    assert notices == ["節子さんがこの部屋からはずれました。", "和子さんがこの部屋を出ました。"]


# ---- 未読と既読 ----


def test_未読の数は自分の発言とお知らせを数えない(login, users):
    miho = login("美穂さん")
    kazuko = login("和子さん")
    room_id = _group(miho, users["和子さん"], users["節子さん"])
    _say(miho, room_id, "ひとつ目")
    _say(kazuko, room_id, "ふたつ目")
    _remove(miho, room_id, users["節子さん"])

    assert _summary(kazuko, room_id)["unread_count"] == 1
    assert _summary(miho, room_id)["unread_count"] == 1


def test_読むと未読の数が0になり_最初の未読の順番を返さない(login, users):
    miho = login("美穂さん")
    kazuko = login("和子さん")
    room_id = _pair(miho, users["和子さん"].id).json()["id"]
    _say(miho, room_id, "ひとつ目")
    _say(miho, room_id, "ふたつ目")
    assert _view(kazuko, room_id)["first_unread_seq"] == 1

    assert _read(kazuko, room_id, 2).status_code == 204

    assert _summary(kazuko, room_id)["unread_count"] == 0
    assert _view(kazuko, room_id)["first_unread_seq"] is None


def test_読み込みだけでは読んだことにならない(login, users):
    miho = login("美穂さん")
    kazuko = login("和子さん")
    room_id = _pair(miho, users["和子さん"].id).json()["id"]
    _say(miho, room_id, "ひとつ目")

    _view(kazuko, room_id)

    assert _summary(kazuko, room_id)["unread_count"] == 1
    assert _view(miho, room_id)["others_read_seqs"] == [0]


def test_読んだ位置は戻らず_部屋の行の数を超えない(login, users):
    miho = login("美穂さん")
    kazuko = login("和子さん")
    room_id = _pair(miho, users["和子さん"].id).json()["id"]
    _say(miho, room_id, "ひとつ目")
    _say(miho, room_id, "ふたつ目")

    _read(kazuko, room_id, 2)
    _read(kazuko, room_id, 1)
    assert _view(miho, room_id)["others_read_seqs"] == [2]

    _read(kazuko, room_id, 99)
    assert _view(miho, room_id)["others_read_seqs"] == [2]


def test_既読の数は部屋にいるほかの人だけを数え_名前を返さない(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["和子さん"], users["節子さん"])
    _say(miho, room_id, "ひとつ目")
    _read(login("和子さん"), room_id, 1)

    assert sorted(_view(miho, room_id)["others_read_seqs"]) == [0, 1]


def test_はずれた人は既読の数に入らない(login, users):
    miho = login("美穂さん")
    room_id = _group(miho, users["和子さん"], users["節子さん"])
    _say(miho, room_id, "ひとつ目")
    _read(login("節子さん"), room_id, 1)

    _remove(miho, room_id, users["節子さん"])

    assert _view(miho, room_id)["others_read_seqs"] == [0]


def test_部屋の人以外は読んだ位置を見られず_送れない(login, users):
    room_id = _pair(login("美穂さん"), users["和子さん"].id).json()["id"]
    setsuko = login("節子さん")

    assert _read(setsuko, room_id, 1).status_code == 404
    assert setsuko.get(f"/api/rooms/{room_id}/messages").status_code == 404


def test_ツインとの部屋には未読の数と既読を出さない(login, claude):
    me = login("誠一さん")
    twin_id = me.get("/api/rooms").json()["items"][0]["id"]
    _say(me, twin_id, "今朝、庭を見ました。")

    view = _view(me, twin_id)
    assert "unread_count" not in view["room"]
    assert "first_unread_seq" not in view
    assert "others_read_seqs" not in view
    assert _read(me, twin_id, 1).status_code == 400


def test_読んだ位置はAIの入力と言明に入らない():
    for name in ("memory.py", "twin.py", "ai.py", "prompts.py", "splitter.py", "ledger.py", "devlog.py"):
        assert "room_reads" not in (ROOT / "app" / name).read_text(encoding="utf-8")
