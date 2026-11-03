"""簡易ログインと画面のファイルのテスト。

役割：名前を選ぶとその人になること、画面に必要な表示があること、閲覧の記録が残ることを確かめる。
4つの極：特定の極に属さない土台。
守る原則：2（並べる順番）、3（出どころを返す）、8（「架空データ」の表示）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import LetterDraft, ShareScope, Source


@pytest.fixture
def client(db_path, conn):
    return TestClient(create_app(db_path))


def test_ログイン画面には架空の5人と招かれた人が出る(client):
    response = client.get("/api/users")

    assert [user["name"] for user in response.json()["items"]] == [
        "誠一さん",
        "和子さん",
        "美穂さん",
        "正雄さん",
        "節子さん",
        "田中さん",
    ]


@pytest.mark.parametrize(
    ("name", "role"),
    [("誠一さん", "本人"), ("美穂さん", "家族"), ("節子さん", "縁のある人"), ("田中さん", "専門職")],
)
def test_名前を選ぶとその人になる(login, name, role):
    me = login(name).get("/api/me")

    assert me.status_code == 200
    assert me.json()["name"] == name
    assert me.json()["role"] == role


def test_ログインしていなければ_だれでもない(client):
    assert client.get("/api/me").status_code == 401


def test_いない人にはログインできない(client):
    assert client.post("/api/login", json={"user_id": 99999}).status_code == 404


def test_ログインにパスワードはいらない(client, users):
    response = client.post("/api/login", json={"user_id": users["節子さん"].id})

    assert response.status_code == 200


def test_2つのウィンドウで別の人として使える(login):
    first = login("節子さん")
    second = login("田中さん")

    assert first.get("/api/me").json()["name"] == "節子さん"
    assert second.get("/api/me").json()["name"] == "田中さん"
    assert first.get("/api/me").json()["name"] == "節子さん"


def test_ログインはcookieを使わない(client, users):
    response = client.post("/api/login", json={"user_id": users["節子さん"].id})

    assert "set-cookie" not in response.headers
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("value", ["9" * 25, "9" * 5000, "abc", "-1"], ids=["25桁", "5000桁", "文字", "負の数"])
def test_おかしな印は_だれでもない扱いになる(client, value):
    assert client.get("/api/me", headers={"X-NT-User": value}).status_code == 401


def test_大きすぎる番号でログインしても落ちない(client):
    assert client.post("/api/login", json={"user_id": 10**25}).status_code == 422


def test_大きすぎる番号で開いても落ちない(login):
    assert login("節子さん").get(f"/api/statements/{10**25}").status_code == 422


def test_番号の上限を超えると形の違いとして扱う(login):
    client = login("節子さん")

    assert client.get(f"/api/statements/{2**53 - 1}").status_code == 404
    assert client.get(f"/api/statements/{2**53}").status_code == 422


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_自動のAPI説明の画面は閉じている(client, path):
    assert client.get(path).status_code == 404


# ---- 画面のファイル ----


def test_最初の画面にデモと架空データの表示がある(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "架空データ" in response.text
    assert 'lang="ja"' in response.text
    # 入口の札の下に出す。入口は app.js が組み立てる
    assert "デモ用の簡易ログイン" in client.get("/app.js").text


@pytest.mark.parametrize("path", ["/app.js", "/style.css"])
def test_画面の部品が配られる(client, path):
    assert client.get(path).status_code == 200


# ---- 記録の並びと閲覧の記録 ----


def test_原則2の順に並ぶ(conn, users, login, add_utterance, make_draft, add_statement):
    from app import ledger

    said = add_utterance("朝の散歩が好きです。")
    guess = ledger.append_statement(
        conn,
        make_draft(
            [said.id],
            "外の空気を大切にしているのかもしれません",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
        ),
    )
    words = add_statement("朝の散歩が好きです。")
    seen = add_utterance("今日は笑顔でうなずいていた", speaker="田中さん")
    now = ledger.append_statement(
        conn,
        make_draft(
            [seen.id],
            "今日は笑顔でうなずいていた",
            source=Source.OBSERVATION,
            speaker_user_id=users["田中さん"].id,
        ),
    )

    items = login("誠一さん").get("/api/statements").json()["items"]

    assert [item["id"] for item in items] == [now.id, words.id, guess.id]
    assert [item["source"] for item in items] == [
        Source.OBSERVATION.value,
        Source.SELF_WORDS.value,
        Source.AI_GUESS.value,
    ]


def test_証言は本人の言葉のあと_AIの推測の前に並ぶ(conn, users, login, add_utterance, make_draft, add_statement):
    from app import ledger

    said = add_utterance("父は朝の散歩が好きです。", speaker="美穂さん")
    guess = ledger.append_statement(
        conn,
        make_draft(
            [said.id],
            "外の空気を大切にしているのかもしれません",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
        ),
    )
    testimony = ledger.append_statement(
        conn,
        make_draft(
            [said.id],
            "朝の散歩が好きなようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
        ),
    )
    words = add_statement("朝の散歩が好きです。")

    items = login("誠一さん").get("/api/statements").json()["items"]

    assert [item["id"] for item in items] == [words.id, testimony.id, guess.id]


def test_同じ順位の中では有効になった日が新しいものが先(login, add_statement):
    older = add_statement("古い言葉です。", effective_date=date(2020, 1, 1))
    newer = add_statement("新しい言葉です。", effective_date=date(2026, 1, 1))

    items = login("誠一さん").get("/api/statements").json()["items"]

    assert [item["id"] for item in items] == [newer.id, older.id]


def test_便りは新しい順に並ぶ(conn, conversation_id, login):
    from app import ledger

    for body, shared_at in [
        ("古い便り", datetime(2020, 1, 1, 10, 0)),
        ("新しい便り", datetime(2026, 1, 1, 10, 0)),
    ]:
        ledger.add_letter(
            conn,
            LetterDraft(
                conversation_id=conversation_id,
                body=body,
                share_with=ShareScope.FAMILY,
                shared_at=shared_at,
            ),
        )

    items = login("美穂さん").get("/api/letters").json()["items"]

    assert [item["body"] for item in items] == ["新しい便り", "古い便り"]


def test_開いた記録は閲覧の記録に残る(conn, users, login, add_statement):
    target = add_statement("縁のある人までの記録です。", scope=ShareScope.CONNECTED)
    client = login("節子さん")

    client.get("/api/statements")
    client.get(f"/api/statements/{target.id}")

    rows = conn.execute("SELECT viewer_user_id, target FROM access_logs ORDER BY id").fetchall()
    assert [(row["viewer_user_id"], row["target"]) for row in rows] == [
        (users["節子さん"].id, "言明の一覧"),
        (users["節子さん"].id, f"言明 {target.id}"),
    ]


def test_見えなかった行は閲覧の記録に残らない(conn, login, add_statement):
    secret = add_statement("本人だけの記録です。")

    login("節子さん").get(f"/api/statements/{secret.id}")

    assert conn.execute("SELECT COUNT(*) FROM access_logs").fetchone()[0] == 0
