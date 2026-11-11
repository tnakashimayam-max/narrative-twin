"""本人の確認、休ませること、見せる範囲の変更のテスト。

役割：本人が決めることが新しい行として残り、元の行が消えないことを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：4（推測に本人が答えられる）、5（元の記録を消さない）、6（話さない自由）、10（範囲は本人が決める）。
"""

from __future__ import annotations

import pytest

from app import ledger
from app.schemas import ConfirmStatus, ShareScope


def test_確かめると新しい行が足され_元の行は残る(conn, users, add_statement):
    original = add_statement("毎朝庭を見ます。")

    confirmed = ledger.confirm_statement(conn, original.id, actor_user_id=users["誠一さん"].id)

    assert confirmed.confirm_status is ConfirmStatus.CONFIRMED
    assert confirmed.replaces_id == original.id
    assert confirmed.body == original.body
    assert ledger.get_statement(conn, original.id).confirm_status is ConfirmStatus.UNCONFIRMED
    assert ledger.head_of(conn, original.id).id == confirmed.id


def test_休ませると新しい行が足される(conn, users, add_statement):
    original = add_statement("毎朝庭を見ます。")

    rested = ledger.rest_statement(conn, original.id, actor_user_id=users["誠一さん"].id)

    assert rested.confirm_status is ConfirmStatus.RESTING
    assert rested.share_scope is ShareScope.SELF_ONLY
    assert ledger.get_statement(conn, original.id).confirm_status is ConfirmStatus.UNCONFIRMED


def test_範囲を変えると新しい行が足され_状態は引き継ぐ(conn, users, add_statement):
    me = users["誠一さん"].id
    original = add_statement("毎朝庭を見ます。")
    confirmed = ledger.confirm_statement(conn, original.id, actor_user_id=me)

    moved = ledger.change_share_scope(
        conn, confirmed.id, actor_user_id=me, share_scope=ShareScope.DECISION_SUPPORT
    )

    assert moved.share_scope is ShareScope.DECISION_SUPPORT
    assert moved.confirm_status is ConfirmStatus.CONFIRMED
    assert ledger.get_statement(conn, original.id).share_scope is ShareScope.SELF_ONLY


@pytest.mark.parametrize("name", ["美穂さん", "和子さん", "節子さん", "田中さん"])
def test_本人以外は確かめも休ませも範囲の変更もできない(conn, users, add_statement, name):
    original = add_statement("毎朝庭を見ます。")
    actor = users[name].id

    with pytest.raises(ledger.LedgerError):
        ledger.confirm_statement(conn, original.id, actor_user_id=actor)
    with pytest.raises(ledger.LedgerError):
        ledger.rest_statement(conn, original.id, actor_user_id=actor)
    with pytest.raises(ledger.LedgerError):
        ledger.change_share_scope(conn, original.id, actor_user_id=actor, share_scope=ShareScope.DECISION_SUPPORT)


def test_置き換えられた行は確かめられない(conn, users, add_statement):
    me = users["誠一さん"].id
    original = add_statement("毎朝庭を見ます。")
    ledger.confirm_statement(conn, original.id, actor_user_id=me)

    with pytest.raises(ledger.LedgerError):
        ledger.confirm_statement(conn, original.id, actor_user_id=me)


def test_ない言明は確かめられない(conn, users):
    with pytest.raises(ledger.LedgerError):
        ledger.confirm_statement(conn, 999999, actor_user_id=users["誠一さん"].id)
