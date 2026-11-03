"""ツインの色のテスト。

役割：本人が確かめた根拠を持つ要素だけで色がのり、減らず、数を返さないことを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：13（色は本人が確かめた分だけ。数字、割合、連続日数は出さない。使わなくても減らさない）。
"""

from __future__ import annotations

from datetime import date, datetime

from app import growth, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, Petal


def _element(conn, evidence, valid_from=date(2026, 1, 1), petals=(Petal.COMFORT,)):
    model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="誠一さんは庭を大切にしている。",
        condition=None,
        petals=list(petals),
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=valid_from,
        evidence_statement_ids=evidence,
        unit=ConsolidationUnit.MONTH,
    )
    conn.commit()


def _comfort(conn, day):
    return growth.levels_by_day(conn, [day])[0][list(Petal).index(Petal.COMFORT)]


def test_本人確認済みでない要素は色をのせない(conn, add_statement):
    statement = add_statement("毎朝庭を見ます。")
    _element(conn, [statement.id])

    assert growth.levels_by_day(conn, [date(2036, 1, 1)])[0] == [growth.NONE] * 5


def test_本人が確かめると色がのる(conn, users, add_statement):
    statement = add_statement("毎朝庭を見ます。")
    _element(conn, [statement.id])

    ledger.confirm_statement(conn, statement.id, actor_user_id=users["誠一さん"].id, at=datetime(2027, 1, 1))

    assert _comfort(conn, date(2026, 12, 31)) == growth.NONE
    assert _comfort(conn, date(2027, 1, 1)) == growth.LIGHT


def test_本人の訂正でも色がのる(conn, users, add_statement, add_utterance):
    statement = add_statement("毎朝庭を見ます。")
    _element(conn, [statement.id])
    spoken = add_utterance("妻と過ごした場所です。")

    ledger.correct_statement(
        conn,
        statement.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=spoken.id,
        body="妻と過ごした場所です。",
        at=datetime(2027, 1, 1),
    )

    assert _comfort(conn, date(2027, 1, 1)) == growth.LIGHT


def test_色は年を追って減らず_お休みにしても減らない(conn, users, add_statement):
    me = users["誠一さん"].id
    statements = [add_statement(f"毎朝庭を見ます。その{index}。") for index in range(12)]
    for index, statement in enumerate(statements):
        _element(conn, [statement.id])
        ledger.confirm_statement(conn, statement.id, actor_user_id=me, at=datetime(2026 + index % 10, 6, 1))
    order = [growth.NONE, growth.LIGHT, growth.MIDDLE, growth.DEEP]
    days = [date(year, 12, 31) for year in range(2026, 2036)]

    levels = [order.index(row[0]) for row in growth.levels_by_day(conn, days)]

    assert levels == sorted(levels)
    assert levels[-1] == order.index(growth.DEEP)
    head = ledger.head_of(conn, statements[0].id)
    ledger.rest_statement(conn, head.id, actor_user_id=me, at=datetime(2036, 1, 1))
    assert growth.levels_by_day(conn, [date(2036, 12, 31)])[0][0] == growth.DEEP


def test_確かめた行の日付で色を決める(conn, users, add_statement):
    statement = add_statement("毎朝庭を見ます。")
    _element(conn, [statement.id], valid_from=date(2030, 1, 1))
    ledger.confirm_statement(conn, statement.id, actor_user_id=users["誠一さん"].id, at=datetime(2027, 1, 1))

    assert _comfort(conn, date(2029, 12, 31)) == growth.NONE
    assert _comfort(conn, date(2030, 1, 1)) == growth.LIGHT


def test_色の応答に数と花びらの名前を返さない(login, add_statement):
    add_statement("毎朝庭を見ます。")

    client = login("誠一さん")
    body = client.get("/api/twin/appearance").json()
    items = client.get("/api/twin/growth").json()["items"]

    assert set(body) == {"levels"}
    assert len(body["levels"]) == 5
    assert all(level in {growth.NONE, growth.LIGHT, growth.MIDDLE, growth.DEEP} for level in body["levels"])
    assert all(set(item) == {"year", "levels"} for item in items)
    assert not {petal.value for petal in Petal} & set(body["levels"])
