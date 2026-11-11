"""プロフィール写真の枠とデモの顔のテスト。

役割：丸いアイコンとは別の枠の見える範囲と入れ替え、デモの顔を入れる処理、表の変更を確かめる。
4つの極：2. 分かち合う。
守る原則：8（デモの顔は架空用の起動だけ）、10（見せる範囲はサーバーで確かめる）。

写真はこのファイルの中で Pillow で作る。本物の顔の写真は読まない。
"""

from __future__ import annotations

import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import ai, config, db, demo
from app.main import create_app
from app.schemas import PhotoKind
from fiction import faces, snapshot
from tests.fake_claude import RoleClaude

SEEDED = ["誠一さん", "美穂さん", "正雄さん", "節子さん"]


def jpeg_bytes(color=(200, 180, 150)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture
def face_folder(monkeypatch, tmp_path):
    """整えた顔の写真が7枚あるフォルダを、本物の置き場の代わりにする。"""
    folder = tmp_path / "faces"
    folder.mkdir()
    for name in faces.FACE_FILES.values():
        (folder / name).write_bytes(jpeg_bytes())
    monkeypatch.setattr(config, "DEMO_FACES_DIR", folder)
    return folder


@pytest.fixture
def use_claude(monkeypatch):
    def _use(**answers):
        fake = RoleClaude(**answers)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


@pytest.fixture
def fictional(tmp_path):
    return config.deployment(data_dir=tmp_path / "fictional")


@pytest.fixture
def mine(tmp_path):
    return config.deployment(mine=True, data_dir=tmp_path / "data")


def _people(deployment) -> dict:
    connection = db.connect(deployment.db_path)
    try:
        return {user.name: user for user in db.list_users(connection)}
    finally:
        connection.close()


def _upload(client, kind="プロフィール"):
    return client.post(f"/api/photos?kind={kind}", content=jpeg_bytes()).json()["id"]


def _photo_count(deployment) -> int:
    connection = db.connect(deployment.db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        connection.close()


def _make_old_form(path) -> None:
    """プロフィール写真の枠がないころのデータベースにする。"""
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE users DROP COLUMN profile_photo_id")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()


def _columns(path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute("PRAGMA table_info(users)")]
    finally:
        connection.close()


# ---- 見える範囲 ----


def test_プロフィール写真は架空用の起動でもログインの前に読めず_アイコンだけが読める(fictional, face_folder):
    app = create_app(deployment=fictional)
    demo.ensure_demo_faces(fictional)
    person = _people(fictional)["誠一さん"]
    guest = TestClient(app)

    assert guest.get(f"/api/photos/{person.photo_id}").status_code == 200
    assert guest.get(f"/api/photos/{person.profile_photo_id}").status_code == 401


def test_入口の一覧にプロフィール写真の番号は出ない(fictional, face_folder):
    app = create_app(deployment=fictional)
    demo.ensure_demo_faces(fictional)
    person = _people(fictional)["誠一さん"]

    body = TestClient(app).get("/api/users")

    assert all("profile_photo_id" not in item for item in body.json()["items"])
    assert str(person.profile_photo_id) not in body.text


def test_ログインのあとはアイコンと同じ範囲でプロフィール写真が見える(fictional, face_folder):
    app = create_app(deployment=fictional)
    demo.ensure_demo_faces(fictional)
    people = _people(fictional)
    setsuko = TestClient(app, headers={"X-NT-User": str(people["節子さん"].id)})

    assert setsuko.get(f"/api/photos/{people['誠一さん'].profile_photo_id}").status_code == 200
    assert setsuko.get("/api/me").json()["profile_photo_id"] == people["節子さん"].profile_photo_id
    choices = setsuko.get("/api/people/choices").json()["items"]
    assert next(item for item in choices if item["name"] == "誠一さん")["profile_photo_id"] == people["誠一さん"].profile_photo_id


def test_はずした人のプロフィール写真は番号も中身も返らない(login, users):
    miho = login("美穂さん")
    icon, profile = _upload(miho), _upload(miho)
    miho.put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": icon, "profile_photo_id": profile})
    setsuko = login("節子さん")
    room = setsuko.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()
    miho.post(f"/api/rooms/{room['id']}/messages", json={"text": "こんにちは。", "client_key": "m"})
    assert setsuko.get(f"/api/photos/{profile}").status_code == 200

    login("誠一さん").post(f"/api/people/{users['美穂さん'].id}/remove")

    assert setsuko.get(f"/api/photos/{profile}").status_code == 404
    assert setsuko.get(f"/api/photos/{icon}").status_code == 404
    # はずした人は過去の発言の話し手として部屋の応答に残るので、そこにも番号が出ないことを確かめる
    people = setsuko.get(f"/api/rooms/{room['id']}/messages").json()["people"]
    removed = next(item for item in people if item["name"] == "美穂さん")
    assert removed["photo_id"] is None
    assert removed["profile_photo_id"] is None
    assert users["美穂さん"].id not in [item["id"] for item in setsuko.get("/api/people/choices").json()["items"]]


def test_プロフィール写真はAIに送られず_番号も渡らない(login, users, use_claude):
    me = login("誠一さん")
    fake = use_claude()
    profile = _upload(me)
    me.put(f"/api/people/{users['誠一さん'].id}", json={"profile_photo_id": profile})
    room = me.get("/api/rooms").json()["items"][0]

    attached = me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": profile, "client_key": "p"})
    me.post(f"/api/rooms/{room['id']}/messages", json={"text": "こんにちは。", "client_key": "q"})

    assert attached.status_code == 400
    assert fake.requests
    for request in fake.requests:
        content = request.content.decode("utf-8")
        assert '"image"' not in content
        assert str(profile) not in content


# ---- 枠の入れ替え ----


def test_アイコンとプロフィール写真は別々に入れ替えられる(login, users):
    miho = login("美穂さん")
    person = f"/api/people/{users['美穂さん'].id}"
    icon, profile = _upload(miho), _upload(miho)
    miho.put(person, json={"photo_id": icon, "profile_photo_id": profile})

    new_profile = _upload(miho)
    changed = miho.put(person, json={"profile_photo_id": new_profile}).json()

    assert changed["photo_id"] == icon
    assert changed["profile_photo_id"] == new_profile

    new_icon = _upload(miho)
    changed = miho.put(person, json={"photo_id": new_icon}).json()

    assert changed["photo_id"] == new_icon
    assert changed["profile_photo_id"] == new_profile


def test_本人は人を登録するときにプロフィール写真も入れられる(login):
    me = login("誠一さん")
    icon, profile = _upload(me), _upload(me)

    created = me.post("/api/people", json={"name": "光子さん", "role": "家族", "photo_id": icon, "profile_photo_id": profile}).json()

    assert created["photo_id"] == icon
    assert created["profile_photo_id"] == profile


def test_他の人が送った写真はプロフィール写真に指定できない(login, users):
    miho = login("美穂さん")
    others = _upload(miho)

    response = login("節子さん").put(f"/api/people/{users['節子さん'].id}", json={"profile_photo_id": others})

    assert response.status_code == 404


@pytest.mark.parametrize("kind", ["会話", "便り", "部屋"])
def test_プロフィールの種類でない写真はプロフィール写真に指定できない(login, users, kind):
    me = login("誠一さん")
    photo_id = _upload(me, kind=kind)

    assert me.put(f"/api/people/{users['誠一さん'].id}", json={"profile_photo_id": photo_id}).status_code == 404
    assert me.post("/api/people", json={"name": "光子さん", "role": "家族", "profile_photo_id": photo_id}).status_code == 404


def test_ない写真はプロフィール写真に指定できない(login, users):
    assert login("誠一さん").put(f"/api/people/{users['誠一さん'].id}", json={"profile_photo_id": 123456}).status_code == 404


def test_本人以外はほかの人のプロフィール写真を直せない(login, users):
    setsuko = login("節子さん")
    photo_id = _upload(setsuko)

    assert setsuko.put(f"/api/people/{users['美穂さん'].id}", json={"profile_photo_id": photo_id}).status_code == 404


def test_同じ写真の行をアイコンとプロフィール写真の両方には指せない(login, users):
    me = login("誠一さん")
    person = f"/api/people/{users['誠一さん'].id}"
    photo_id = _upload(me)

    assert me.put(person, json={"photo_id": photo_id, "profile_photo_id": photo_id}).status_code == 400

    assert me.put(person, json={"photo_id": photo_id}).status_code == 200
    assert me.put(person, json={"profile_photo_id": photo_id}).status_code == 400

    other = _upload(me)
    assert me.put(person, json={"profile_photo_id": other}).status_code == 200
    assert me.put(person, json={"photo_id": other}).status_code == 400
    assert me.put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": other}).status_code == 400


# ---- デモの顔を入れる ----


def test_顔が決まっていて登録されている人の両方の枠に_別の行で入る(fictional, face_folder):
    create_app(deployment=fictional)

    assert demo.ensure_demo_faces(fictional) == SEEDED

    people = _people(fictional)
    connection = db.connect(fictional.db_path)
    for name in SEEDED:
        person = people[name]
        assert person.photo_id and person.profile_photo_id
        assert person.photo_id != person.profile_photo_id
        rows = connection.execute(
            "SELECT id, file, kind, owner_user_id FROM photos WHERE id IN (?, ?)", (person.photo_id, person.profile_photo_id)
        ).fetchall()
        assert {row["kind"] for row in rows} == {PhotoKind.PROFILE.value}
        assert {row["owner_user_id"] for row in rows} == {person.id}
        assert len({row["file"] for row in rows}) == 2
        for row in rows:
            assert (fictional.photos_dir / row["file"]).is_file()
    connection.close()
    assert people["和子さん"].photo_id is None
    assert people["和子さん"].profile_photo_id is None
    assert not {"健太くん", "井ノ口先生", "田中さん"} & people.keys()


def test_起動し直しても写真は増えず_入れ替えた写真を上書きしない(fictional, face_folder):
    app = create_app(deployment=fictional)
    demo.ensure_demo_faces(fictional)
    person = _people(fictional)["美穂さん"]
    miho = TestClient(app, headers={"X-NT-User": str(person.id)})
    replaced = _upload(miho)
    miho.put(f"/api/people/{person.id}", json={"profile_photo_id": replaced})
    count = _photo_count(fictional)

    assert demo.ensure_demo_faces(fictional) == []

    after = _people(fictional)["美穂さん"]
    assert _photo_count(fictional) == count
    assert after.profile_photo_id == replaced
    assert after.photo_id == person.photo_id


def test_空いた枠だけに入れる(fictional, face_folder):
    app = create_app(deployment=fictional)
    person = _people(fictional)["美穂さん"]
    miho = TestClient(app, headers={"X-NT-User": str(person.id)})
    own_icon = _upload(miho)
    miho.put(f"/api/people/{person.id}", json={"photo_id": own_icon})

    demo.ensure_demo_faces(fictional)

    after = _people(fictional)["美穂さん"]
    assert after.photo_id == own_icon
    assert after.profile_photo_id is not None


def test_はずした人には入れない(fictional, face_folder):
    app = create_app(deployment=fictional)
    people = _people(fictional)
    me = TestClient(app, headers={"X-NT-User": str(people["誠一さん"].id)})
    me.post(f"/api/people/{people['美穂さん'].id}/remove")

    assert "美穂さん" not in demo.ensure_demo_faces(fictional)

    connection = db.connect(fictional.db_path)
    row = connection.execute("SELECT photo_id, profile_photo_id FROM users WHERE id = ?", (people["美穂さん"].id,)).fetchone()
    connection.close()
    assert row["photo_id"] is None and row["profile_photo_id"] is None


def test_写真のフォルダがなくても起動でき_頭文字のまま動く(fictional):
    app = create_app(deployment=fictional)

    assert demo.ensure_demo_faces(fictional) == []

    assert _photo_count(fictional) == 0
    items = TestClient(app).get("/api/users").json()["items"]
    assert all(item["photo_id"] is None for item in items)
    person = _people(fictional)["誠一さん"]
    assert TestClient(app, headers={"X-NT-User": str(person.id)}).get("/api/me").json()["profile_photo_id"] is None


def test_壊れた写真があっても起動は止まらず_ほかの人には入る(fictional, face_folder):
    (face_folder / faces.FACE_FILES["美穂さん"]).write_bytes(b"not an image")
    create_app(deployment=fictional)

    assert demo.ensure_demo_faces(fictional) == ["誠一さん", "正雄さん", "節子さん"]

    assert _people(fictional)["美穂さん"].photo_id is None


def test_自分用の起動には同じ名前の人がいても架空の顔が入らない(mine, face_folder):
    app = create_app(deployment=mine)
    client = TestClient(app)
    created = client.post("/api/self", json={"name": "誠一さん"}).json()
    client.headers["X-NT-User"] = str(created["id"])

    assert demo.ensure_demo_faces(mine) == []
    assert faces.fill_everyone(mine) == []
    registered = client.post("/api/people", json={"name": "美穂さん", "role": "家族"}).json()
    invited = client.post("/api/people/professional", json={"name": "田中さん"}).json()

    assert registered["photo_id"] is None and registered["profile_photo_id"] is None
    assert invited["photo_id"] is None and invited["profile_photo_id"] is None
    assert _photo_count(mine) == 0
    assert not mine.photos_dir.exists()


def test_自分用の起動の一覧と写真の道は_ログインの前にプロフィール写真を出さない(mine):
    app = create_app(deployment=mine)
    client = TestClient(app)
    created = client.post("/api/self", json={"name": "山田一郎"}).json()
    client.headers["X-NT-User"] = str(created["id"])
    profile = _upload(client)
    client.put(f"/api/people/{created['id']}", json={"profile_photo_id": profile})
    guest = TestClient(app)

    assert all("profile_photo_id" not in item for item in guest.get("/api/users").json()["items"])
    assert guest.get(f"/api/photos/{profile}").status_code == 401


# ---- あとから登録される人 ----


def test_田中さんは招いたときに写真が付き_託す設定も入る(fictional, face_folder):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})

    invited = me.post("/api/people/professional", json={"name": "田中さん", "relation": "ケアマネジャー"}).json()

    assert invited["photo_id"] and invited["profile_photo_id"]
    assert invited["photo_id"] != invited["profile_photo_id"]
    connection = db.connect(fictional.db_path)
    delegations = connection.execute("SELECT COUNT(*) FROM delegations WHERE delegate_user_id = ?", (invited["id"],)).fetchone()[0]
    connection.close()
    assert delegations == 1
    tanaka = TestClient(app, headers={"X-NT-User": str(invited["id"])})
    assert tanaka.get(f"/api/photos/{invited['profile_photo_id']}").status_code == 200


def test_写真のフォルダがなくても田中さんを招ける(fictional):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})

    response = me.post("/api/people/professional", json={"name": "田中さん"})

    assert response.status_code == 201
    assert response.json()["photo_id"] is None


@pytest.mark.parametrize("name", ["健太くん", "井ノ口先生"])
def test_まだいない人は登録したときに写真が付く(fictional, face_folder, name):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})

    created = me.post("/api/people", json={"name": name, "role": "家族"}).json()

    assert created["photo_id"] and created["profile_photo_id"]
    assert created["photo_id"] != created["profile_photo_id"]


def test_呼ばれ方が同じ人にも写真が付く(fictional, face_folder):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})

    created = me.post("/api/people", json={"name": "井ノ口 正", "called_name": "井ノ口先生", "role": "専門職"}).json()

    assert created["profile_photo_id"]


def test_顔が決まっていない人には付かない(fictional, face_folder):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})

    created = me.post("/api/people", json={"name": "光子さん", "role": "家族"}).json()

    assert created["photo_id"] is None and created["profile_photo_id"] is None


def test_登録のときに写真を指定していれば_その枠は顔で上書きしない(fictional, face_folder):
    app = create_app(deployment=fictional)
    me = TestClient(app, headers={"X-NT-User": str(_people(fictional)["誠一さん"].id)})
    own = _upload(me)

    created = me.post("/api/people", json={"name": "健太くん", "role": "家族", "profile_photo_id": own}).json()

    assert created["profile_photo_id"] == own
    assert created["photo_id"] not in (None, own)


def test_話に出た候補から登録したときも写真が付く(fictional, face_folder):
    app = create_app(deployment=fictional)
    me_id = _people(fictional)["誠一さん"].id
    me = TestClient(app, headers={"X-NT-User": str(me_id)})
    connection = db.connect(fictional.db_path)
    with connection:
        conversation = db.insert_row(connection, "conversations", {"conversation_date": "2026-11-04"})
        utterance = db.insert_row(
            connection,
            "utterances",
            {"conversation_id": conversation, "speaker_user_id": me_id, "text": "健太くんが来ました。", "spoken_at": "2026-11-04T10:00:00", "place": "日記と会話"},
        )
        cursor = connection.execute(
            "INSERT INTO person_candidates (name, relative_kind, gender, evidence_utterance_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("健太くん", "その他", "男性", utterance, "候補", "2026-11-04T10:00:00"),
        )
    candidate = cursor.lastrowid
    connection.close()

    response = me.post(f"/api/people/candidates/{candidate}/decide", json={"decision": "登録する", "role": "家族"})

    assert response.status_code == 200
    assert response.json()["person"]["profile_photo_id"]


# ---- 表の変更 ----


def test_表の変更の前に控えが取られ_記録を失わない(tmp_path):
    path = tmp_path / "old.db"
    db.init_db(path)
    connection = db.connect(path)
    with connection:
        connection.execute("UPDATE users SET memo = '残す記録', photo_id = 123 WHERE id = 1")
    connection.close()
    _make_old_form(path)
    assert "profile_photo_id" not in _columns(path)

    db.init_db(path)

    assert "profile_photo_id" in _columns(path)
    connection = db.connect(path)
    row = connection.execute("SELECT memo, photo_id, profile_photo_id FROM users WHERE id = 1").fetchone()
    assert (row["memo"], row["photo_id"], row["profile_photo_id"]) == ("残す記録", 123, None)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    connection.close()
    backup = tmp_path / "old.db.before_v2"
    assert backup.exists()
    assert "profile_photo_id" not in _columns(backup)
    kept = sqlite3.connect(backup)
    assert kept.execute("SELECT memo FROM users WHERE id = 1").fetchone()[0] == "残す記録"
    assert kept.execute("PRAGMA user_version").fetchone()[0] == 1
    kept.close()


def test_自分用のデータベースも次の起動で控えを取ってから表が変わり_顔は入らない(mine, face_folder):
    client = TestClient(create_app(deployment=mine))
    client.post("/api/self", json={"name": "山田一郎"})
    _make_old_form(mine.db_path)
    assert "profile_photo_id" not in _columns(mine.db_path)

    create_app(deployment=mine)

    assert "profile_photo_id" in _columns(mine.db_path)
    backup = mine.db_path.with_name(f"{mine.db_path.name}.before_v2")
    assert backup.exists()
    assert "profile_photo_id" not in _columns(backup)
    assert _photo_count(mine) == 0
    assert [user.name for user in _people(mine).values()] == ["山田一郎"]


def test_新しいデータベースは最初から枠があり_控えを取らない(tmp_path):
    path = tmp_path / "new.db"

    db.init_db(path)

    assert "profile_photo_id" in _columns(path)
    assert not (tmp_path / "new.db.before_v2").exists()


# ---- 控え ----


def test_枠がないころの控えへ戻しても_次の起動で枠が足され顔が入る(fictional, face_folder, monkeypatch):
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: False)
    create_app(deployment=fictional)
    saved = snapshot.save(fictional)
    _make_old_form(saved.db_path)

    snapshot.restore(fictional, saved.name)
    create_app(deployment=fictional)
    names = demo.ensure_demo_faces(fictional)

    assert "profile_photo_id" in _columns(fictional.db_path)
    assert names == SEEDED


def test_控えを取ると顔の写真も写り_戻すと顔が残る(fictional, face_folder, monkeypatch):
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: False)
    app = create_app(deployment=fictional)
    demo.ensure_demo_faces(fictional)
    person = _people(fictional)["節子さん"]
    saved = snapshot.save(fictional)

    snapshot.restore(fictional, saved.name)

    app = create_app(deployment=fictional)
    client = TestClient(app, headers={"X-NT-User": str(person.id)})
    assert client.get(f"/api/photos/{person.profile_photo_id}").status_code == 200
    assert demo.ensure_demo_faces(fictional) == []
