"""新規のデータのテスト。

役割：入口の「新規で始める」で作るデータがデモと混ざらないことと、自分用の起動が変わらないことを確かめる。
4つの極：特定の極に属さない土台。
守る原則：8（デモの架空データに混ぜない）、10（見える範囲の決まりは新規でも同じ）。
"""

from __future__ import annotations

import io
import json
import threading
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import clock, config, db, jobs, rooms
from app.main import create_app


@pytest.fixture
def demo(tmp_path):
    return TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))


def _start(demo) -> str:
    response = demo.post("/api/fresh")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _open(demo, fresh_id) -> TestClient:
    client = TestClient(demo.app)
    client.headers["X-NT-Data"] = fresh_id
    return client


def _register(client, name="山田一郎") -> TestClient:
    response = client.post("/api/self", json={"name": name})
    assert response.status_code == 201, response.text
    client.headers["X-NT-User"] = str(response.json()["id"])
    return client


def _fresh_in_use(demo, name="山田一郎") -> tuple[str, TestClient]:
    fresh_id = _start(demo)
    return fresh_id, _register(_open(demo, fresh_id), name)


def _twin_room(client) -> int:
    return next(room["id"] for room in client.get("/api/rooms").json()["items"] if room["kind"] == "ツイン")


def _say_to_twin(client, text):
    return client.post(f"/api/rooms/{_twin_room(client)}/messages", json={"text": text, "client_key": text[:60]})


def _utterance_count(path) -> int:
    conn = db.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    finally:
        conn.close()


def _files_under(folder) -> set:
    return {path for path in folder.rglob("*") if path.is_file()}


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(output, format="JPEG")
    return output.getvalue()


def test_新規で始めると_本人のいないデータが開く(demo):
    client = _open(demo, _start(demo))

    assert client.get("/api/mode").json() == {
        "mine": False,
        "kind": "fresh",
        "label": "新規のデータ",
        "needs_self": True,
        "ai_ready": False,
    }
    assert client.get("/api/users").json()["items"] == []


def test_本人を登録するまでは_同じ新規を返し_空のフォルダを増やさない(demo, tmp_path):
    first = _start(demo)

    assert _start(demo) == first
    assert [path.name for path in (tmp_path / "fresh").iterdir()] == [first]


def test_本人を登録すると_次は別の新規を作り_前の新規は消えない(demo):
    first, _ = _fresh_in_use(demo, "山田一郎")
    second, _ = _fresh_in_use(demo, "鈴木花子")

    assert second != first
    items = demo.get("/api/fresh").json()["items"]
    assert [(item["id"], item["name"]) for item in items] == [(second, "鈴木花子"), (first, "山田一郎")]
    assert datetime.fromisoformat(items[0]["created_at"]).tzinfo is not None


def test_本人のいない新規は一覧に出ない(demo):
    _start(demo)

    assert demo.get("/api/fresh").json() == {"items": []}


def test_新規で話しても_デモの発言は増えない(demo, tmp_path):
    fresh_id, client = _fresh_in_use(demo)
    before = _utterance_count(tmp_path / "narrative_twin.db")

    _say_to_twin(client, "今日は庭の草を抜いた。")

    assert _utterance_count(tmp_path / "narrative_twin.db") == before
    assert _utterance_count(tmp_path / "fresh" / fresh_id / "narrative_twin.db") == 1


def test_デモの入口は新規を作っても変わらない(demo):
    before = demo.get("/api/users").json()

    _fresh_in_use(demo)

    assert demo.get("/api/users").json() == before
    assert demo.get("/api/mode").json()["kind"] == "demo"
    assert demo.get("/api/mode").json()["needs_self"] is False


def test_ヘッダのない要求はデモのデータに届く(demo):
    _fresh_in_use(demo)

    assert "誠一さん" in [item["name"] for item in demo.get("/api/users").json()["items"]]


@pytest.mark.parametrize("value", ["..", "../narrative_twin", "../../mine", "C:\\x", "20261105-171500-123456", ""])
def test_形の違う番号とない番号は404になる(demo, value):
    response = _open(demo, value).get("/api/mode")

    assert response.status_code == 404
    assert response.json() == {"detail": "そのデータは見つかりません。"}


def test_形の違うフォルダは新規に数えない(demo, tmp_path):
    (tmp_path / "fresh" / "別のもの").mkdir(parents=True)

    assert demo.get("/api/fresh").json() == {"items": []}
    assert _start(demo) != "別のもの"


def test_新規の道はヘッダが付いていてもデモのアプリが受ける(demo):
    fresh_id, client = _fresh_in_use(demo)

    assert [item["id"] for item in client.get("/api/fresh").json()["items"]] == [fresh_id]


def test_ログインの番号はデータをまたがない(demo):
    _, client = _fresh_in_use(demo)
    fresh_user = client.headers["X-NT-User"]
    seiichi = next(item["id"] for item in demo.get("/api/users").json()["items"] if item["name"] == "誠一さん")

    assert demo.get("/api/me", headers={"X-NT-User": fresh_user}).status_code == 401
    assert client.get("/api/me", headers={"X-NT-User": str(seiichi)}).status_code == 401


def test_作り直したサーバーでも前の新規が開ける(demo, tmp_path):
    fresh_id, _ = _fresh_in_use(demo, "山田一郎")

    again = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))

    assert [item["name"] for item in again.get("/api/fresh").json()["items"]] == ["山田一郎"]
    assert [item["name"] for item in _open(again, fresh_id).get("/api/users").json()["items"]] == ["山田一郎"]


def test_保存先は新規のフォルダとそのログだけ(demo, tmp_path):
    fresh_id = _start(demo)
    client = _open(demo, fresh_id)
    before = _files_under(tmp_path)
    folder = tmp_path / "fresh" / fresh_id

    _register(client)
    _say_to_twin(client, "今日は庭の草を抜いた。")
    client.post("/api/photos?kind=プロフィール", content=_jpeg())

    made = _files_under(tmp_path) - before
    assert made
    assert all(folder in path.parents or path == tmp_path / "logs" / "fresh.jsonl" for path in made)


def test_写真は新規のフォルダに入り_別のデータからは読めない(demo, tmp_path):
    fresh_id, client = _fresh_in_use(demo)

    photo_id = client.post("/api/photos?kind=プロフィール", content=_jpeg()).json()["id"]

    assert client.get(f"/api/photos/{photo_id}").status_code == 200
    assert any((tmp_path / "fresh" / fresh_id / "photos").iterdir())
    seiichi = next(item["id"] for item in demo.get("/api/users").json()["items"] if item["name"] == "誠一さん")
    assert demo.get(f"/api/photos/{photo_id}", headers={"X-NT-User": str(seiichi)}).status_code == 404


def test_質問セットは新規の中に書き_Gitに入っているファイルに触れない(demo, tmp_path):
    fresh_id = _start(demo)
    before = config.EVAL_CASES_PATH.read_bytes()

    response = _open(demo, fresh_id).post("/api/eval/cases", json={"question": "試し", "question_type": "今の考え"})

    assert response.status_code == 201, response.text
    assert config.EVAL_CASES_PATH.read_bytes() == before
    assert (tmp_path / "fresh" / fresh_id / "eval_cases.json").exists()


def test_新規の要求はデモのログを通らず_ログに本文を書かない(demo, tmp_path):
    fresh_id = _start(demo)
    client = _open(demo, fresh_id)
    fictional = tmp_path / "logs" / "fictional.jsonl"
    lines = fictional.read_text(encoding="utf-8") if fictional.exists() else ""

    _register(client, "ログに出ない名前")
    _say_to_twin(client, "ログに出ない話。")

    assert (fictional.read_text(encoding="utf-8") if fictional.exists() else "") == lines
    written = (tmp_path / "logs" / "fresh.jsonl").read_text(encoding="utf-8")
    assert written
    assert "ログに出ない" not in written
    assert all("text" not in json.loads(line) for line in written.splitlines())


def test_新規の日付は本物の今日のままで_動かす道もない(demo):
    fresh_id, client = _fresh_in_use(demo)

    assert demo.post("/api/dev/date", json={"date": "2036-05-06"}).status_code == 200

    assert clock.now(demo.app.state.fresh_apps.app_of(fresh_id)).date() == date.today()
    assert client.post("/api/dev/date", json={"date": "2036-05-06"}).status_code == 404


def test_新規では初めから専門職を招ける(demo):
    _, client = _fresh_in_use(demo)

    assert client.get("/api/me").json()["can_invite_professional"] is True


def test_キーがないと話せない理由を返し_言葉は残る(demo):
    _, client = _fresh_in_use(demo)

    assert client.get("/api/mode").json()["ai_ready"] is False
    response = _say_to_twin(client, "今日は庭の草を抜いた。")

    assert response.status_code == 503
    assert response.json() == {"detail": "ツインと話すには API キーが要ります。"}
    room = _twin_room(client)
    assert "今日は庭の草を抜いた。" in [item["text"] for item in client.get(f"/api/rooms/{room}/messages").json()["items"]]


def test_キーがなくても登録とホームまでは使える(demo):
    _, client = _fresh_in_use(demo)

    assert client.get("/api/me").status_code == 200
    assert client.get("/api/twin/appearance").status_code == 200
    assert client.get("/api/rooms").status_code == 200


def test_キーがあれば案内を出さない(demo, monkeypatch):
    monkeypatch.setenv(config.API_KEY_NAME, "試しの値")

    assert _open(demo, _start(demo)).get("/api/mode").json()["ai_ready"] is True


def test_止まった処理は_起動で決めたときだけ_最初に開いたときに1回流す(demo, tmp_path, monkeypatch):
    called = []
    done = threading.Event()

    def record(app):
        called.append(app.state.db_path)
        done.set()

    monkeypatch.setattr(jobs, "catch_up", record)
    fresh_id = _start(demo)
    _open(demo, fresh_id).get("/api/mode")
    assert called == []

    again = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))
    again.app.state.fresh_apps.catch_up_on_open = True
    _open(again, fresh_id).get("/api/mode")
    assert done.wait(5)
    _open(again, fresh_id).get("/api/mode")

    assert called == [tmp_path / "fresh" / fresh_id / "narrative_twin.db"]


def test_自分用の起動には新規の道も振り分けもない(tmp_path):
    mine = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))

    assert mine.get("/api/fresh").status_code == 404
    assert mine.post("/api/fresh").status_code in {404, 405}
    assert mine.get("/api/mode", headers={"X-NT-Data": "20261105-171500-123456"}).json()["kind"] == "mine"
    assert not hasattr(mine.app.state, "fresh_apps")
    assert not (tmp_path / "fresh").exists()
    assert not (tmp_path / "mine" / "fresh").exists()


def test_自分用の起動はキーがなくても今までの文のまま(tmp_path):
    mine = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))
    _register(mine)

    response = _say_to_twin(mine, "今日は庭の草を抜いた。")

    assert response.status_code == 503
    assert response.json() == {"detail": rooms.AI_FAILED_MESSAGE}


def test_ログの書き出しは新規全体で1つにする(demo):
    first, _ = _fresh_in_use(demo, "山田一郎")
    second, _ = _fresh_in_use(demo, "鈴木花子")
    apps = demo.app.state.fresh_apps

    assert apps.app_of(first).state.devlog is apps.app_of(second).state.devlog


def test_読めない新規は一覧から外し_開くと404になり_新規で始めるにも使わない(demo, tmp_path):
    kept, _ = _fresh_in_use(demo, "山田一郎")
    broken = "20261105-171500-123456"
    (tmp_path / "fresh" / broken).mkdir()
    (tmp_path / "fresh" / broken / "narrative_twin.db").write_bytes(b"not a database, broken file")

    assert [item["id"] for item in demo.get("/api/fresh").json()["items"]] == [kept]
    assert _open(demo, broken).get("/api/mode").status_code == 404
    assert _start(demo) not in {kept, broken}


def test_新規のアプリには振り分けが付かず_入れ子にならない(demo):
    fresh_id = _start(demo)

    assert not hasattr(demo.app.state.fresh_apps.app_of(fresh_id).state, "fresh_apps")


def test_新規の保存先に自分用の置き場を使わない(tmp_path):
    fresh = config.fresh_deployment(config.deployment(data_dir=tmp_path), "20261105-171500-123456")

    assert "mine" not in {part.lower() for part in fresh.db_path.parts[len(tmp_path.parts) :]}
    assert fresh.log_path.name == "fresh.jsonl"
    assert fresh.kind == config.KIND_FRESH
