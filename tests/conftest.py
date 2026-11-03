"""テストの共通部品。

役割：一時的なデータベース、架空の利用者、ログイン済みのブラウザ役を用意する。
4つの極：特定の極に属さない土台。
守る原則：8（テストの人物も架空の人だけを使う）。

本物の API キーと本物のデータベースに触れないようにするのが、この場所の大事な役目。
テストはキーがなくても、ネットにつながらなくても通る。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import config, db, ledger
from app.main import create_app
from app.schemas import (
    ConfirmStatus,
    ConversationDraft,
    LifeStage,
    Period,
    Petal,
    Place,
    Residence,
    Role,
    ShareScope,
    Source,
    StatementDraft,
    Theme,
    UtteranceDraft,
)


@pytest.fixture(autouse=True)
def keep_real_key_out(monkeypatch, tmp_path):
    """本物の .env と環境変数のキーをテストから隠す（誤って Claude を呼ばないため）。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "存在しない.env")


@pytest.fixture(autouse=True)
def keep_real_logs_out(monkeypatch, tmp_path):
    """開発のログを一時的な場所へ向ける。テストの呼び出しで本物の logs/ を増やさないため。"""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")


@pytest.fixture(autouse=True)
def professionals_open_in_tests(monkeypatch):
    """既存のテストは招かれた田中さんを使うので、日付の切り替えを開けておく。切り替えのテストだけ閉じる。"""
    monkeypatch.setattr(config, "PROFESSIONAL_FROM", date(2000, 1, 1))


def invite_tanaka(connection) -> None:
    """招かれた田中さんがいる状態を作る。招く道のテストは tests/test_room_access.py にある。"""
    with connection:
        connection.execute(
            "INSERT INTO users (id, name, role, called_name, relation, residence) VALUES (?, ?, ?, ?, ?, ?)",
            (6, "田中さん", Role.PROFESSIONAL.value, "田中さん", "ケアマネジャー", Residence.NEAR.value),
        )
        connection.execute(
            "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (?, ?, ?)",
            (6, ShareScope.DECISION_SUPPORT.value, "2036-01-01"),
        )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    db.init_db(db_path)
    connection = db.connect(db_path)
    invite_tanaka(connection)
    yield connection
    connection.close()


@pytest.fixture
def users(conn):
    """名前で引ける利用者の表。例：users["節子さん"]"""
    return {user.name: user for user in db.list_users(conn)}


@pytest.fixture
def conversation_id(conn):
    """発言をぶら下げる会話を1つ作る。"""
    draft = ConversationDraft(conversation_date=date(2026, 11, 2), life_stage=LifeStage.ACTIVE)
    return ledger.add_conversation(conn, draft).id


@pytest.fixture
def add_utterance(conn, users, conversation_id):
    """発言を1つ追記する。話した人を省くと誠一さん（本人）の発言になる。"""

    def _add(text, speaker="誠一さん", is_twin=False):
        return ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation_id,
                speaker_user_id=None if is_twin else users[speaker].id,
                speaker_is_twin=is_twin,
                text=text,
                spoken_at=datetime(2026, 11, 2, 10, 0),
                place=Place.DIARY_AND_TALK,
            ),
        )

    return _add


@pytest.fixture
def make_draft(users):
    """言明の下書きを作る。指定しない項目は、本人の言葉として素直な値にする。"""

    def _make(utterance_ids, body, **changes):
        values = {
            "source_utterance_ids": utterance_ids,
            "subject_user_id": users["誠一さん"].id,
            "speaker_user_id": users["誠一さん"].id,
            "speaker_is_twin": False,
            "source": Source.SELF_WORDS,
            "confirm_status": ConfirmStatus.UNCONFIRMED,
            "body": body,
            "period": Period.NOW,
            "theme": Theme.FAMILY,
            "petals": [Petal.ATTACHMENT],
            "share_scope": ShareScope.SELF_ONLY,
            "effective_date": date(2026, 11, 2),
        }
        values.update(changes)
        return StatementDraft(**values)

    return _make


@pytest.fixture
def add_statement(conn, add_utterance, make_draft):
    """本人の言葉の言明を、発言ごと追記する。絞り込みのテストで何度も使う。"""

    def _add(body, scope=ShareScope.SELF_ONLY, **changes):
        utterance = add_utterance(body)
        return ledger.append_statement(
            conn, make_draft([utterance.id], body, share_scope=scope, **changes)
        )

    return _add


@pytest.fixture
def login(db_path, conn, users):
    """指定した人としてログインしたブラウザ役を返す。"""

    def _login(name):
        client = TestClient(create_app(db_path))
        response = client.post("/api/login", json={"user_id": users[name].id})
        assert response.status_code == 200
        client.headers["X-NT-User"] = str(users[name].id)
        return client

    return _login
