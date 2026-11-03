"""同意の絞り込み（決まった手順で動く層）。

役割：ログイン中の人の役割と託す設定から、見てよい記録だけを取り出す。AIは呼ばない。
4つの極：2. 分かち合う、4. 10年後に代弁する。
守る原則：10（託す人と見せる範囲は本人が決める。「本人だけ」は本人以外に件数も見せない）。

絞り込みをサーバー側のここに集めているのは、画面の表示で隠すだけだと、応答そのものに
見せてはいけない記録が残るため。応答を組み立てる前に、この関数で取り出す。

関連するテスト：tests/test_consent.py
全体の説明：docs/architecture.md
"""

from __future__ import annotations

import sqlite3

from app import db, ledger
from app.schemas import Letter, Role, ShareScope, Statement, User

# 役割ごとに、託す設定がなくても見える範囲
_BASE_SCOPES: dict[Role, frozenset[ShareScope]] = {
    Role.SELF: frozenset(ShareScope),
    Role.FAMILY: frozenset({ShareScope.FAMILY, ShareScope.CONNECTED}),
    Role.CONNECTED: frozenset({ShareScope.CONNECTED}),
    Role.PROFESSIONAL: frozenset(),
}

# 託す設定で意思決定支援用が見えるようになる役割。
# 縁のある人は入れない：「縁のある人まで」の記録だけを返す決まりなので、設定があっても広げない。
_CAN_BE_DELEGATED = {Role.FAMILY, Role.PROFESSIONAL}

# 役割ごとに応答から外す項目。
# 花びらと根拠の発言の番号は、専門職の画面にだけ出す決まりなので、ほかの人には返さない。
# 置き換えた言明の番号は、訂正の履歴をたどれる本人にだけ返す。
_HIDDEN_FIELDS: dict[Role, set[str]] = {
    Role.SELF: {"petals", "source_utterance_ids"},
    Role.FAMILY: {"petals", "source_utterance_ids", "replaces_id"},
    Role.CONNECTED: {"petals", "source_utterance_ids", "replaces_id"},
    Role.PROFESSIONAL: {"replaces_id"},
}


def scopes_for(role: Role, delegated: bool) -> frozenset[ShareScope]:
    """役割と託す設定の有無から、見える共有範囲を決める。"""
    scopes = _BASE_SCOPES[role]
    if delegated and role in _CAN_BE_DELEGATED:
        return scopes | {ShareScope.DECISION_SUPPORT}
    return scopes


def can_act_for_self(conn: sqlite3.Connection, viewer: User) -> bool:
    """本人か、立場が家族の託された人かを返す。専門職を招く操作とツインの操作は、この人だけができる。

    招かれた専門職にも delegations の行が入るので、行の有無だけでは決めない。専門職が次の専門職を
    招いたり、ツインをはずしたりしないよう、立場が家族であることも確かめる。
    関連するテスト：tests/test_room_access.py
    """
    return viewer.role is Role.SELF or (viewer.role is Role.FAMILY and _has_delegation(conn, viewer.id))


def _has_delegation(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM delegations WHERE delegate_user_id = ? AND scope = ? LIMIT 1",
        (user_id, ShareScope.DECISION_SUPPORT.value),
    ).fetchone()
    return row is not None


def visible_scopes(conn: sqlite3.Connection, viewer: User) -> frozenset[ShareScope]:
    """この人に見える共有範囲を返す。"""
    return scopes_for(viewer.role, _has_delegation(conn, viewer.id))


def visible_statements(conn: sqlite3.Connection, viewer: User) -> list[Statement]:
    """この人に見える、いま有効な言明を返す。"""
    return ledger.current_statements(conn, visible_scopes(conn, viewer))


def visible_statement(
    conn: sqlite3.Connection, viewer: User, statement_id: int
) -> Statement | None:
    """番号で開いた言明を、見てよいときだけ返す。

    本人は訂正の履歴も含めて開ける。本人以外が開けるのは連鎖の先頭の行だけで、
    見せるかどうかは先頭の行の共有範囲で決める。訂正前の文は本人にしか見せないため。
    見えない行は「ない」と同じ扱いにして、存在そのものを漏らさない。
    """
    statement = ledger.get_statement(conn, statement_id)
    if statement is None or viewer.role is Role.SELF:
        return statement
    head = ledger.head_of(conn, statement_id)
    if head.id != statement_id or head.share_scope not in visible_scopes(conn, viewer):
        return None
    return statement


def statement_for_viewer(viewer: User, statement: Statement) -> dict:
    """見える言明を、この人に返してよい項目だけの形にする。"""
    return statement.model_dump(mode="json", exclude=_HIDDEN_FIELDS[viewer.role])


def visible_letters(conn: sqlite3.Connection, viewer: User) -> list[Letter]:
    """この人に見える便りを、新しい順に返す。"""
    values = [scope.value for scope in visible_scopes(conn, viewer)]
    if not values:
        return []
    placeholders = ", ".join("?" for _ in values)
    rows = conn.execute(
        f"SELECT * FROM letters WHERE share_with IN ({placeholders}) "
        "ORDER BY shared_at DESC, rowid DESC",
        values,
    ).fetchall()
    return [db.row_to_model(row, Letter) for row in rows]


def visible_letter(conn: sqlite3.Connection, viewer: User, letter_id: int) -> Letter | None:
    """番号で開いた便りを、見てよいときだけ返す。見えない便りは「ない」と同じ扱いにする。"""
    row = conn.execute("SELECT * FROM letters WHERE id = ?", (letter_id,)).fetchone()
    if row is None:
        return None
    letter = db.row_to_model(row, Letter)
    return letter if letter.share_with in visible_scopes(conn, viewer) else None


# 共有範囲ごとの、見えるものの説明。
_SCOPE_WORDS: dict[ShareScope, str] = {
    ShareScope.FAMILY: "「家族」向けの便りとその写真、「家族」の範囲の記録",
    ShareScope.CONNECTED: "「縁のある人まで」向けの便りとその写真、「縁のある人まで」の範囲の記録",
    ShareScope.DECISION_SUPPORT: "もしものときの話し合いに使う記録（意思決定支援用）",
}


def role_change_preview(conn: sqlite3.Connection, person: User, new: Role) -> dict[str, list[str]]:
    """立場を変えると見えるようになるものと、見えなくなるものを言葉で返す。

    託す設定も含めて絞り込みと同じ関数から作る。説明と実際の見える範囲を食い違わせないため。
    """
    delegated = _has_delegation(conn, person.id)
    before = scopes_for(person.role, delegated)
    after = scopes_for(new, delegated)
    return {
        "sees": [_SCOPE_WORDS[scope] for scope in _SCOPE_WORDS if scope in after - before],
        "stops": [_SCOPE_WORDS[scope] for scope in _SCOPE_WORDS if scope in before - after],
        "same": [
            *[f"変わらず見えるもの：{_SCOPE_WORDS[scope]}" for scope in _SCOPE_WORDS if scope in after & before],
            "入っている部屋の発言と写真は、部屋にいる間は見えます",
        ],
    }
