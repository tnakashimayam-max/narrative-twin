"""共有範囲の絞り込みのテスト。

役割：見てよい記録だけがサーバーから返ること、見えない記録の存在が漏れないことを確かめる。
4つの極：2. 分かち合う、4. 10年後に代弁する。
守る原則：10（見せる範囲は本人が決める。「本人だけ」は本人以外に件数も見せない）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import consent, db, ledger
from app.main import create_app
from app.schemas import LetterDraft, Role, ShareScope

ALL_SCOPES = list(ShareScope)


# ---- 役割 × 共有範囲の表 ----

# 期待する表はあえてコードから読まず、ここに書き写す（実装と同時に間違えないため）
EXPECTED = {
    (Role.SELF, True): set(ALL_SCOPES),
    (Role.SELF, False): set(ALL_SCOPES),
    (Role.FAMILY, True): {
        ShareScope.FAMILY,
        ShareScope.CONNECTED,
        ShareScope.DECISION_SUPPORT,
    },
    (Role.FAMILY, False): {ShareScope.FAMILY, ShareScope.CONNECTED},
    (Role.CONNECTED, True): {ShareScope.CONNECTED},
    (Role.CONNECTED, False): {ShareScope.CONNECTED},
    # アセスメントなどから残した「専門職だけ」は、託された専門職にだけ見える（家族には見えない）
    (Role.PROFESSIONAL, True): {ShareScope.DECISION_SUPPORT, ShareScope.PROFESSIONAL_ONLY},
    (Role.PROFESSIONAL, False): set(),
}


@pytest.mark.parametrize(("key", "expected"), EXPECTED.items())
def test_役割と託す設定で見える範囲が決まる(key, expected):
    role, delegated = key
    assert consent.scopes_for(role, delegated) == expected


# ---- 実際の人で確かめる ----


@pytest.fixture
def records(add_statement):
    """4つの共有範囲の記録を1つずつ置く。"""
    return {scope: add_statement(f"{scope.value}の記録です。", scope=scope) for scope in ALL_SCOPES}


def _bodies(client, path="/api/statements"):
    response = client.get(path)
    assert response.status_code == 200
    return {item["body"] for item in response.json()["items"]}


def test_誠一さんには全部返る(login, records):
    assert _bodies(login("誠一さん")) == {f"{s.value}の記録です。" for s in ALL_SCOPES}


def test_美穂さんには本人だけ以外が返る(login, records):
    assert _bodies(login("美穂さん")) == {
        "家族の記録です。",
        "縁のある人までの記録です。",
        "意思決定支援用の記録です。",
    }


def test_節子さんには縁のある人までだけが返る(login, records):
    assert _bodies(login("節子さん")) == {"縁のある人までの記録です。"}


def test_田中さんには意思決定支援用と専門職だけが返る(login, records):
    assert _bodies(login("田中さん")) == {"意思決定支援用の記録です。", "専門職だけの記録です。"}


def test_託す設定がなければ美穂さんに意思決定支援用は返らない(conn, users, login, records):
    with conn:
        conn.execute("DELETE FROM delegations WHERE delegate_user_id = ?", (users["美穂さん"].id,))

    assert _bodies(login("美穂さん")) == {"家族の記録です。", "縁のある人までの記録です。"}


def test_招かれていなければ田中さんには何も返らない(conn, users, login, records):
    with conn:
        conn.execute("DELETE FROM delegations WHERE delegate_user_id = ?", (users["田中さん"].id,))

    assert _bodies(login("田中さん")) == set()


def test_託す設定があっても節子さんは縁のある人までだけ(conn, users, login, records):
    with conn:
        conn.execute(
            "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (?, ?, ?)",
            (users["節子さん"].id, ShareScope.DECISION_SUPPORT.value, "2026-11-02"),
        )

    assert _bodies(login("節子さん")) == {"縁のある人までの記録です。"}


# ---- 見えない記録が漏れないこと ----


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人だけの記録は本人以外の応答に何も出ない(login, records, name):
    """本文も、その行の番号も、件数の項目も出ない。番号は通し番号でないので、飛びからも数えられない。"""
    response = login(name).get("/api/statements")
    secret = records[ShareScope.SELF_ONLY]

    assert "本人だけの記録です。" not in response.text
    assert secret.id not in {item["id"] for item in response.json()["items"]}
    # 応答には一覧そのもののほか、合計や件数の項目を持たせない
    assert set(response.json().keys()) == {"items"}


HIDDEN_PAIRS = [
    ("美穂さん", ShareScope.SELF_ONLY),
    ("美穂さん", ShareScope.PROFESSIONAL_ONLY),
    ("節子さん", ShareScope.SELF_ONLY),
    ("節子さん", ShareScope.FAMILY),
    ("節子さん", ShareScope.DECISION_SUPPORT),
    ("田中さん", ShareScope.SELF_ONLY),
    ("田中さん", ShareScope.FAMILY),
    ("田中さん", ShareScope.CONNECTED),
]


@pytest.mark.parametrize(("name", "scope"), HIDDEN_PAIRS)
def test_見えない行を番号で開いても存在が分からない(login, records, name, scope):
    """403 だと「ある」と分かるので、存在しない番号と同じ応答にする。"""
    client = login(name)
    secret = records[scope]

    hidden = client.get(f"/api/statements/{secret.id}")
    missing = client.get("/api/statements/99999")

    assert hidden.status_code == 404
    assert hidden.status_code == missing.status_code
    assert hidden.json() == missing.json()


def test_見える行は番号で開ける(login, records):
    target = records[ShareScope.CONNECTED]

    response = login("節子さん").get(f"/api/statements/{target.id}")

    assert response.status_code == 200
    assert response.json()["body"] == "縁のある人までの記録です。"


def test_問い合わせで役割を名乗っても絞り込みは変わらない(login, records):
    """見える範囲はログイン中の人で決まり、ブラウザから送った値では変わらない。"""
    client = login("節子さん")

    response = client.get(
        "/api/statements", params={"role": "本人", "user_id": 1, "share_scope": "本人だけ"}
    )

    assert {item["body"] for item in response.json()["items"]} == {"縁のある人までの記録です。"}


def test_ログインしていなければ返さない(db_path, conn, records):
    client = TestClient(create_app(db_path))

    assert client.get("/api/statements").status_code == 401
    assert client.get(f"/api/statements/{records[ShareScope.CONNECTED].id}").status_code == 401
    assert client.get("/api/letters").status_code == 401


def test_存在しない人の印を送っても返さない(db_path, conn, records):
    client = TestClient(create_app(db_path))
    client.headers["X-NT-User"] = "99999"

    assert client.get("/api/statements").status_code == 401


# ---- 花びらと根拠の番号は、専門職の応答にだけ入る ----


@pytest.mark.parametrize("name", ["誠一さん", "美穂さん", "節子さん"])
def test_花びらと根拠の番号は専門職以外の応答に入らない(login, records, name):
    client = login(name)
    target = records[ShareScope.CONNECTED]

    items = client.get("/api/statements").json()["items"]
    opened = client.get(f"/api/statements/{target.id}").json()

    assert items
    for item in [*items, opened]:
        assert "petals" not in item
        assert "source_utterance_ids" not in item


def test_花びらと根拠の番号は専門職の応答に入る(login, records):
    client = login("田中さん")
    target = records[ShareScope.DECISION_SUPPORT]

    items = [item for item in client.get("/api/statements").json()["items"] if item["id"] == target.id]
    opened = client.get(f"/api/statements/{target.id}").json()

    assert items
    for item in [*items, opened]:
        assert item["petals"] == [petal.value for petal in target.petals]
        assert item["source_utterance_ids"] == target.source_utterance_ids


# ---- 訂正と共有範囲 ----


def test_訂正のあとは訂正後の文だけが一覧に出る(conn, users, login, add_statement, add_utterance):
    from app import ledger

    original = add_statement("娘とは週に一度話します。", scope=ShareScope.CONNECTED)
    said = add_utterance("娘とは毎日話します。")
    ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )

    assert _bodies(login("節子さん")) == {"娘とは毎日話します。"}


def test_本人が範囲を狭めたら古い広い行も見えなくなる(conn, users, login, add_statement):
    """見せるかどうかは、連鎖の先頭の共有範囲で決める。

    範囲を変える処理はまだないので、狭めた行を表に直接入れて確かめる。
    """
    original = add_statement("娘とは週に一度話します。", scope=ShareScope.CONNECTED)
    with conn:
        conn.execute(
            """
            INSERT INTO statements (
                id, source_utterance_ids, subject_user_id, speaker_user_id, speaker_is_twin,
                source, confirm_status, body, period, theme, petals, share_scope,
                effective_date, replaces_id, created_at
            ) VALUES (?, ?, ?, ?, 0, '本人の言葉', '未確認', ?, '今', '家族', ?, '本人だけ',
                      '2026-11-02', ?, '2026-11-02T00:00:00')
            """,
            (
                db.new_id(conn, "statements"),
                json.dumps(original.source_utterance_ids),
                users["誠一さん"].id,
                users["誠一さん"].id,
                original.body,
                json.dumps([p.value for p in original.petals], ensure_ascii=False),
                original.id,
            ),
        )

    client = login("節子さん")

    assert _bodies(client) == set()
    assert client.get(f"/api/statements/{original.id}").status_code == 404


def test_本人が範囲を広げても本人だけだった古い行は開けない(conn, users, login, add_statement):
    """本人だけ → 本人だけの訂正 → 縁のある人まで、の連鎖を表に直接入れて確かめる。"""
    first = add_statement("最初の文です。")

    def _append(scope, replaces_id):
        new_id = db.new_id(conn, "statements")
        with conn:
            conn.execute(
                """
                INSERT INTO statements (
                    id, source_utterance_ids, subject_user_id, speaker_user_id, speaker_is_twin,
                    source, confirm_status, body, period, theme, petals, share_scope,
                    effective_date, replaces_id, created_at
                ) VALUES (?, ?, ?, ?, 0, '本人の言葉', '未確認', ?, '今', '家族', '[]', ?,
                          '2026-11-02', ?, '2026-11-02T00:00:00')
                """,
                (
                    new_id,
                    json.dumps(first.source_utterance_ids),
                    users["誠一さん"].id,
                    users["誠一さん"].id,
                    first.body,
                    scope.value,
                    replaces_id,
                ),
            )
        return new_id

    second_id = _append(ShareScope.SELF_ONLY, first.id)
    third_id = _append(ShareScope.CONNECTED, second_id)

    setsuko = login("節子さん")
    assert setsuko.get(f"/api/statements/{first.id}").status_code == 404
    assert setsuko.get(f"/api/statements/{second_id}").status_code == 404

    # いま有効な行は見えるが、隠れた行の番号は返らない
    head = setsuko.get(f"/api/statements/{third_id}")
    assert head.status_code == 200
    assert "replaces_id" not in head.json()
    assert "replaces_id" not in setsuko.get("/api/statements").json()["items"][0]

    # 本人には、置き換えた行の番号も返る
    assert login("誠一さん").get(f"/api/statements/{third_id}").json()["replaces_id"] == second_id


@pytest.mark.parametrize(
    ("name", "scope"),
    [
        ("美穂さん", ShareScope.FAMILY),
        ("節子さん", ShareScope.CONNECTED),
        ("田中さん", ShareScope.DECISION_SUPPORT),
    ],
)
def test_訂正前の文は本人以外には開けない(conn, users, login, add_statement, add_utterance, name, scope):
    """共有範囲が同じでも、先頭でない行は存在しない番号と同じ応答にする。"""
    from app import ledger

    original = add_statement("娘とは週に一度話します。", scope=scope)
    said = add_utterance("娘とは毎日話します。")
    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )
    client = login(name)

    hidden = client.get(f"/api/statements/{original.id}")
    missing = client.get("/api/statements/99999")

    assert hidden.status_code == 404
    assert hidden.json() == missing.json()

    head = client.get(f"/api/statements/{corrected.id}")
    assert head.status_code == 200
    assert head.json()["body"] == "娘とは毎日話します。"
    assert "replaces_id" not in head.json()
    assert "週に一度" not in client.get("/api/statements").text


def test_本人は訂正の履歴を番号でたどれる(conn, users, login, add_statement, add_utterance):
    from app import ledger

    original = add_statement("娘とは週に一度話します。", scope=ShareScope.CONNECTED)
    said = add_utterance("娘とは毎日話します。")
    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )
    client = login("誠一さん")

    head = client.get(f"/api/statements/{corrected.id}").json()
    old = client.get(f"/api/statements/{head['replaces_id']}")

    assert old.status_code == 200
    assert old.json()["body"] == "娘とは週に一度話します。"


# ---- 便り（タイムラインの投稿）----


@pytest.fixture
def letters(conn, conversation_id):
    return {
        scope: ledger.add_letter(
            conn,
            LetterDraft(
                conversation_id=conversation_id,
                body=f"{scope.value}への便りです。",
                share_with=scope,
                shared_at=datetime(2026, 11, 2, 10, 0),
            ),
        ).id
        for scope in (ShareScope.FAMILY, ShareScope.CONNECTED)
    }


def test_便りは共有の相手で絞り込む(login, letters):
    both = {"家族への便りです。", "縁のある人までへの便りです。"}

    assert _bodies(login("誠一さん"), "/api/letters") == both
    assert _bodies(login("美穂さん"), "/api/letters") == both
    assert _bodies(login("節子さん"), "/api/letters") == {"縁のある人までへの便りです。"}
    assert _bodies(login("田中さん"), "/api/letters") == set()


def test_便りの共有の相手は家族か縁のある人までだけ(conn, conversation_id):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute(
                """
                INSERT INTO letters (id, conversation_id, body, share_with, shared_at)
                VALUES (777, ?, '本文', '本人だけ', '2026-11-02T10:00:00')
                """,
                (conversation_id,),
            )
