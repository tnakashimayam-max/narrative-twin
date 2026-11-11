"""託す設定と、本人の確認の API。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：託す人と託すテーマを本人が決める。「今日うかがったこと」を確かめ、直し、休ませる。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：4（推測に本人が答えられる）、5（元の記録を消さない）、6（話さない自由）、
          10（託す人と見せる範囲は本人が決める）、13（色は本人が確かめた分だけ）。

託す範囲はテーマで決める。選んだテーマの言葉は、未確認も含めて「意思決定支援用」の新しい行にする。
確かめたかどうかは出どころのそばに表示して分ける。決めた経緯は docs/decisions.md にある。

訂正の言葉は、部屋のない「確認」の会話に発言として置く。今日の会話に置くと、裏の分割が別の言明を
作って二重になるため（app/jobs.py の分割は、部屋の会話と便りだけを読む）。

関連するテスト：tests/test_handover.py、tests/test_confirm.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import clock, db, ledger, verbatim
from app.auth import NOT_FOUND, get_conn, require_self
from app.schemas import (
    ConfirmStatus,
    ConversationDraft,
    Place,
    Role,
    ShareScope,
    Source,
    Statement,
    Theme,
    User,
    UtteranceDraft,
)

router = APIRouter(prefix="/api")

_CONFIRM_TRIGGER = "確認"
_REVIEW_LIMIT = 8
_REVIEW_SOURCES = (Source.SELF_WORDS, Source.AI_GUESS)


class HandoverInput(BaseModel):
    """託す設定の入力。"""

    model_config = ConfigDict(extra="forbid")

    delegate_ids: list[int] = Field(max_length=30)
    themes: list[Theme] = Field(max_length=len(Theme))


class CorrectionInput(BaseModel):
    """本人の訂正の入力。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


def handover_themes(conn: sqlite3.Connection) -> dict[Theme, date]:
    rows = conn.execute("SELECT theme, decided_on FROM handover_themes ORDER BY id").fetchall()
    return {Theme(row["theme"]): date.fromisoformat(row["decided_on"]) for row in rows}


def apply_handover(conn: sqlite3.Connection, self_user: User, at: datetime | None = None) -> int:
    """託すテーマの言葉を「意思決定支援用」の行にする。足した行の数を返す。

    本人が決めたテーマに、新しい言葉を当てはめる処理。お休みの言葉は本人だけのまま。
    「専門職だけ」の行（アセスメントなど専門職の内側の資料から残した記録）は変えない。意思決定支援用にすると
    託された家族に見えてしまうため（CLAUDE.md の「守ること」、tyumajinさんの決定、2026-11-08）。
    託すのをやめたとき（_narrow_theme）も意思決定支援用の行だけを戻すので、「専門職だけ」の行は専門職に見えたまま残る。
    関連するテスト：tests/test_handover.py の test_専門職だけの記録は_託すテーマに入っても家族には見えない
    """
    themes = handover_themes(conn)
    if not themes:
        return 0
    moved = 0
    with ledger.atomic(conn):
        for statement in ledger.current_statements(conn):
            if (
                statement.theme in themes
                and statement.share_scope not in (ShareScope.DECISION_SUPPORT, ShareScope.PROFESSIONAL_ONLY)
                and statement.confirm_status is not ConfirmStatus.RESTING
            ):
                ledger.change_share_scope(
                    conn,
                    statement.id,
                    actor_user_id=self_user.id,
                    share_scope=ShareScope.DECISION_SUPPORT,
                    at=at,
                )
                moved += 1
    return moved


def _narrow_theme(conn: sqlite3.Connection, self_user: User, theme: Theme, at: datetime) -> None:
    """託すのをやめたテーマの言葉を「本人だけ」に戻す。"""
    for statement in ledger.current_statements(conn):
        if statement.theme is theme and statement.share_scope is ShareScope.DECISION_SUPPORT:
            # 今の様子は専門職と家族が書いたものなので、本人の託す設定では変えない
            if statement.source is not Source.OBSERVATION:
                ledger.change_share_scope(
                    conn, statement.id, actor_user_id=self_user.id, share_scope=ShareScope.SELF_ONLY, at=at
                )


def _delegates(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute(
        "SELECT DISTINCT delegate_user_id AS id FROM delegations WHERE scope = ?",
        (ShareScope.DECISION_SUPPORT.value,),
    ).fetchall()
    users = [db.get_user(conn, row["id"]) for row in rows]
    return [user for user in users if user is not None and user.removed_at is None]


def _handover_view(conn: sqlite3.Connection) -> dict:
    themes = handover_themes(conn)
    delegates = _delegates(conn)
    family = [user for user in db.list_users(conn) if user.role is Role.FAMILY and not user.is_deceased]
    days = [*themes.values()]
    return {
        "themes": [theme.value for theme in themes],
        "all_themes": [theme.value for theme in Theme],
        "delegate_ids": [user.id for user in delegates if user.role is Role.FAMILY],
        "family": [{"id": user.id, "name": user.display_name} for user in family],
        # 今だれに見えているかを本人が分かるように、招かれた専門職の名前を返す
        "professionals": [user.display_name for user in delegates if user.role is Role.PROFESSIONAL],
        "decided_on": max(days).isoformat() if days else None,
    }


@router.get("/handover")
def read_handover(_: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """託す設定を返す。本人だけが開ける。"""
    return _handover_view(conn)


@router.put("/handover")
def save_handover(
    body: HandoverInput,
    request: Request,
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """託す人とテーマを本人が決める。選んだテーマの今の言葉に、新しい行で範囲を広げる。"""
    now = clock.now(request.app)
    family_ids = {user.id for user in db.list_users(conn) if user.role is Role.FAMILY and not user.is_deceased}
    if set(body.delegate_ids) - family_ids:
        raise HTTPException(status_code=400, detail="託せるのは家族だけです。")
    chosen = set(body.themes)
    with ledger.atomic(conn):
        current = {user.id for user in _delegates(conn) if user.role is Role.FAMILY}
        for user_id in set(body.delegate_ids) - current:
            conn.execute(
                "INSERT INTO delegations (delegate_user_id, scope, decided_on) VALUES (?, ?, ?)",
                (user_id, ShareScope.DECISION_SUPPORT.value, now.date().isoformat()),
            )
        for user_id in current - set(body.delegate_ids):
            conn.execute("DELETE FROM delegations WHERE delegate_user_id = ?", (user_id,))
        before = handover_themes(conn)
        for theme in chosen - before.keys():
            conn.execute(
                "INSERT INTO handover_themes (theme, decided_on) VALUES (?, ?)",
                (theme.value, now.date().isoformat()),
            )
        for theme in before.keys() - chosen:
            conn.execute("DELETE FROM handover_themes WHERE theme = ?", (theme.value,))
            _narrow_theme(conn, viewer, theme, now)
        apply_handover(conn, viewer, at=now)
    return _handover_view(conn)


def _review_view(conn: sqlite3.Connection, statement: Statement) -> dict:
    origin = None
    origin_is_self = False
    origin_speaker = None
    # 推測には、もとの発言を添える。日付だけでは、本人が何の話か思い出せないため
    if statement.source is Source.AI_GUESS and statement.source_utterance_ids:
        utterance = ledger.get_utterance(conn, statement.source_utterance_ids[0])
        origin = utterance.text if utterance else None
        # 推測のもとは家族のコメントのこともある。札は話した人から決める（原則3）
        origin_is_self = utterance is not None and utterance.speaker_user_id == statement.subject_user_id
        # 本人以外の言葉なら、だれの言葉かを添える。「ほかの人の言葉」だけでは出どころが分からないため（原則3）
        if utterance is not None and not origin_is_self and utterance.speaker_user_id is not None:
            speaker = db.get_user(conn, utterance.speaker_user_id)
            origin_speaker = speaker.display_name if speaker else None
    return {
        "id": statement.id,
        "body": statement.body,
        "origin": origin,
        "origin_is_self": origin_is_self,
        "origin_speaker": origin_speaker,
        "source": statement.source.value,
        "effective_date": statement.effective_date.isoformat(),
        "is_guess": statement.source is Source.AI_GUESS,
    }


def _varied(items: list[Statement]) -> list[Statement]:
    """同じテーマが続かないよう、順を保ったままテーマごとに1件ずつ取り出す。

    ほぼ同じ趣旨の推測に何度も答えることにならないようにするため（原則6）。
    関連するテスト：tests/test_handover.py の test_確かめの一覧は同じテーマが続かない
    """
    groups: dict[Theme, list[Statement]] = {}
    for item in items:
        groups.setdefault(item.theme, []).append(item)
    varied: list[Statement] = []
    while groups:
        for theme in list(groups):
            varied.append(groups[theme].pop(0))
            if not groups[theme]:
                del groups[theme]
    return varied


@router.get("/review")
def read_review(
    request: Request, _: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """「今日うかがったこと」の一覧。まだ確かめていない本人の言葉とAIの推測を返す。"""
    items = [
        statement
        for statement in ledger.current_statements(conn)
        if statement.confirm_status is ConfirmStatus.UNCONFIRMED and statement.source in _REVIEW_SOURCES
    ]
    # 今日の話を先に、次に推測を出す。本人が答えられるようにするため（原則4）。
    # 推測だけを先にすると、古い推測で上限が埋まり、今日話した言葉を確かめられなくなる
    today = clock.now(request.app).date()
    items.sort(
        key=lambda item: (item.effective_date == today, item.source is Source.AI_GUESS, item.effective_date),
        reverse=True,
    )
    return {"items": [_review_view(conn, item) for item in _varied(items)[:_REVIEW_LIMIT]]}


def _reviewable(conn: sqlite3.Connection, statement_id: int) -> Statement:
    statement = ledger.get_statement(conn, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if ledger.head_of(conn, statement_id).id != statement_id:
        raise HTTPException(status_code=409, detail="この言葉は、もう確かめてあります。画面を開き直してください。")
    return statement


@router.post("/statements/{statement_id}/confirm")
def confirm(
    request: Request,
    statement_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """「そうです」。確かめた行を足す。"""
    _reviewable(conn, statement_id)
    return _review_view(
        conn, ledger.confirm_statement(conn, statement_id, actor_user_id=viewer.id, at=clock.now(request.app))
    )


@router.post("/statements/{statement_id}/rest")
def rest(
    request: Request,
    statement_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """「今は話したくない」。本人だけに戻した行を足す。"""
    _reviewable(conn, statement_id)
    return _review_view(conn, ledger.rest_statement(conn, statement_id, actor_user_id=viewer.id, at=clock.now(request.app)))


def _confirm_conversation(conn: sqlite3.Connection, day: date) -> int:
    """その日の確認の会話を返す。部屋がないので、裏の分割の対象にならない。"""
    row = conn.execute(
        "SELECT id FROM conversations WHERE room_id IS NULL AND trigger = ? AND conversation_date = ?",
        (_CONFIRM_TRIGGER, day.isoformat()),
    ).fetchone()
    if row:
        return row["id"]
    draft = ConversationDraft(conversation_date=day, trigger=_CONFIRM_TRIGGER, life_stage=ledger.life_stage_on(conn, day))
    return ledger.add_conversation(conn, draft).id


@router.post("/statements/{statement_id}/correct")
def correct(
    body: CorrectionInput,
    request: Request,
    statement_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """「ちがいます」。本人の言葉を発言として残し、訂正の行で元の行を指す。元の行は履歴に残る。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="修正する言葉を入れてください。")
    _reviewable(conn, statement_id)
    now = clock.now(request.app)
    # 訂正の本文は発言の全文なので、改行や区切りの確かめに通らないときは分かる言葉で返す
    problem = verbatim.quote_problem(text, text)
    if problem:
        raise HTTPException(status_code=400, detail="言葉を短くして、もう一度お願いします。")
    with ledger.atomic(conn):
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=_confirm_conversation(conn, now.date()),
                speaker_user_id=viewer.id,
                text=text,
                spoken_at=now,
                place=Place.DIARY_AND_TALK,
            ),
        )
        corrected = ledger.correct_statement(
            conn,
            statement_id,
            actor_user_id=viewer.id,
            utterance_id=utterance.id,
            body=text,
            effective_date=now.date(),
            at=now,
        )
    return _review_view(conn, corrected)
