"""写真の問いと言明への分割の入口。

役割：AI の層（app/ai.py）と決まった手順の層（app/splitter.py）をつなぎ、API として出す。
4つの極：1. 書く・話す（写真の問い）、3. 読み解き、積み上げる（言明への分割）。
守る原則：1（判断は人が行う）、8（架空データ）、9（本人の言葉は一字も変えない）。

AI の呼び出しがうまくいかないときは、理由を問わず同じ分かりやすいメッセージを返す。
画面を使う人にとって、API のエラーの番号には意味がないため。
呼び出しのモデルとトークン数は ai_calls の表に残す。

関連するテスト：tests/test_intake.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool

from app import ai, config, db, splitter
from app.auth import current_user, get_conn
from app.schemas import Role, User

router = APIRouter(prefix="/api")

AI_FAILED_MESSAGE = "いま、うまくお答えできませんでした。少し時間をおいて、もう一度お試しください。"
PHOTO_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Claude API が受け付ける1枚の大きさの上限
MAX_PHOTO_BYTES = 5 * 1024 * 1024
NO_MODEL_SUMMARY = "まだありません。"

_AI_ERRORS = (
    config.MissingApiKeyError,
    ai.AIResultError,
    ai.AIRefusedError,
    ai.AIUnavailableError,
)
_SPENT_ERRORS = (ai.AIResultError, ai.AIRefusedError)


def _self_only(viewer: User) -> None:
    """本人だけが使える道。本人以外には、道がないのと同じ応答にする。"""
    if viewer.role is not Role.SELF:
        raise HTTPException(status_code=404, detail="見つかりません")


def split_and_save(
    conn: sqlite3.Connection,
    *,
    target: str,
    place: str,
    utterances: list,
    model_summary: str = NO_MODEL_SUMMARY,
    letter_body: str | None = None,
    client=None,
) -> splitter.SplitReport:
    """発言を言明に分けて保存する。本文に問題があれば、理由を伝えて1回だけやり直す。

    1回だけにするのは、費用と待ち時間を読めるようにするため。残った問題の言明は捨てて数える。
    """
    users = db.list_users(conn)
    transcript = splitter.format_transcript(
        place, splitter.first_spoken_on(utterances), utterances, users, letter_body
    )

    def _call(feedback: str | None = None):
        try:
            result = ai.split_utterances(transcript, model_summary, feedback, client=client)
        except _SPENT_ERRORS as error:
            _log_failed_call(conn, "言明への分割", error, target)
            raise
        db.log_ai_call(
            conn,
            role="言明への分割",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=target,
        )
        return result

    result = _call()
    problems = splitter.quote_problems(result.value, utterances)
    if problems:
        result = _call("\n".join(problems))
    return splitter.save_split(conn, result.value, utterances, users)


def _log_failed_call(conn: sqlite3.Connection, role: str, error, target: str | None = None) -> None:
    """答えが得られなかった呼び出しも、費用がかかったので記録する。"""
    db.log_ai_call(
        conn,
        role=role,
        model=error.model,
        input_tokens=error.input_tokens,
        output_tokens=error.output_tokens,
        target=target,
    )


def _report_body(report: splitter.SplitReport) -> dict:
    return {
        "statement_count": len(report.statement_ids),
        "interpretation_count": report.interpretation_count,
        "question_count": report.question_count,
        "dropped_question_count": report.dropped_question_count,
        "dropped": [{"body": item.body, "reason": item.reason} for item in report.dropped],
    }


def _split_route(
    conn: sqlite3.Connection,
    target: str,
    place: str,
    utterances: list,
    letter_body: str | None = None,
) -> dict:
    if not utterances:
        raise HTTPException(status_code=404, detail="見つかりません")
    if splitter.already_split(conn, utterances):
        raise HTTPException(status_code=409, detail="この記録は、もう言明に分けてあります。")
    try:
        report = split_and_save(
            conn, target=target, place=place, utterances=utterances, letter_body=letter_body
        )
    except _AI_ERRORS as error:
        raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
    return _report_body(report)


@router.post("/photo-question")
async def photo_question(
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, str]:
    """写真を1枚受け取り、本人に問いかける文を1つ返す。本文に写真そのものを送る。"""
    _self_only(viewer)
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type not in PHOTO_TYPES:
        raise HTTPException(
            status_code=400, detail="この形式の写真は読めません。JPEG、PNG、GIF、WebP をお使いください。"
        )
    image = await request.body()
    if not image:
        raise HTTPException(status_code=400, detail="写真が届いていません。もう一度お試しください。")
    if len(image) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="写真が大きすぎます。5MB までの写真をお使いください。")
    try:
        # 待つ間にほかの要求を止めないよう、別のスレッドで呼ぶ
        result = await run_in_threadpool(ai.ask_about_photo, image, media_type)
    except _AI_ERRORS as error:
        if isinstance(error, _SPENT_ERRORS):
            _log_failed_call(conn, "写真の問い", error)
        raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
    db.log_ai_call(
        conn,
        role="写真の問い",
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    return {"question": result.value.question}


@router.post("/conversations/{conversation_id}/statements")
def split_conversation(
    conversation_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """会話の発言を言明に分ける。同じ会話は1回だけ処理する。"""
    _self_only(viewer)
    utterances = splitter.load_utterances(conn, conversation_id=conversation_id)
    place = utterances[0].place.value if utterances else ""
    return _split_route(conn, f"会話 {conversation_id}", place, utterances)


@router.post("/letters/{letter_id}/statements")
def split_thread(
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """スレッドの発言を言明に分ける。同じスレッドは1回だけ処理する。"""
    _self_only(viewer)
    row = conn.execute("SELECT body FROM letters WHERE id = ?", (letter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="見つかりません")
    utterances = splitter.load_utterances(conn, letter_id=letter_id)
    return _split_route(conn, f"便り {letter_id}", "スレッド", utterances, row["body"])
