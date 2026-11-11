"""専門職の職種と、職種ごとのツールのテスト。

役割：ツールは田中さん（ケアマネジャー）だけが使え、美穂さんの「ツインに聞く」は残り、主治医は託されるまで部屋の話だけなことを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：1（判断するのは人）、10（見える範囲は本人が決める）。
"""

from __future__ import annotations

import pytest

from app import ai, ledger, professions
from app.schemas import Role, ShareScope, Theme
from tests.fake_claude import RoleClaude


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def doctor(conn):
    """登録してあるが、託されていない主治医。"""
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name, relation, residence) VALUES (?, ?, ?, ?, ?, ?)",
            (77, "井ノ口先生", Role.PROFESSIONAL.value, "井ノ口先生", professions.DOCTOR, "近く"),
        )
    return 77


def _as(login_client, user_id):
    login_client.headers["X-NT-User"] = str(user_id)
    return login_client


def test_ケアマネジャーのツールは4つで_職種も返る(login):
    me = login("田中さん").get("/api/me").json()

    assert me["profession"] == "ケアマネジャー"
    assert [tool["id"] for tool in me["tools"]] == ["voice", "plan", "letter", "sheet"]
    # 画面に出すのは、たずねる、チェック、手紙の3つ。1枚の紙は API に残し、画面では出さない（static/tools.js）
    assert [tool["name"] for tool in me["tools"]] == ["ツインにたずねる", "ケア資料をチェックする", "書類を作る", "1枚の紙"]


def test_託された家族には職種もツールもなく_ツインに聞くは使える(login):
    me = login("美穂さん").get("/api/me").json()

    assert me["profession"] is None
    assert me["tools"] == []
    assert me["can_use_supporter_screen"] is True


def test_入口の名前に職種が付く(login):
    items = {item["name"]: item for item in login("誠一さん").get("/api/users").json()["items"]}

    assert items["田中さん"]["profession"] == "ケアマネジャー"
    assert items["美穂さん"]["profession"] is None


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/advocacy/sheet", None),
        ("post", "/api/advocacy/sheet", {"again": False}),
        ("get", "/api/advocacy/plans", None),
        ("post", "/api/advocacy/plans", {"client_key": "k1", "text": "週2回のデイサービス"}),
    ],
)
def test_託された家族は紙とケアプラン案を使えない(login, claude, method, path, body):
    client = login("美穂さん")

    response = client.request(method, path, json=body) if body is not None else client.request(method, path)

    assert response.status_code == 404
    assert claude.bodies("原案") == []


def test_託された家族もツインの考えを作れる(conn, login, claude, add_statement):
    add_statement("この家で暮らしたい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    conn.commit()
    client = login("美穂さん")

    turn = client.post("/api/advocacy/ask", json={"question": "家のことは", "client_key": "k1"}).json()["turn"]

    assert client.post(f"/api/advocacy/turns/{turn['id']}/advocate", json={}).status_code == 200


@pytest.mark.parametrize("words", ["1枚の紙を作ってください", "週2回のデイサービスの案を見てください"])
def test_ツールを使えない人の紙と案の言葉は_紙と案を作らない(login, claude, words):
    reply = login("美穂さん").post("/api/advocacy/chat", json={"question": words, "client_key": "k1"})

    assert reply.status_code == 200
    assert reply.json()["kind"] == "問い"
    assert claude.bodies("原案") == []


def test_託されていない主治医は_部屋の話のほかは何も見えずツールもない(login, doctor, add_statement, conn):
    add_statement("この家で暮らしたい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    conn.commit()
    client = _as(login("田中さん"), doctor)

    me = client.get("/api/me").json()

    assert me["profession"] == "主治医"
    assert me["can_use_supporter_screen"] is False
    assert me["tools"] == []
    assert client.get("/api/statements").json()["items"] == []
    assert client.post("/api/advocacy/ask", json={"question": "家のことは", "client_key": "k1"}).status_code == 404


def test_招く画面の職種の選択肢にツールが添えられる(login):
    items = login("田中さん").get("/api/people/professions").json()["items"]

    assert [item["name"] for item in items] == ["ケアマネジャー", "主治医", "歯科医", "ヘルパー", "訪問看護", "包括支援センター", "商店"]
    assert [tool["id"] for tool in items[0]["tools"]] == ["voice", "plan", "letter", "sheet"]
    # 主治医と、2026-11-08 に足した5つの職種は、ツールを持たない（託されたらケア会議だけ）
    assert all(item["tools"] == [] for item in items[1:])


@pytest.mark.parametrize("job", ["歯科医", "ヘルパー", "訪問看護", "包括支援センター", "商店"])
def test_足した職種で招くと_その職種になり_ツールはない(login, job):
    person = login("誠一さん").post("/api/people/professional", json={"name": f"{job}の人", "relation": job})

    assert person.status_code == 201
    assert person.json()["profession"] == job
    me = _as(login("田中さん"), person.json()["id"]).get("/api/me").json()
    assert me["profession"] == job
    assert me["tools"] == []


def test_職種を選んで招くと_その職種になる(login):
    me = login("誠一さん")

    person = me.post("/api/people/professional", json={"name": "川口先生", "relation": "主治医"})

    assert person.status_code == 201
    assert person.json()["profession"] == "主治医"


def test_登録してある専門職を招くと_新しく作らず託す(login, doctor, conn):
    me = login("誠一さん")
    before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    person = me.post("/api/people/professional", json={"name": "井ノ口先生", "relation": "主治医"})

    assert person.status_code == 201
    assert person.json()["id"] == doctor
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before
    assert _as(login("田中さん"), doctor).get("/api/me").json()["can_use_supporter_screen"] is True


def test_託された主治医は読めるが_ツールは使えない(login, doctor, conn):
    with conn:
        conn.execute(
            "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (?, ?, '2036-01-01')",
            (doctor, ShareScope.DECISION_SUPPORT.value),
        )
    client = _as(login("田中さん"), doctor)

    assert client.get("/api/advocacy/overview").status_code == 200
    assert client.get("/api/me").json()["tools"] == []
    assert client.get("/api/advocacy/sheet").status_code == 404


def test_職種を選ばずに招いた専門職は_これまでどおりツールを使える(conn, login):
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name) VALUES (78, '山田さん', ?, '山田さん')",
            (Role.PROFESSIONAL.value,),
        )
        conn.execute(
            "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (78, ?, '2036-01-01')",
            (ShareScope.DECISION_SUPPORT.value,),
        )
    me = _as(login("田中さん"), 78).get("/api/me").json()

    assert me["profession"] is None
    assert [tool["id"] for tool in me["tools"]] == ["voice", "plan", "letter", "sheet"]
