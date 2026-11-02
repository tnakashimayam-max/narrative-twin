"""写真のテスト。

役割：写真を整えて保存すること、見てよい人にだけ返すこと、AI に送る写真を限ることを確かめる。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：8（提出物とデモの写真は架空）、10（見せる範囲はサーバーで確かめる）。

写真はこのファイルの中で Pillow で作る。実在の写真は使わない。
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from PIL import Image

from app import ai, images, photos
from app.schemas import Photo, PhotoKind
from tests.fake_claude import RoleClaude


def jpeg_bytes(size=(400, 300), *, exif=None) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (120, 160, 90)).save(output, format="JPEG", **({"exif": exif} if exif else {}))
    return output.getvalue()


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (200, 100), (10, 20, 30, 0)).save(output, format="PNG")
    return output.getvalue()


def _gps_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "NarrativeTwinTest"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (35.0, 40.0, 12.0)
    return exif


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


def _upload(client, data=None, kind="会話"):
    return client.post(f"/api/photos?kind={kind}", content=jpeg_bytes() if data is None else data)


# ---- 整える ----


def test_大きな写真は長い辺が1568px以下になる():
    result = Image.open(io.BytesIO(images.normalize(jpeg_bytes((4000, 3000)))))

    assert max(result.size) == 1568
    assert result.format == "JPEG"


def test_小さな写真は大きくしない():
    result = Image.open(io.BytesIO(images.normalize(jpeg_bytes((400, 300)))))

    assert result.size == (400, 300)


def test_撮影の位置情報は保存されない():
    source = jpeg_bytes((800, 600), exif=_gps_exif())
    assert dict(Image.open(io.BytesIO(source)).getexif().get_ifd(0x8825))

    result = Image.open(io.BytesIO(images.normalize(source)))

    assert dict(result.getexif()) == {}
    assert dict(result.getexif().get_ifd(0x8825)) == {}


def test_PNGも受け取りJPEGにする():
    assert Image.open(io.BytesIO(images.normalize(png_bytes()))).format == "JPEG"


@pytest.mark.parametrize(
    "data",
    [b"This is not an image." * 20, b"\xff\xd8\xff\xe0broken", b"%PDF-1.7 fake"],
    ids=["文字", "壊れたJPEG", "PDF"],
)
def test_読めない形式は分かりやすい言葉で止める(me, data):
    response = _upload(me, data)

    assert response.status_code == 400
    assert response.json()["detail"] == images.UNREADABLE_MESSAGE


def test_空の写真は分かりやすい言葉で止める(me):
    assert _upload(me, b"").json()["detail"] == images.EMPTY_MESSAGE


def test_大きすぎる写真は分かりやすい言葉で止める(me):
    response = _upload(me, b"x" * (images.MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 400
    assert "10MB" in response.json()["detail"]


def test_保存するファイルの名前は乱数で_長い辺は1568px以下で_位置情報がない(conn, me, db_path):
    photo_id = _upload(me, jpeg_bytes((4000, 3000), exif=_gps_exif())).json()["id"]

    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    saved = db_path.parent / "photos" / row["file"]
    image = Image.open(saved)
    assert len(row["file"]) == 36
    assert max(image.size) == 1568
    assert dict(image.getexif()) == {}


def test_写真の道は中身だけを返す(me):
    photo_id = _upload(me).json()["id"]

    response = me.get(f"/api/photos/{photo_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert not any("kind" in key.lower() or "owner" in key.lower() for key in response.headers)


# ---- 見てよい人 ----


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_ツインとの部屋に送った写真は本人だけが見られる(me, login, use_claude, name):
    use_claude()
    photo_id = _upload(me).json()["id"]
    room = me.get("/api/rooms").json()["items"][0]
    me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"})

    assert me.get(f"/api/photos/{photo_id}").status_code == 200
    assert login(name).get(f"/api/photos/{photo_id}").status_code == 404


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は会話と便りの写真を送れない(login, name):
    assert _upload(login(name), kind="会話").status_code == 404
    assert _upload(login(name), kind="便り").status_code == 404


def test_部屋の写真は部屋の人だけが見られる(me, login, users):
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()
    photo_id = _upload(me, kind="部屋").json()["id"]
    me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"})

    assert login("美穂さん").get(f"/api/photos/{photo_id}").status_code == 200
    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 404


def test_便りの写真は便りの範囲の人だけが見られる(me, login):
    photo_id = _upload(me, kind="便り").json()["id"]
    me.post("/api/letters", json={"body": "便りです。", "photo_id": photo_id, "share_with": "家族"})

    assert login("美穂さん").get(f"/api/photos/{photo_id}").status_code == 200
    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 404
    assert login("田中さん").get(f"/api/photos/{photo_id}").status_code == 404


def test_縁のある人までの便りの写真は節子さんにも見える(me, login):
    photo_id = _upload(me, kind="便り").json()["id"]
    me.post("/api/letters", json={"body": "便りです。", "photo_id": photo_id, "share_with": "縁のある人まで"})

    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 200


def test_プロフィール写真はログイン中の人に見え_はずすと見えなくなる(me, login, users):
    miho = login("美穂さん")
    photo_id = _upload(miho, kind="プロフィール").json()["id"]
    miho.put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": photo_id})
    setsuko = login("節子さん")

    assert setsuko.get(f"/api/photos/{photo_id}").status_code == 200

    me.post(f"/api/people/{users['美穂さん'].id}/remove")

    assert setsuko.get(f"/api/photos/{photo_id}").status_code == 404


def test_見られない写真とない写真は同じ404(login, me):
    photo_id = _upload(me).json()["id"]
    other = login("節子さん")

    hidden = other.get(f"/api/photos/{photo_id}")
    missing = other.get("/api/photos/123456")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_写真のファイルは画面の道からは開けない(conn, me):
    photo_id = _upload(me).json()["id"]
    file_name = conn.execute("SELECT file FROM photos WHERE id = ?", (photo_id,)).fetchone()["file"]

    assert me.get(f"/photos/{file_name}").status_code == 404
    assert me.get(f"/data/photos/{file_name}").status_code == 404


def test_ログインしていなければ写真は見えない(db_path, conn):
    from fastapi.testclient import TestClient

    from app.main import create_app

    assert TestClient(create_app(db_path)).get("/api/photos/1").status_code == 401


# ---- AI に送る写真 ----


def test_ツインとの部屋に送った写真だけをSonnetに送る(conn, me, use_claude):
    from app import config

    fake = use_claude(
        ツインの会話={
            "reply": "縁側の写真ですね。",
            "question": "ここで何をしますか？",
            "question_theme": "住まい・土地",
            "care": "なし",
            "previous_question_result": None,
            "photo_description": "木の縁側と庭が写っています。",
        }
    )
    photo_id = _upload(me).json()["id"]
    room = me.get("/api/rooms").json()["items"][0]
    body = me.post(
        f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"}
    ).json()

    sent = fake.bodies()[0]
    assert sent["model"] == config.MODEL_BY_ROLE["写真の問い"]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["messages"][0]["content"][0]["type"] == "image"
    assert [item["text"] for item in body["replies"]] == ["縁側の写真ですね。", "ここで何をしますか？"]
    row = conn.execute("SELECT description FROM photos WHERE id = ?", (photo_id,)).fetchone()
    assert row["description"] == "木の縁側と庭が写っています。"


@pytest.mark.parametrize("kind", [PhotoKind.PROFILE, PhotoKind.ROOM, PhotoKind.LETTER])
def test_会話の写真のほかはAIに送る関数が受け付けない(kind):
    photo = Photo(id=1, file="a.jpg", kind=kind, created_at=datetime(2026, 11, 2))
    fake = RoleClaude()

    with pytest.raises(ValueError):
        ai.twin_photo_turn(photo, b"x", system="s", text="t", client=fake.client())

    assert fake.requests == []


def test_プロフィール写真はAIに送られない(me, use_claude, users):
    fake = use_claude()
    photo_id = _upload(me, kind="プロフィール").json()["id"]
    me.put(f"/api/people/{users['誠一さん'].id}", json={"photo_id": photo_id})
    room = me.get("/api/rooms").json()["items"][0]

    response = me.post(
        f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"}
    )

    assert response.status_code == 400
    me.post(f"/api/rooms/{room['id']}/messages", json={"text": "こんにちは。", "client_key": "q"})
    assert all('"image"' not in request.content.decode("utf-8") for request in fake.requests)


def test_人の写真を発言に付けることはできない(me, login, users):
    miho = login("美穂さん")
    photo_id = _upload(miho, kind="部屋").json()["id"]
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()

    response = me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"})

    assert response.status_code == 404


def test_保存の関数は撮影の情報を落とした写真だけを書く(tmp_path, conn, users):
    photo = photos.store_photo(
        conn,
        tmp_path,
        jpeg_bytes((800, 600), exif=_gps_exif()),
        owner_user_id=users["誠一さん"].id,
        kind=PhotoKind.TALK,
        moment="2026-11-02T10:00:00",
    )

    assert b"Exif" not in (tmp_path / photo.file).read_bytes()[:64]


# ---- プロフィールに指定できる写真 ----


def test_便りの写真の番号を自分のプロフィールに指しても見えるようにならない(me, login, users):
    photo_id = _upload(me, kind="便り").json()["id"]
    me.post("/api/letters", json={"body": "便りです。", "photo_id": photo_id, "share_with": "家族"})
    miho = login("美穂さん")

    response = miho.put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": photo_id})

    assert response.status_code == 404
    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 404


def test_部屋の写真の番号をプロフィールに指しても部屋の外の人には見えない(me, login, users):
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["美穂さん"].id]}).json()
    photo_id = _upload(me, kind="部屋").json()["id"]
    me.post(f"/api/rooms/{room['id']}/messages", json={"photo_id": photo_id, "client_key": "p"})

    assert login("美穂さん").put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": photo_id}).status_code == 404
    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 404


def test_本人も会話の写真をプロフィールにはできない(me, users):
    photo_id = _upload(me, kind="会話").json()["id"]

    assert me.put(f"/api/people/{users['誠一さん'].id}", json={"photo_id": photo_id}).status_code == 404


def test_ない写真の番号はプロフィールに指せない(me, users):
    assert me.put(f"/api/people/{users['誠一さん'].id}", json={"photo_id": 123456}).status_code == 404


def test_人を登録するときも別の種類の写真は指せない(me):
    photo_id = _upload(me, kind="会話").json()["id"]

    response = me.post("/api/people", json={"name": "和子さん", "role": "家族", "photo_id": photo_id})

    assert response.status_code == 404


def test_本人は家族のプロフィール写真を登録できる(me, login, users):
    photo_id = _upload(me, kind="プロフィール").json()["id"]

    assert me.put(f"/api/people/{users['美穂さん'].id}", json={"photo_id": photo_id}).status_code == 200
    assert login("節子さん").get(f"/api/photos/{photo_id}").status_code == 200


def test_人どうしの部屋に会話の写真は付けられない(me, users):
    room = me.post("/api/rooms", json={"kind": "グループ", "member_ids": [users["美穂さん"].id]}).json()
    photo_id = _upload(me, kind="会話").json()["id"]

    response = me.post(
        f"/api/rooms/{room['id']}/messages", json={"text": "写真です。", "photo_id": photo_id, "client_key": "p"}
    )

    assert response.status_code == 400


# ---- 写真に残る情報 ----


def _with_comment(fmt: str) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (300, 200), (120, 160, 90))
    if fmt == "JPEG":
        image.save(output, format="JPEG", comment=b"secret-comment")
    else:
        from PIL import PngImagePlugin

        info = PngImagePlugin.PngInfo()
        info.add_text("comment", "secret-comment")
        image.save(output, format="PNG", pnginfo=info)
    return output.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_写真のコメント欄は保存されない(fmt):
    source = _with_comment(fmt)
    assert b"secret-comment" in source

    assert b"secret-comment" not in images.normalize(source)


def test_壊れたPNGは読めない写真として止める(me):
    broken = png_bytes()[:40] + b"\x00" * 60

    response = _upload(me, broken)

    assert response.status_code == 400
    assert response.json()["detail"] == images.UNREADABLE_MESSAGE
