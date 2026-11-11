"""今の様子の入力（決まった手順で動く層）。AI は呼ばない。

役割：専門職と託された家族が見た今の様子を、言明として残す。
4つの極：4. 10年後に代弁する。
守る原則：2（今の本人を最優先する）、3（今の様子は本人の言葉と分ける）。

言明には根拠の発言が要るので、部屋のない「今の様子」の会話と発言を一緒に入れる。部屋がないので、
裏の分割の対象にならない。範囲は「意思決定支援用」にする。本人の託す設定で狭める道はない。人が見て書いた
記録で、本人の言葉ではないため。この限界は docs/decisions.md に書いている。

関連するテスト：tests/test_advocacy.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import clock, consent, db, ledger
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import (
    ConversationDraft,
    Period,
    Place,
    Role,
    ShareScope,
    Source,
    Statement,
    StatementDraft,
    Theme,
    User,
    UtteranceDraft,
)

router = APIRouter(prefix="/api")

TRIGGER = "今の様子"


class ObservationInput(BaseModel):
    """今の様子の入力。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    theme: Theme = Theme.HOME
    # 会議の画面から入れたか。発言の場所に残し、会議で見た様子だと後からたどれるようにする
    at_meeting: bool = False


def can_use_supporter_screen(conn: sqlite3.Connection, viewer: User) -> bool:
    """支援者の画面を使えるかを返す。招かれた専門職と、立場が家族の託された人。"""
    if viewer.role not in {Role.PROFESSIONAL, Role.FAMILY}:
        return False
    return ShareScope.DECISION_SUPPORT in consent.visible_scopes(conn, viewer)


def save_observation(
    conn: sqlite3.Connection,
    speaker: User,
    self_user: User,
    text: str,
    theme: Theme,
    at: datetime,
    place: Place = Place.CHAT,
    trigger: str = TRIGGER,
    share_scope: ShareScope = ShareScope.DECISION_SUPPORT,
) -> Statement:
    """今の様子を、会話、発言、言明の順に1つの処理で入れる。

    trigger は会話のきっかけ。専門職がチャットで伝えたことと資料から読んだことは、別のきっかけで残す
    （app/professional_records.py）。どこから来た様子かを、あとからたどれるようにするため。
    share_scope は、アセスメントなど専門職だけに見せる資料から残すときに「専門職だけ」にする。
    """
    with ledger.atomic(conn):
        conversation = ledger.add_conversation(
            conn,
            ConversationDraft(
                conversation_date=at.date(),
                trigger=trigger,
                theme=theme,
                closed_at=at,
                life_stage=ledger.life_stage_on(conn, at.date()),
            ),
        )
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation.id,
                speaker_user_id=speaker.id,
                text=text,
                spoken_at=at,
                place=place,
            ),
        )
        return ledger.append_statement(
            conn,
            StatementDraft(
                source_utterance_ids=[utterance.id],
                subject_user_id=self_user.id,
                speaker_user_id=speaker.id,
                source=Source.OBSERVATION,
                body=text,
                period=Period.NOW,
                theme=theme,
                share_scope=share_scope,
                effective_date=at.date(),
                created_at=at,
            ),
        )


def now_view(conn: sqlite3.Connection, viewer: User, limit: int) -> list[dict]:
    """今の様子を新しい順に返す。見える範囲は consent が決める。"""
    items = [item for item in consent.visible_statements(conn, viewer) if item.source is Source.OBSERVATION]
    # 台帳は入れた順（作成日時、追記の順）に返る。日付と作成日時で新しい順に並べ、同じ日は新しく入れた様子を先にする（原則2）。
    # 1つの書き込みから分けた文は作成日時が同じなので、並べ替えが元の順を保ち、書き込みの文の順のまま出る。
    # 逆にしてから並べると、あとの文（「蓋は、誠一さんが開けました。」など）だけが前の文なしで先頭に出ていた。
    # 関連するテスト：tests/test_advocacy.py の test_1つの書き込みから分けた今の様子は_書き込みの文の順に出る
    items.sort(key=lambda item: (item.effective_date, item.created_at), reverse=True)
    views = []
    for item in items[:limit]:
        speaker = db.get_user(conn, item.speaker_user_id)
        views.append(
            {
                "statement_id": item.id,
                "body": item.body,
                "date": item.effective_date.isoformat(),
                "speaker": speaker.display_name if speaker else "はずした人",
                # 「母は」と書かれた記録を読めるように、話した人の続柄を添える
                "speaker_label": f"{speaker.relation}の{speaker.display_name}" if speaker and speaker.relation else None,
                "source": item.source.value,
            }
        )
    return views


@router.post("/observations", status_code=201)
def add_observation(
    body: ObservationInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """今の様子を入れる。招かれた専門職と、立場が家族の託された人ができる。"""
    if not can_use_supporter_screen(conn, viewer):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="今の様子を入れてください。")
    self_user = db.self_user(conn)
    place = Place.MEETING if body.at_meeting else Place.CHAT
    statement = save_observation(conn, viewer, self_user, text, body.theme, clock.now(request.app), place)
    return {"statement_id": statement.id}
