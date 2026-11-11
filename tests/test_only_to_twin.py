"""ツインにだけ話した言葉の絞り込みのテスト。

役割：本人以外の一覧と、番号で開く API と、根拠をたどる API から、ツインにだけ話した言葉をサーバーで外すことを確かめる。
4つの極：2. 分かち合う。4. 10年後に代弁する。
守る原則：10（見せる範囲はサーバーが決める）、16（言葉は補強するときだけ引く）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import consent, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, Place, ShareScope, UtteranceDraft


def _statement(conn, users, conversation_id, make_draft, body, place):
    utterance = ledger.add_utterance(
        conn,
        UtteranceDraft(
            conversation_id=conversation_id,
            speaker_user_id=users["誠一さん"].id,
            speaker_is_twin=False,
            text=body,
            spoken_at=datetime(2036, 1, 1, 10),
            place=place,
        ),
    )
    statement = ledger.append_statement(conn, make_draft([utterance.id], body, share_scope=ShareScope.DECISION_SUPPORT))
    conn.commit()
    return statement


@pytest.fixture
def words(conn, users, conversation_id, make_draft):
    told = _statement(conn, users, conversation_id, make_draft, "ツインにだけ話した言葉。", Place.DIARY_AND_TALK)
    shared = _statement(conn, users, conversation_id, make_draft, "人に話した言葉。", Place.CHAT)
    return told, shared


@pytest.fixture
def miho(login):
    """託された家族。本人以外の代表として使う。"""
    return login("美穂さん")


def test_本人以外の一覧からツインにだけ話した言葉を外す(words, miho):
    bodies = [item["body"] for item in miho.get("/api/statements").json()["items"]]

    assert "人に話した言葉。" in bodies
    assert "ツインにだけ話した言葉。" not in bodies


def test_本人の一覧には残る(words, login):
    bodies = [item["body"] for item in login("誠一さん").get("/api/statements").json()["items"]]

    assert "ツインにだけ話した言葉。" in bodies


def test_番号で開いても見えない(words, miho):
    told, shared = words

    assert miho.get(f"/api/statements/{told.id}").status_code == 404
    assert miho.get(f"/api/statements/{shared.id}").status_code == 200


def test_AIの材料には残る(conn, users, words):
    assert "ツインにだけ話した言葉。" in [item.body for item in consent.visible_statements(conn, users["美穂さん"])]


def test_根拠をたどっても原文が見えない(conn, words, miho):
    told, shared = words
    element = model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="家を大事にしている。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=[told.id, shared.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()

    body = miho.get(f"/api/advocacy/elements/{element.id}").text

    assert "人に話した言葉。" in body
    assert "ツインにだけ話した言葉。" not in body
