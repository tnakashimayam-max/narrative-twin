"""行の番号の持ち方のテスト。

役割：通し番号の飛びから見えない行の数が分からないこと、並び順が番号に頼らないことを確かめる。
4つの極：2. 分かち合う、4. 10年後に代弁する。
守る原則：10（「本人だけ」は本人以外に件数も見せない）。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from app import db, ledger
from app.schemas import (
    CommentDraft,
    ConversationDraft,
    LetterDraft,
    LifeStage,
    ShareScope,
)

# 期待する表はコードから読まず、ここに書き写す
RANDOM_ID_TABLES = [
    "selfhood_pieces",
    "selfhood_evidence",
    "selfhood_documents",
    "dialogue_readings",
    "heart_readings",
    "statements",
    "utterances",
    "conversations",
    "letters",
    "comments",
    "photos",
    "interpretations",
    "model_elements",
    "inference_logs",
    "rooms",
    "room_messages",
    "advocacy_turns",
    "advocacy_voices",
    "advocacy_sheets",
    "advocacy_plans",
    "advocacy_plan_choices",
]
SEQUENTIAL_ID_TABLES = [
    "handover_themes",
    "users",
    "delegations",
    "access_logs",
    "question_logs",
    "consolidation_runs",
    "evaluations",
    "care_plan_drafts",
    "ai_calls",
    "twin_presences",
    "family_links",
    "map_places",
    "map_links",
    "person_candidates",
    "job_failures",
]
# 番号の列がない表。部屋の人と読んだ位置は部屋と人の組で、印は対象の組で見分ける。
NO_ID_TABLES = ["room_members", "room_reads", "split_marks", "story_marks", "story_keys", "ai_call_cache"]
LARGEST_SAFE_INTEGER = 2**53 - 1


def _id_column(conn, table):
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return next(column for column in columns if column["name"] == "id")


def test_番号の持ち方をすべての表で決めている():
    assert set(RANDOM_ID_TABLES) | set(SEQUENTIAL_ID_TABLES) | set(NO_ID_TABLES) == set(db.TABLE_MODELS)


@pytest.mark.parametrize("table", RANDOM_ID_TABLES)
def test_隠れる行がある表の番号は指定が必須で_自動の通し番号が付かない(conn, table):
    column = _id_column(conn, table)

    assert column["pk"] == 0
    assert column["notnull"] == 1


@pytest.mark.parametrize("table", SEQUENTIAL_ID_TABLES)
def test_本人以外に番号を出さない表は通し番号のまま(conn, table):
    assert _id_column(conn, table)["pk"] == 1


def test_番号を指定しない行は入らない(conn):
    with pytest.raises(sqlite3.IntegrityError, match="id"):
        with conn:
            conn.execute(
                "INSERT INTO conversations (conversation_date, life_stage) VALUES (?, ?)",
                ("2026-11-02", LifeStage.ACTIVE.value),
            )


@pytest.mark.parametrize("value", [0, -1, LARGEST_SAFE_INTEGER + 1])
def test_JavaScriptで安全に扱えない番号は入らない(conn, value):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute(
                "INSERT INTO conversations (id, conversation_date, life_stage) VALUES (?, ?, ?)",
                (value, "2026-11-02", LifeStage.ACTIVE.value),
            )


@pytest.mark.parametrize(
    ("drawn", "expected"), [(0, 1), (LARGEST_SAFE_INTEGER - 1, LARGEST_SAFE_INTEGER)]
)
def test_作る番号はJavaScriptで安全に扱える範囲に入る(conn, monkeypatch, drawn, expected):
    bounds = []

    def _draw(bound):
        bounds.append(bound)
        return drawn

    monkeypatch.setattr(db.secrets, "randbelow", _draw)

    assert db.new_id(conn, "statements") == expected
    assert bounds == [LARGEST_SAFE_INTEGER]


def test_同じ番号が出たら作り直す(conn, monkeypatch, add_statement):
    existing = add_statement("朝の散歩が好きです。")
    draws = iter([existing.id - 1, 4999])
    monkeypatch.setattr(db.secrets, "randbelow", lambda _bound: next(draws))

    assert db.new_id(conn, "statements") == 5000


def test_番号は通し番号にならない(add_statement):
    ids = sorted(add_statement(f"記録{number}です。").id for number in range(20))

    assert all(later - earlier > 1000 for earlier, later in zip(ids, ids[1:]))


def test_追記した行を番号で取り直せる(conn, users, add_statement, add_utterance, conversation_id):
    """番号が rowid と別になったので、追記のあと正しい行を読み直せることを確かめる。"""
    said = add_utterance("朝の散歩が好きです。")
    statement = add_statement("庭の花も見ます。")
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 2)),
    )
    comment = ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter.id,
            author_user_id=users["美穂さん"].id,
            body="コメントです。",
            commented_at=datetime(2026, 11, 2),
        ),
    )

    assert ledger.get_utterance(conn, said.id).text == "朝の散歩が好きです。"
    assert ledger.get_statement(conn, statement.id).body == "庭の花も見ます。"
    assert letter.body == "便りです。"
    assert comment.body == "コメントです。"


# ---- 並び順は番号に頼らない ----


@pytest.fixture
def descending_ids(monkeypatch):
    """番号が追記の順と逆に大きくなるようにする。番号に頼った並びなら、順番が逆になる。"""
    numbers = iter(range(1_000_000, 0, -1000))
    monkeypatch.setattr(db.secrets, "randbelow", lambda _bound: next(numbers))


def test_同じ時刻の言明は追記した順に並ぶ(conn, descending_ids, add_statement):
    same_time = datetime(2026, 11, 2, 10, 0)
    bodies = ["一つ目です。", "二つ目です。", "三つ目です。"]
    for body in bodies:
        add_statement(body, created_at=same_time)

    assert [item.body for item in ledger.current_statements(conn)] == bodies


def test_一覧の並びも番号に頼らない(login, descending_ids, add_statement):
    same_time = datetime(2026, 11, 2, 10, 0)
    bodies = ["一つ目です。", "二つ目です。", "三つ目です。"]
    for body in bodies:
        add_statement(body, created_at=same_time, effective_date=date(2026, 11, 2))

    items = login("誠一さん").get("/api/statements").json()["items"]

    assert [item["body"] for item in items] == bodies


def test_同じ時刻の便りは新しく追記した順に並ぶ(conn, login, descending_ids):
    same_time = datetime(2026, 11, 2, 10, 0)
    for body in ["一つ目の便り", "二つ目の便り"]:
        ledger.add_letter(
            conn, LetterDraft(body=body, share_with=ShareScope.FAMILY, shared_at=same_time)
        )

    items = login("美穂さん").get("/api/letters").json()["items"]

    assert [item["body"] for item in items] == ["二つ目の便り", "一つ目の便り"]


@pytest.mark.parametrize("name", ["誠一さん", "美穂さん", "節子さん", "田中さん"])
def test_応答に追記の順の番号は出ない(login, add_statement, name):
    for scope in ShareScope:
        add_statement(f"{scope.value}の記録です。", scope=scope)

    items = login(name).get("/api/statements").json()["items"]

    assert items
    for item in items:
        assert "rowid" not in item


# ---- 会話、便り、コメントの追記 ----


def test_会話を追記できる(conn):
    conversation = ledger.add_conversation(
        conn,
        ConversationDraft(
            conversation_date=date(2026, 11, 2),
            trigger="庭の写真",
            life_stage=LifeStage.ACTIVE,
        ),
    )

    assert conversation.trigger == "庭の写真"
    assert conversation.summary is None


def test_コメントの本文は発言としても残る(conn, users):
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 2)),
    )

    comment = ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter.id,
            author_user_id=users["美穂さん"].id,
            body="お父さんは庭が好きでしたね。",
            commented_at=datetime(2026, 11, 2, 12, 0),
        ),
    )

    utterance = ledger.get_utterance(conn, comment.utterance_id)
    assert utterance.text == "お父さんは庭が好きでしたね。"
    assert utterance.letter_id == letter.id
    assert utterance.speaker_user_id == users["美穂さん"].id
    assert utterance.place.value == "スレッド"


def test_ツインの問いはコメントでもツインの発言になる(conn):
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 2)),
    )

    comment = ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter.id,
            body="誠一さん、庭の何がお好きですか？",
            commented_at=datetime(2026, 11, 2, 12, 0),
            is_twin_question=True,
        ),
    )

    utterance = ledger.get_utterance(conn, comment.utterance_id)
    assert comment.author_user_id is None
    assert utterance.speaker_is_twin is True
    assert utterance.speaker_user_id is None


def test_コメントが入らなければ発言も残らない(conn, users):
    """発言とコメントは一緒に入る。片方だけ残ると、原文の控えが宙に浮くため。"""
    before = conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]

    with pytest.raises(ledger.LedgerError):
        ledger.add_comment(
            conn,
            CommentDraft(
                letter_id=99999,
                author_user_id=users["美穂さん"].id,
                body="コメントです。",
                commented_at=datetime(2026, 11, 2),
            ),
        )

    assert conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_便りの共有の相手は型でも家族か縁のある人まで():
    with pytest.raises(ValueError):
        LetterDraft(body="便りです。", share_with=ShareScope.SELF_ONLY, shared_at=datetime(2026, 11, 2))
