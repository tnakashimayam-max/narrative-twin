"""固める処理。言明を月、年、人生の段階の順に、意思モデルの要素へまとめる。

役割：AI に要素の案を作らせ、根拠の番号と形をプログラムが確かめて保存する。
4つの極：3. 読み解き、積み上げる。
守る原則：3（要素は AI の読み取り）、5（元の記録を消さない）、11（推定と本人の言葉を混ぜない）。

新しい言明が今の要素と食い違っても上書きしない。「変化」の要素として残し、本人に確かめる問いを作る。
根拠は入力で振った番号で受け取る。本当の番号は53ビットの乱数で、AI に書き写させると誤りやすいため。
渡していない番号は捨て、根拠のない要素は入れない。
年と人生の段階の要素の根拠は、選んだ下の段の要素の根拠の和集合にする。

使っている Claude API の機能：JSON 出力と Pydantic での検証。10年分は Message Batches API（fiction/ の道具）で流す。
入力の番号の集合が前回と同じなら AI を呼ばない。2回押しても費用がかからないようにするため。

関連するテスト：tests/test_consolidate.py
全体の説明：docs/architecture.md、決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from app import ai, config, db, ledger, model_store, prompts
from app.schemas import (
    ConfirmStatus,
    ConsolidatedElement,
    ConsolidationResult,
    ConsolidationUnit,
    ElementKind,
    ElementStatus,
    LifeStage,
    ModelElement,
    Source,
    Statement,
)

MONTH_ROLE = "固める処理（月）"
UPPER_ROLE = "固める処理"
_CONFIRM_ENDING = "合っていますか？"


@dataclass
class Request:
    """1回の固める処理の入力。"""

    unit: ConsolidationUnit
    start: date
    end: date
    role: str
    system: str
    text: str
    # 根拠の番号（1始まり）から元の材料を引く
    statements: list[Statement] = field(default_factory=list)
    lower: list[ModelElement] = field(default_factory=list)
    context: list[ModelElement] = field(default_factory=list)
    previous: list[ModelElement] = field(default_factory=list)

    def input_ids(self, conn: sqlite3.Connection) -> list[int]:
        if self.unit is ConsolidationUnit.MONTH:
            return sorted(ledger.root_of(conn, item.id) for item in self.statements)
        return sorted({number for element in self.lower for number in element.evidence_statement_ids})


def month_span(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _speaker_name(conn: sqlite3.Connection, statement: Statement) -> str:
    if statement.speaker_is_twin:
        return "ツイン（AI）"
    user = db.get_user(conn, statement.speaker_user_id)
    return user.display_name if user else "はずした人"


def _statement_line(conn: sqlite3.Connection, number: int, statement: Statement) -> str:
    return (
        f"[{number}] {statement.effective_date.isoformat()}／{statement.source.value}／{_speaker_name(conn, statement)}"
        f"／{statement.confirm_status.value}／テーマ：{statement.theme.value}：{statement.body}"
    )


def _context_lines(elements: list[ModelElement], heads: dict[int, Statement]) -> str:
    if not elements:
        return "まだありません。"
    return "\n".join(model_store.describe(element, number, heads) for number, element in enumerate(elements, start=1))


def _context(conn: sqlite3.Connection, before: date) -> list[ModelElement]:
    """今の有効な要素のうち、人生の段階と年のものを新しい順に上限まで返す。読む量を増やさないため。"""
    upper = [
        element
        for element in model_store.active_elements(conn)
        if element.unit is not ConsolidationUnit.MONTH and element.valid_from < before
    ]
    upper.sort(key=lambda element: element.valid_from, reverse=True)
    return upper[: config.MODEL_LIMITS["context_elements"]]


def month_statements(conn: sqlite3.Connection, start: date, end: date) -> list[Statement]:
    """期間の、いま有効な言明を返す。お休みは入れない。今の様子はそのつど読むので要素にしない。"""
    return [
        statement
        for statement in ledger.current_statements(conn)
        if start <= statement.effective_date <= end
        and statement.confirm_status is not ConfirmStatus.RESTING
        and statement.source is not Source.OBSERVATION
    ]


def _self_name(conn: sqlite3.Connection) -> str:
    self_user = db.self_user(conn)
    return self_user.display_name if self_user else "本人"


def build_month(conn: sqlite3.Connection, year: int, month: int) -> Request | None:
    """月の固める処理の入力を作る。言明がなければ None を返す。"""
    start, end = month_span(year, month)
    statements = month_statements(conn, start, end)
    if not statements:
        return None
    heads = ledger.head_map(conn)
    context = _context(conn, start)
    previous = model_store.elements_in_period(conn, ConsolidationUnit.MONTH, start, end)
    lines: list[str] = []
    total = 0
    kept: list[Statement] = []
    for statement in statements:
        line = _statement_line(conn, len(kept) + 1, statement)
        if total + len(line) > config.MODEL_LIMITS["month_statement_chars"]:
            break
        lines.append(line)
        kept.append(statement)
        total += len(line)
    text = prompts.consolidate_user_text(
        period=f"{year}年{month}月",
        materials="\n".join(lines),
        context=_context_lines(context, heads),
        previous=_context_lines(previous, heads),
    )
    return Request(
        ConsolidationUnit.MONTH,
        start,
        end,
        MONTH_ROLE,
        prompts.consolidate_system(_self_name(conn), ConsolidationUnit.MONTH.value),
        text,
        statements=kept,
        context=context,
        previous=previous,
    )


def _build_upper(
    conn: sqlite3.Connection, unit: ConsolidationUnit, lower_unit: ConsolidationUnit, start: date, end: date, label: str
) -> Request | None:
    lower = model_store.elements_in_period(conn, lower_unit, start, end)
    if not lower:
        return None
    heads = ledger.head_map(conn)
    context = [element for element in _context(conn, start) if element.unit is ConsolidationUnit.LIFE_STAGE]
    previous = model_store.elements_in_period(conn, unit, start, end)
    text = prompts.consolidate_user_text(
        period=label,
        materials=_context_lines(lower, heads),
        context=_context_lines(context, heads),
        previous=_context_lines(previous, heads),
    )
    return Request(
        unit,
        start,
        end,
        UPPER_ROLE,
        prompts.consolidate_system(_self_name(conn), unit.value),
        text,
        lower=lower,
        context=context,
        previous=previous,
    )


def build_year(conn: sqlite3.Connection, year: int) -> Request | None:
    return _build_upper(
        conn, ConsolidationUnit.YEAR, ConsolidationUnit.MONTH, date(year, 1, 1), date(year, 12, 31), f"{year}年"
    )


def stage_span(conn: sqlite3.Connection, stage: LifeStage) -> tuple[date, date] | None:
    """人生の段階の期間を、会話の記録から求める。"""
    row = conn.execute(
        "SELECT MIN(conversation_date) AS first, MAX(conversation_date) AS last FROM conversations WHERE life_stage = ?",
        (stage.value,),
    ).fetchone()
    if row is None or row["first"] is None:
        return None
    return date.fromisoformat(row["first"]), date.fromisoformat(row["last"])


def build_stage(conn: sqlite3.Connection, stage: LifeStage) -> Request | None:
    span = stage_span(conn, stage)
    if span is None:
        return None
    start = date(span[0].year, 1, 1)
    end = date(span[1].year, 12, 31)
    return _build_upper(conn, ConsolidationUnit.LIFE_STAGE, ConsolidationUnit.YEAR, start, end, stage.value)


def _evidence_of(item: ConsolidatedElement, request: Request, conn: sqlite3.Connection) -> list[int]:
    """入力の番号を、言明のいちばん元の行の番号に直す。渡していない番号は捨てる。"""
    numbers: set[int] = set()
    for number in item.evidence_numbers:
        index = number - 1
        if request.unit is ConsolidationUnit.MONTH:
            if 0 <= index < len(request.statements):
                numbers.add(ledger.root_of(conn, request.statements[index].id))
        elif 0 <= index < len(request.lower):
            numbers.update(request.lower[index].evidence_statement_ids)
    return sorted(numbers)


def _pick(elements: list[ModelElement], number: int | None) -> ModelElement | None:
    if number is None or not 1 <= number <= len(elements):
        return None
    return elements[number - 1]


def _status(evidence: list[int], heads: dict[int, Statement]) -> ElementStatus:
    confirmed = any(
        number in heads and heads[number].confirm_status in {ConfirmStatus.CONFIRMED, ConfirmStatus.CORRECTED}
        for number in evidence
    )
    return ElementStatus.SELF_CONFIRMED if confirmed else ElementStatus.AI_VIEW


def _usable_question(question: str | None) -> bool:
    if not question:
        return False
    text = question.strip()
    return text.endswith(_CONFIRM_ENDING) and text.count("？") == 1 and text.count("?") == 0


def _latest_twin_conversation(conn: sqlite3.Connection) -> int | None:
    """問いの候補をぶら下げる会話を返す。候補はツインとの部屋の会話から選ばれるため。"""
    row = conn.execute(
        """
        SELECT c.id FROM conversations c JOIN rooms r ON r.id = c.room_id
        WHERE r.kind = 'ツイン' ORDER BY c.conversation_date DESC, c.rowid DESC LIMIT 1
        """
    ).fetchone()
    return row["id"] if row else None


def save_result(
    conn: sqlite3.Connection,
    request: Request,
    result: ConsolidationResult,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    now: datetime,
) -> list[ModelElement]:
    """AI の案を確かめて保存する。全部入るか、全部入らないかにする。"""
    heads = ledger.head_map(conn)
    saved: list[ModelElement] = []
    continued: set[int] = set()
    with ledger.atomic(conn):
        for item in result.elements:
            evidence = _evidence_of(item, request, conn)
            if not evidence:
                continue
            prior = _pick(request.previous, item.continues)
            conflict = _pick(request.context, item.conflicts_with)
            kind = ElementKind.CHANGE if conflict is not None else item.kind
            status = _status(evidence, heads)
            valid_from = request.start
            if prior is not None:
                continued.add(prior.id)
                model_store.close_element(conn, prior.id, now.date())
            element = model_store.add_element(
                conn,
                kind=kind,
                body=item.body.strip(),
                condition=(item.condition or "").strip() or None,
                petals=item.petals,
                confidence=item.confidence,
                # 変化は確かめるまで AI の見立てにする
                status=ElementStatus.AI_VIEW if kind is ElementKind.CHANGE else status,
                valid_from=valid_from,
                evidence_statement_ids=evidence,
                unit=request.unit,
                version=prior.version + 1 if prior else 1,
                previous_version_id=prior.id if prior else (conflict.id if conflict else None),
                unconfirmed_reconsolidations=(
                    prior.unconfirmed_reconsolidations + (0 if status is ElementStatus.SELF_CONFIRMED else 1)
                    if prior
                    else 0
                ),
            )
            saved.append(element)
            if kind is ElementKind.CHANGE and _usable_question(item.confirm_question):
                ledger.add_pending_question(
                    conn, question=item.confirm_question.strip(), conversation_id=_latest_twin_conversation(conn)
                )
        # 前回の要素のうち、続かなかったものは閉じる。行は残す
        for element in request.previous:
            if element.id not in continued:
                model_store.close_element(conn, element.id, now.date())
        model_store.add_run(
            conn,
            unit=request.unit,
            period_start=request.start,
            period_end=request.end,
            input_statement_ids=request.input_ids(conn),
            output_element_ids=[element.id for element in saved],
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            run_at=now,
        )
    return saved


def already_done(conn: sqlite3.Connection, request: Request) -> bool:
    """入力の言明の番号の集合が前回と同じかを返す。同じなら AI を呼ばない。

    状態が変わった言明も同じ番号なので、確認だけでは流し直さない。色は読むときに根拠から求める。
    """
    run = model_store.last_run(conn, request.unit, request.start, request.end)
    return run is not None and sorted(run.input_statement_ids) == request.input_ids(conn)


def run_request(conn: sqlite3.Connection, request: Request, now: datetime, client=None) -> list[ModelElement]:
    """1回の固める処理を、すぐに流す。"""
    if already_done(conn, request):
        return []
    result = ai.consolidate(request.role, system=request.system, text=request.text, client=client)
    db.log_ai_call(
        conn,
        role=request.role,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        target=f"{request.unit.value} {request.start.isoformat()}",
        seconds=result.seconds,
    )
    return save_result(
        conn,
        request,
        result.value,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        now=now,
    )


def _changed_months(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """前回の固める処理のあとで言明が変わった月を返す。"""
    months = sorted(
        {
            (statement.effective_date.year, statement.effective_date.month)
            for statement in ledger.current_statements(conn)
            if statement.source is not Source.OBSERVATION
        }
    )
    changed = []
    for year, month in months:
        request = build_month(conn, year, month)
        if request is not None and not already_done(conn, request):
            changed.append((year, month))
    return changed


def stage_of_day(conn: sqlite3.Connection, day: date) -> LifeStage:
    """その日の人生の段階を、その日までの最後の会話から求める。"""
    row = conn.execute(
        "SELECT life_stage FROM conversations WHERE conversation_date <= ? ORDER BY conversation_date DESC, rowid DESC LIMIT 1",
        (day.isoformat(),),
    ).fetchone()
    return LifeStage(row["life_stage"]) if row else LifeStage.ACTIVE


@dataclass
class RunReport:
    """固める処理の結果。"""

    months: list[str] = field(default_factory=list)
    elements: int = 0


def run_recent(conn: sqlite3.Connection, now: datetime, client=None) -> RunReport:
    """言明が変わった月を流し、その年と人生の段階も流し直す。"""
    report = RunReport()
    changed = _changed_months(conn)
    for year, month in changed:
        request = build_month(conn, year, month)
        report.elements += len(run_request(conn, request, now, client=client))
        report.months.append(f"{year}年{month}月")
    stages: set[LifeStage] = set()
    for year in sorted({year for year, _ in changed}):
        request = build_year(conn, year)
        if request is not None:
            report.elements += len(run_request(conn, request, now, client=client))
        stages.add(stage_of_day(conn, date(year, 12, 31) if year < now.year else now.date()))
    for stage in stages:
        request = build_stage(conn, stage)
        if request is not None:
            report.elements += len(run_request(conn, request, now, client=client))
    return report
