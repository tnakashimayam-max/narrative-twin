"""意思モデルの要素の読み書き（決まった手順で動く層）。AI は呼ばない。

役割：要素と固める処理の記録を保存し、見える要素を絞り、読む量の上限の中で並べる。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：3（要素は AI の読み取りで、本人の言葉と分ける）、10（根拠が見えない要素は返さない）、
          13（色は本人が確かめた根拠を持つ要素だけ）。

意思モデルは言明から作り直せる。本人の言葉は作り直せないので、要素は根拠の言明の番号だけを持ち、本文を写さない。
根拠の番号は訂正の連鎖のいちばん元の行で持つ。確かめや訂正で先頭の行が増えても、番号を変えずにたどれるため。

要素が見えるのは、根拠の先頭の行がすべて見えるときだけ。隠れた根拠があると、要素の本文から
「本人だけ」の中身が漏れるため。

関連するテスト：tests/test_consolidate.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app import config, consent, db, ledger
from app.schemas import (
    ConfirmStatus,
    ConsolidationRun,
    ConsolidationUnit,
    ElementKind,
    ElementStatus,
    ModelElement,
    Petal,
    Role,
    Statement,
    User,
)

_CONFIRMED = {ConfirmStatus.CONFIRMED, ConfirmStatus.CORRECTED}
_UNIT_ORDER = {ConsolidationUnit.LIFE_STAGE: 0, ConsolidationUnit.YEAR: 1, ConsolidationUnit.MONTH: 2}


def add_element(
    conn: sqlite3.Connection,
    *,
    kind: ElementKind,
    body: str,
    condition: str | None,
    petals: list[Petal],
    confidence: float,
    status: ElementStatus,
    valid_from: date,
    evidence_statement_ids: list[int],
    unit: ConsolidationUnit,
    version: int = 1,
    previous_version_id: int | None = None,
    unconfirmed_reconsolidations: int = 0,
    valid_to: date | None = None,
) -> ModelElement:
    """要素を入れる。根拠のない要素は入れない。"""
    if not evidence_statement_ids:
        raise ledger.LedgerError("根拠のない要素は入れられません")
    element_id = db.insert_row(
        conn,
        "model_elements",
        {
            "kind": kind.value,
            "body": body,
            "condition": condition,
            "petals": [petal.value for petal in petals],
            "confidence": confidence,
            "status": status.value,
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat() if valid_to else None,
            "evidence_statement_ids": evidence_statement_ids,
            "unit": unit.value,
            "version": version,
            "previous_version_id": previous_version_id,
            "last_confirmed_on": None,
            "unconfirmed_reconsolidations": unconfirmed_reconsolidations,
        },
    )
    return get_element(conn, element_id)


def get_element(conn: sqlite3.Connection, element_id: int) -> ModelElement | None:
    row = conn.execute("SELECT * FROM model_elements WHERE id = ?", (element_id,)).fetchone()
    return db.row_to_model(row, ModelElement) if row else None


def close_element(conn: sqlite3.Connection, element_id: int, day: date) -> None:
    """要素の有効期間を閉じる。行は残す。版のたどりと、変化の説明に使うため。"""
    conn.execute("UPDATE model_elements SET valid_to = ? WHERE id = ?", (day.isoformat(), element_id))


def add_run(
    conn: sqlite3.Connection,
    *,
    unit: ConsolidationUnit,
    period_start: date,
    period_end: date,
    input_statement_ids: list[int],
    output_element_ids: list[int],
    model: str,
    input_tokens: int,
    output_tokens: int,
    run_at: datetime,
) -> None:
    """固める処理の記録を残す。どのモデルで作ったかを、あとから確かめられるようにする。"""
    conn.execute(
        """
        INSERT INTO consolidation_runs (unit, period_start, period_end, input_statement_ids, output_element_ids,
            model, input_tokens, output_tokens, run_at)
        VALUES (?, ?, ?, json(?), json(?), ?, ?, ?, ?)
        """,
        (
            unit.value,
            period_start.isoformat(),
            period_end.isoformat(),
            str(sorted(input_statement_ids)),
            str(output_element_ids),
            model,
            input_tokens,
            output_tokens,
            run_at.isoformat(),
        ),
    )


def last_run(
    conn: sqlite3.Connection, unit: ConsolidationUnit, period_start: date, period_end: date
) -> ConsolidationRun | None:
    row = conn.execute(
        """
        SELECT * FROM consolidation_runs WHERE unit = ? AND period_start = ? AND period_end = ?
        ORDER BY id DESC LIMIT 1
        """,
        (unit.value, period_start.isoformat(), period_end.isoformat()),
    ).fetchone()
    return db.row_to_model(row, ConsolidationRun) if row else None


def active_elements(conn: sqlite3.Connection, unit: ConsolidationUnit | None = None) -> list[ModelElement]:
    """いま有効な要素を返す。"""
    query = "SELECT * FROM model_elements WHERE valid_to IS NULL"
    params: list[str] = []
    if unit is not None:
        query += " AND unit = ?"
        params.append(unit.value)
    rows = conn.execute(query + " ORDER BY valid_from, rowid", params).fetchall()
    return [db.row_to_model(row, ModelElement) for row in rows]


def elements_in_period(
    conn: sqlite3.Connection, unit: ConsolidationUnit, start: date, end: date
) -> list[ModelElement]:
    """期間の中で始まった、いま有効な要素を返す。"""
    rows = conn.execute(
        """
        SELECT * FROM model_elements
        WHERE valid_to IS NULL AND unit = ? AND valid_from >= ? AND valid_from <= ?
        ORDER BY valid_from, rowid
        """,
        (unit.value, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [db.row_to_model(row, ModelElement) for row in rows]


def confirmed_by_self(element: ModelElement, heads: dict[int, Statement]) -> bool:
    """根拠の先頭の行に、本人が確かめた行があるかを返す。確かめられるのは本人だけなので出どころは問わない。"""
    return any(
        number in heads and heads[number].confirm_status in _CONFIRMED for number in element.evidence_statement_ids
    )


def visible_elements(conn: sqlite3.Connection, viewer: User) -> list[ModelElement]:
    """この人に見える、いま有効な要素を返す。根拠の先頭の行がすべて見えるときだけ見える。"""
    elements = active_elements(conn)
    if viewer.role is Role.SELF:
        return elements
    scopes = consent.visible_scopes(conn, viewer)
    if not scopes:
        return []
    heads = ledger.head_map(conn)
    return [
        element
        for element in elements
        if all(
            number in heads
            and heads[number].share_scope in scopes
            and heads[number].confirm_status is not ConfirmStatus.RESTING
            for number in element.evidence_statement_ids
        )
    ]


def for_reading(elements: list[ModelElement], heads: dict[int, Statement]) -> list[ModelElement]:
    """読む量の上限の中で要素を選ぶ。記録が増えても読む量を増やさないため。

    人生の段階、新しい年、新しい月の順に、本人が確かめたものを先にして、上限の字数まで入れる。
    並びが毎回同じになるので、プロンプトキャッシュに当たる。
    """

    def rank(element: ModelElement) -> tuple:
        return (
            _UNIT_ORDER[element.unit],
            -element.valid_from.toordinal(),
            not confirmed_by_self(element, heads),
            element.id,
        )

    kept: list[ModelElement] = []
    total = 0
    for element in sorted(elements, key=rank):
        size = len(element.body) + len(element.condition or "") + 30
        if total + size > config.MODEL_LIMITS["element_chars"]:
            break
        kept.append(element)
        total += size
    return kept


def describe(element: ModelElement, number: int, heads: dict[int, Statement]) -> str:
    """AI に渡す1行を作る。本人の言葉の本文は入れない。"""
    state = "本人が確かめた" if confirmed_by_self(element, heads) else "AIの見立て"
    condition = f"（条件：{element.condition}）" if element.condition else ""
    return (
        f"[{number}] {element.unit.value}／{element.kind.value}／{element.valid_from.isoformat()}から／{state}："
        f"{element.body}{condition}"
    )
