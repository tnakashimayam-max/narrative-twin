"""資料についてのチャット。

役割：専門職が渡した資料（ケアプラン、アセスメントなど）について、専門職がツイン（AI）に問い、
     ツインが本人として代弁で答える。資料の画面で、元の PDF とレビューの横に出す。
4つの極：4. 10年後に代弁する。
守る原則：1（決めるのは人）、3（資料は書いた人の文で、本人の言葉と分ける）、9（本人の言葉は記録の本文のまま）、
          16（言葉を並べず代弁で答える）、17（診断と服薬の指示はしない）。

答えの作り方は「ツインに聞く」の代弁と同じ（app/advocacy_voice.py の gather と speak）。違うのは、問いに
その資料の要点、ケアプランの中身、読んだ項目、ツインのレビューを添えること。資料は書いた人の文なので、
本人の思いとしては渡さない（話した人と対象を分ける）。問いと答えは資料ごと、聞いた人ごとに残し、資料の画面で読み直せる。

関連するテスト：tests/test_document_chat.py
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import advocacy, advocacy_voice, clock, db, ledger, professional_records
from app.auth import current_user, get_conn
from app.schemas import DocumentStatus, InferenceKind, RequestKind, User

router = APIRouter(prefix="/api/advocacy")

MAX_QUESTION_CHARS = 1000
# 問いに添える項目の数。資料が長くても読む量を増やさないため
_ITEMS_IN_TASK = 15
# 次の答えに渡す、同じ資料の直前のやり取りの数と、1つの答えの字数。続けた聞き方に答えられるようにし、読む量は一定に保つ
_PREVIOUS_TURNS = 3
_PREVIOUS_CLAIM_CHARS = 200


class DocumentChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    client_key: str = Field(min_length=1, max_length=64)


def document_task(question: str, document: dict, review: dict | None, previous: list[dict] | None = None) -> str:
    """代弁の問いに、資料の中身とレビューと、同じ資料の直前のやり取りを添える。資料は書いた人の文として、見出しで分けて渡す。

    直前のやり取りは、「さっきの答えをもう少し」のような続けた聞き方に答えるため（tyumajinさんの依頼）。
    前の答えはツインの推測なので、本人の言葉としては渡さない。
    関連するテスト：tests/test_document_chat.py の test_資料についての問いには資料の中身とレビューを添えて代弁を作る、
    test_同じ資料の直前のやり取りを次の答えに渡す
    """
    head = (
        f"種類：{document['kind']}／題：{document.get('title') or ''}／書いた人：{document.get('author') or '不明'}"
        f"／日付：{document.get('document_date') or '不明'}"
    )
    lines = [
        question,
        "",
        "## この問いは、専門職が渡した次の資料についてです（資料を書いた人の文で、本人の言葉ではありません）",
        head,
        f"要点：{document.get('summary') or ''}",
    ]
    plan = document.get("care_plan")
    if plan:
        lines.append("ケアプランの中身：" + json.dumps(plan, ensure_ascii=False))
    items = document.get("items") or []
    if items:
        lines.append("資料から読んだ項目：")
        lines.extend(f"- {item['kind']}：{item['text']}" for item in items[:_ITEMS_IN_TASK])
    if review:
        lines.append("")
        lines.append("## この資料へのツイン（AI）のレビュー（前に書いたもの）")
        lines.append(review.get("overview", ""))
        lines.extend(f"- 気になるところ：{concern.get('text', '')}" for concern in review.get("concerns", []))
    if previous:
        lines.append("")
        lines.append("## この資料について、前に聞かれたことと、ツイン（AI）の答え（古い順。答えはツインの推測で、本人の言葉ではありません）")
        for turn in previous:
            claim = ((turn.get("voice") or {}).get("claim") or "")[:_PREVIOUS_CLAIM_CHARS]
            lines.append(f"- 問い：{turn['question']}")
            lines.append(f"  答え：{claim}")
    return "\n".join(lines)


def _turn_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["log_id"],)).fetchone()
    voice = {**json.loads(log["result"]), "label": advocacy_voice.LABEL} if log else None
    return {"id": row["id"], "question": row["question"], "created_at": row["created_at"], "voice": voice}


def _readable_document(conn: sqlite3.Connection, viewer: User, document_id: int) -> sqlite3.Row:
    """問える資料を返す。資料を扱える専門職だけ。名前の確かめ待ちの資料は、本人の資料と確かめるまで問えない。"""
    professional_records._require_document_tool(conn, viewer)
    row = professional_records._document_row(conn, document_id)
    if row["status"] == DocumentStatus.NAME_CHECK.value:
        raise HTTPException(status_code=400, detail="資料の名前を確かめてから、ツインに聞けます。")
    return row


@router.get("/documents/{document_id}/chat")
def read_document_chat(
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """この資料について、この人が聞いた問いと答えを古い順に返す。AI は呼ばない。ほかの人のやり取りは返さない。"""
    _readable_document(conn, viewer, document_id)
    rows = conn.execute(
        "SELECT * FROM document_chats WHERE document_id = ? AND asker_user_id = ? ORDER BY rowid", (document_id, viewer.id)
    ).fetchall()
    return {"items": [_turn_view(conn, row) for row in rows]}


@router.post("/documents/{document_id}/chat")
def ask_about_document(
    body: DocumentChatInput,
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """資料について問い、ツインが本人として代弁で答える。同じ client_key なら AI を呼ばずに前の答えを返す。"""
    row = _readable_document(conn, viewer, document_id)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="問いを書いてください。")
    with request.app.state.voice_lock:
        done = conn.execute(
            "SELECT * FROM document_chats WHERE asker_user_id = ? AND client_key = ?", (viewer.id, body.client_key)
        ).fetchone()
        if done is not None:
            return {"turn": _turn_view(conn, done)}
        if not advocacy_voice.has_material(conn, viewer):
            raise HTTPException(status_code=400, detail=advocacy.NOT_YET)
        document = professional_records._document_view(conn, row)
        recent = conn.execute(
            "SELECT * FROM document_chats WHERE document_id = ? AND asker_user_id = ? ORDER BY rowid DESC LIMIT ?",
            (document_id, viewer.id, _PREVIOUS_TURNS),
        ).fetchall()
        previous = [_turn_view(conn, item) for item in reversed(recent)]
        task = document_task(question, document, professional_records.review_of(conn, document_id), previous)
        # 健康や病気の問いでも止めない。診断と服薬の指示をしない決まりを添えるだけ（原則17）
        health = advocacy.request_kind_from_words(question) is RequestKind.HEALTH
        now = clock.now(request.app)
        try:
            material = advocacy_voice.gather(conn, viewer, task, health=health)
            view, result = advocacy_voice.speak(conn, material, today=now.date())
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=advocacy_voice.AI_FAILED_MESSAGE) from error
        log_id = advocacy._log_inference(
            conn, InferenceKind.ADVOCACY, question, view, material.elements, advocacy_voice.used_statement_ids(view), result
        )
        with ledger.atomic(conn):
            chat_id = db.insert_row(
                conn,
                "document_chats",
                {
                    "document_id": document_id,
                    "asker_user_id": viewer.id,
                    "question": question,
                    "client_key": body.client_key,
                    "log_id": log_id,
                    "created_at": now.isoformat(),
                },
            )
        saved = conn.execute("SELECT * FROM document_chats WHERE id = ?", (chat_id,)).fetchone()
    return {"turn": _turn_view(conn, saved)}
