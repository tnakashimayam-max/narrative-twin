"""固める処理と意思モデルのテスト。

役割：根拠の番号をプログラムが確かめること、変化を上書きしないこと、見える範囲、読む量の上限を確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：5（元の記録を消さない）、10（根拠が見えない要素は返さない）、13（本人が確かめた根拠）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import ai, config, consolidate, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Theme
from tests.fake_claude import RoleClaude

NOW = datetime(2026, 12, 1, 9, 0)


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _element(body="誠一さんは庭を大切にしている。", numbers=(1,), kind="価値", **more):
    return {"kind": kind, "body": body, "petals": ["くつろぎ"], "confidence": 0.7, "evidence_numbers": list(numbers), **more}


@pytest.fixture
def statement(add_statement):
    return add_statement("毎朝庭を見ます。", effective_date=date(2026, 11, 2))


def _run_month(conn, year=2026, month=11):
    request = consolidate.build_month(conn, year, month)
    return consolidate.run_request(conn, request, NOW)


def test_言明から要素を作り_根拠はいちばん元の行の番号で持つ(conn, statement, claude, users):
    confirmed = ledger.confirm_statement(conn, statement.id, actor_user_id=users["誠一さん"].id)
    claude.answers["固める処理"] = {"elements": [_element()]}

    saved = _run_month(conn)

    assert len(saved) == 1
    assert saved[0].evidence_statement_ids == [statement.id]
    assert saved[0].unit is ConsolidationUnit.MONTH
    assert saved[0].valid_from == date(2026, 11, 1)
    assert ledger.head_of(conn, saved[0].evidence_statement_ids[0]).id == confirmed.id


def test_渡していない番号の根拠は捨て_根拠のない要素は入らない(conn, statement, claude):
    claude.answers["固める処理"] = {
        "elements": [_element(numbers=(99,)), _element(body="誠一さんは朝を大切にしている。", numbers=(1, 99))]
    }

    saved = _run_month(conn)

    assert [item.body for item in saved] == ["誠一さんは朝を大切にしている。"]
    assert saved[0].evidence_statement_ids == [statement.id]


def test_要素の状態は根拠に本人の確かめがあるときだけ本人確認済み(conn, statement, claude, users):
    claude.answers["固める処理"] = {"elements": [_element()]}
    first = _run_month(conn)[0]
    assert first.status is ElementStatus.AI_VIEW
    heads = ledger.head_map(conn)
    assert model_store.confirmed_by_self(first, heads) is False

    ledger.confirm_statement(conn, statement.id, actor_user_id=users["誠一さん"].id)

    assert model_store.confirmed_by_self(first, ledger.head_map(conn)) is True


def test_固める処理は同じ入力の言明ではAIを呼ばない(conn, statement, claude):
    claude.answers["固める処理"] = {"elements": [_element()]}
    _run_month(conn)
    count = len(claude.requests)

    assert _run_month(conn) == []

    assert len(claude.requests) == count


def test_同じ月を流し直すと版が進み_前の版を指す(conn, add_statement, statement, claude):
    claude.answers["固める処理"] = {"elements": [_element()]}
    first = _run_month(conn)[0]
    add_statement("夕方も庭に出ます。", effective_date=date(2026, 11, 3))
    claude.answers["固める処理"] = {"elements": [_element(numbers=(1, 2), continues=1)]}

    second = _run_month(conn)[0]

    assert second.version == 2
    assert second.previous_version_id == first.id
    assert second.unconfirmed_reconsolidations == 1
    assert model_store.get_element(conn, first.id).valid_to == NOW.date()
    assert len(second.evidence_statement_ids) == 2


def test_続かなかった前回の要素は閉じ_行は残る(conn, add_statement, statement, claude):
    claude.answers["固める処理"] = {"elements": [_element()]}
    first = _run_month(conn)[0]
    add_statement("夕方も庭に出ます。", effective_date=date(2026, 11, 3))
    claude.answers["固める処理"] = {"elements": [_element(body="誠一さんは夕方を好む。", numbers=(2,))]}

    _run_month(conn)

    assert model_store.get_element(conn, first.id).valid_to == NOW.date()
    assert first.id not in {item.id for item in model_store.active_elements(conn)}


def test_変化は元の要素を閉じず_確かめる問いが1つ入る(conn, add_statement, claude, statement):
    claude.answers["固める処理"] = {"elements": [_element(body="誠一さんは運転を続けたい。")]}
    _run_month(conn)
    upper = model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="誠一さんは運転を続けたい。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=[statement.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    add_statement("免許を返しました。", effective_date=date(2027, 5, 1))
    claude.answers["固める処理"] = {
        "elements": [
            _element(
                body="誠一さんは免許を返した。",
                kind="価値",
                conflicts_with=1,
                confirm_question="運転を続けたいから返すへ変わったかもしれません。合っていますか？",
            )
        ]
    }

    change = _run_month(conn, 2027, 5)[0]

    assert change.kind is ElementKind.CHANGE
    assert change.status is ElementStatus.AI_VIEW
    assert change.previous_version_id == upper.id
    assert model_store.get_element(conn, upper.id).valid_to is None
    row = conn.execute("SELECT question, asked_on FROM question_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["question"].endswith("合っていますか？")
    assert row["asked_on"] is None


def test_年の要素の根拠は選んだ月の要素の根拠の和集合(conn, add_statement, statement, claude):
    second = add_statement("夕方も庭に出ます。", effective_date=date(2026, 12, 3))
    claude.answers["固める処理"] = {"elements": [_element()]}
    _run_month(conn, 2026, 11)
    claude.answers["固める処理"] = {"elements": [_element(body="誠一さんは夕方を好む。")]}
    _run_month(conn, 2026, 12)
    claude.answers["固める処理"] = {"elements": [_element(body="誠一さんは庭で過ごす。", numbers=(1, 2))]}

    year = consolidate.run_request(conn, consolidate.build_year(conn, 2026), NOW)[0]

    assert year.unit is ConsolidationUnit.YEAR
    assert sorted(year.evidence_statement_ids) == sorted([statement.id, second.id])


def test_お休みの言明は固める材料に入らない(conn, statement, users, claude):
    ledger.rest_statement(conn, statement.id, actor_user_id=users["誠一さん"].id)

    assert consolidate.build_month(conn, 2026, 11) is None


def test_固める処理の記録にモデルとトークン数と入力の番号が残る(conn, statement, claude):
    claude.answers["固める処理"] = {"elements": [_element()]}

    saved = _run_month(conn)

    start, end = consolidate.month_span(2026, 11)
    run = model_store.last_run(conn, ConsolidationUnit.MONTH, start, end)
    assert run.model
    assert run.input_tokens == 100
    assert run.input_statement_ids == [statement.id]
    assert run.output_element_ids == [saved[0].id]


def test_流し直しは変わった月と年と人生の段階だけを動かす(conn, statement, claude):
    claude.answers["固める処理"] = {"elements": [_element()]}

    report = consolidate.run_recent(conn, NOW)

    assert report.months == ["2026年11月"]
    assert len(claude.requests) == 3
    assert consolidate.run_recent(conn, NOW).months == []
    assert len(claude.requests) == 3


# ---- 見える範囲 ----


def _make_element(conn, evidence):
    element = model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="誠一さんは庭を大切にしている。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=evidence,
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    return element


def test_要素は根拠の先頭の行がすべて見えるときだけ返る(conn, users, add_statement):
    shared = add_statement("毎朝庭を見ます。", scope=ShareScope.DECISION_SUPPORT)
    private = add_statement("兄のことです。", scope=ShareScope.SELF_ONLY)
    visible = _make_element(conn, [shared.id])
    hidden = _make_element(conn, [shared.id, private.id])

    tanaka = model_store.visible_elements(conn, users["田中さん"])

    assert [item.id for item in tanaka] == [visible.id]
    assert {item.id for item in model_store.visible_elements(conn, users["誠一さん"])} == {visible.id, hidden.id}


def test_お休みにした根拠を持つ要素は見えなくなる(conn, users, add_statement):
    shared = add_statement("毎朝庭を見ます。", scope=ShareScope.DECISION_SUPPORT)
    _make_element(conn, [shared.id])

    ledger.rest_statement(conn, shared.id, actor_user_id=users["誠一さん"].id)

    assert model_store.visible_elements(conn, users["田中さん"]) == []


def test_招かれていない人には要素が見えない(conn, users, add_statement):
    shared = add_statement("毎朝庭を見ます。", scope=ShareScope.DECISION_SUPPORT)
    _make_element(conn, [shared.id])

    assert model_store.visible_elements(conn, users["節子さん"]) == []


# ---- 読む量 ----


def test_要素がいくら増えても読む量は上限を超えない(conn, add_statement):
    shared = add_statement("毎朝庭を見ます。")
    for index in range(600):
        model_store.add_element(
            conn,
            kind=ElementKind.VALUE,
            body=f"誠一さんは庭を大切にしている。その{index}。" + "あ" * 40,
            condition=None,
            petals=[],
            confidence=0.5,
            status=ElementStatus.AI_VIEW,
            valid_from=date(2026, 1, 1),
            evidence_statement_ids=[shared.id],
            unit=ConsolidationUnit.MONTH,
        )
    conn.commit()
    heads = ledger.head_map(conn)

    kept = model_store.for_reading(model_store.active_elements(conn), heads)

    assert sum(len(item.body) + 30 for item in kept) <= config.MODEL_LIMITS["element_chars"]
    assert len(kept) < 600


def test_読む順は人生の段階から年そして新しい月(conn, add_statement):
    shared = add_statement("毎朝庭を見ます。", theme=Theme.LIKES)
    for unit, day in [
        (ConsolidationUnit.MONTH, date(2027, 2, 1)),
        (ConsolidationUnit.MONTH, date(2028, 2, 1)),
        (ConsolidationUnit.YEAR, date(2027, 1, 1)),
        (ConsolidationUnit.LIFE_STAGE, date(2026, 1, 1)),
    ]:
        model_store.add_element(
            conn,
            kind=ElementKind.VALUE,
            body=f"{unit.value}{day}",
            condition=None,
            petals=[],
            confidence=0.5,
            status=ElementStatus.AI_VIEW,
            valid_from=day,
            evidence_statement_ids=[shared.id],
            unit=unit,
        )
    conn.commit()

    kept = model_store.for_reading(model_store.active_elements(conn), ledger.head_map(conn))

    assert [item.unit for item in kept] == [
        ConsolidationUnit.LIFE_STAGE,
        ConsolidationUnit.YEAR,
        ConsolidationUnit.MONTH,
        ConsolidationUnit.MONTH,
    ]
    assert kept[2].valid_from > kept[3].valid_from


def test_AIに渡す要素の説明に本人の言葉の本文は入らない(conn, add_statement):
    shared = add_statement("目印の本人の言葉です。")
    element = _make_element(conn, [shared.id])

    line = model_store.describe(element, 1, ledger.head_map(conn))

    assert "目印の本人の言葉" not in line
    assert element.body in line
