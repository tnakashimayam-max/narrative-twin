"""人の登録と管理のテスト。

役割：登録、立場の変更、はずすことを本人だけができること、はずした人の扱いを確かめる。
4つの極：2. 分かち合う。
守る原則：10（託す人と見せる範囲は本人が決める）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, ledger
from app.main import create_app
from app.schemas import LetterDraft, ShareScope


@pytest.fixture
def me(login):
    return login("誠一さん")


FULL = {
    "name": "和子さん",
    "called_name": "和子",
    "role": "家族",
    "relation": "妻",
    "residence": "同居",
    "birth_year": 1948,
    "gender": "女性",
    "is_deceased": False,
    "memo": "紫陽花の世話を一緒にしている。",
}


def _names(client) -> list[str]:
    return [item["name"] for item in client.get("/api/users").json()["items"]]


def test_本人が家族と知人を登録できる(me):
    response = me.post("/api/people", json=FULL)

    assert response.status_code == 201
    body = response.json()
    assert body["called_name"] == "和子"
    assert body["relation"] == "妻"
    assert body["residence"] == "同居"
    assert body["memo"] == "紫陽花の世話を一緒にしている。"
    assert "和子さん" in _names(me)


def test_新しい人の番号は乱数(me):
    assert me.post("/api/people", json=FULL).json()["id"] > 1000


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は人を登録できない(login, name):
    assert login(name).post("/api/people", json=FULL).status_code == 404


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は一覧と属性を開けない(login, name):
    assert login(name).get("/api/people").status_code == 404


def test_本人を2人にはできない(me):
    assert me.post("/api/people", json={**FULL, "role": "本人"}).status_code == 400


def test_同じ名前は登録できない(me):
    me.post("/api/people", json=FULL)

    assert me.post("/api/people", json=FULL).status_code == 409


def test_呼ばれ方を省くと名前を使う(me):
    assert me.post("/api/people", json={"name": "和子さん", "role": "家族"}).json()["called_name"] == "和子さん"


def test_各人は自分の名前と呼ばれ方と写真を直せる(login, users):
    miho = login("美穂さん")

    response = miho.put(f"/api/people/{users['美穂さん'].id}", json={"called_name": "みほ"})

    assert response.status_code == 200
    assert response.json()["called_name"] == "みほ"


def test_本人以外の応答に属性を入れない(login, users):
    miho = login("美穂さん")

    body = miho.put(f"/api/people/{users['美穂さん'].id}", json={"called_name": "みほ"}).json()

    assert "memo" not in body
    assert "birth_year" not in body


@pytest.mark.parametrize(
    "fields",
    [{"role": "専門職"}, {"memo": "書き換え"}, {"is_deceased": True}, {"relation": "姉"}],
    ids=["立場", "メモ", "亡くなった", "続柄"],
)
def test_本人以外は自分の立場や属性を直せない(login, users, fields):
    assert login("美穂さん").put(f"/api/people/{users['美穂さん'].id}", json=fields).status_code == 404


def test_本人以外はほかの人を直せない(login, users):
    assert login("美穂さん").put(f"/api/people/{users['節子さん'].id}", json={"called_name": "せつ"}).status_code == 404


def test_本人は立場を変えられる(me, users):
    body = me.put(f"/api/people/{users['節子さん'].id}", json={"role": "家族"}).json()

    assert body["role"] == "家族"


def test_本人の立場は変えられない(me, users):
    assert me.put(f"/api/people/{users['誠一さん'].id}", json={"role": "家族"}).status_code == 400


def test_立場を変えると見える便りが変わる(conn, me, login, users):
    from datetime import datetime

    ledger.add_letter(
        conn, LetterDraft(body="家族への便り", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 2))
    )
    setsuko = login("節子さん")
    assert setsuko.get("/api/letters").json()["items"] == []

    me.put(f"/api/people/{users['節子さん'].id}", json={"role": "家族"})

    assert [item["body"] for item in setsuko.get("/api/letters").json()["items"]] == ["家族への便り"]


def test_立場を変える前に見えるようになるものを言葉で出す(me, users):
    preview = me.get(f"/api/people/{users['節子さん'].id}/role-preview", params={"role": "家族"}).json()

    assert preview["sees"] == ["「家族」向けの便りとその写真"]
    assert preview["stops"] == []


def test_立場を変える前に見えなくなるものも言葉で出す(me, users):
    preview = me.get(f"/api/people/{users['美穂さん'].id}/role-preview", params={"role": "専門職"}).json()

    assert "「家族」向けの便りとその写真" in preview["stops"]


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は立場の変更の説明も開けない(login, users, name):
    response = login(name).get(f"/api/people/{users['節子さん'].id}/role-preview", params={"role": "家族"})

    assert response.status_code == 404


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は人をはずせない(login, users, name):
    assert login(name).post(f"/api/people/{users['節子さん'].id}/remove").status_code == 404


def test_本人ははずせない(me, users):
    assert me.post(f"/api/people/{users['誠一さん'].id}/remove").status_code == 400


def test_はずした人はログインの一覧から消え_ログインもできない(me, login, users):
    setsuko = login("節子さん")

    assert me.post(f"/api/people/{users['節子さん'].id}/remove").status_code == 204

    assert "節子さん" not in _names(me)
    assert me.post("/api/login", json={"user_id": users["節子さん"].id}).status_code == 404
    # 別のウィンドウで開いたままの人も、見えなくなる
    assert setsuko.get("/api/me").status_code == 401
    assert setsuko.get("/api/letters").status_code == 401


def test_はずしても過去の発言は残る(conn, me, login, users):
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["節子さん"].id]}).json()
    login("節子さん").post(f"/api/rooms/{room['id']}/messages", json={"text": "お茶をどうぞ。", "client_key": "k"})

    me.post(f"/api/people/{users['節子さん'].id}/remove")

    view = me.get(f"/api/rooms/{room['id']}/messages").json()
    assert [item["text"] for item in view["items"]] == ["お茶をどうぞ。"]
    assert "節子さん" in [person["name"] for person in view["people"]]
    assert conn.execute("SELECT removed_at FROM users WHERE name = '節子さん'").fetchone()[0]


def test_亡くなった方はログインの一覧に出ない(me):
    me.post("/api/people", json={**FULL, "is_deceased": True})

    assert "和子さん" not in _names(me)
    assert "和子さん" in [item["name"] for item in me.get("/api/people").json()["items"]]


# ---- 自分用の最初の起動 ----


@pytest.fixture
def mine(tmp_path):
    return TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))


def test_自分用の最初の起動では本人の登録が要る(mine):
    assert mine.get("/api/mode").json()["needs_self"] is True
    assert mine.get("/api/users").json()["items"] == []


def test_本人を登録するとログインできる(mine):
    response = mine.post(
        "/api/self", json={"name": "山田一郎", "called_name": "一郎さん", "birth_year": 1950, "gender": "男性"}
    )

    assert response.status_code == 201
    assert response.json()["role"] == "本人"
    assert mine.get("/api/mode").json()["needs_self"] is False
    assert [item["name"] for item in mine.get("/api/users").json()["items"]] == ["山田一郎"]


def test_本人の登録は1回だけ(mine):
    mine.post("/api/self", json={"name": "山田一郎"})

    assert mine.post("/api/self", json={"name": "別の人"}).status_code == 409


def test_架空用では本人を登録し直せない(login, me):
    assert me.post("/api/self", json={"name": "別の人"}).status_code == 409
