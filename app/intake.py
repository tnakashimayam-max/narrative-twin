"""言明への分割の入口。

役割：AI の層（app/ai.py）と決まった手順の層（app/splitter.py）をつなぎ、発言を言明に分けて保存する。
4つの極：3. 読み解き、積み上げる。
守る原則：1（判断は人が行う）、9（本人の言葉は一字も変えない）。

呼び出しのモデルとトークン数は ai_calls の表に残す。ふだんは会話を閉じたあとや、スレッドとグループの
発言のあとに裏で動く（app/jobs.py）。

関連するテスト：tests/test_intake.py、tests/test_jobs.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app import ai, config, db, splitter
from app.auth import NOT_FOUND, get_conn, require_self
from app.schemas import SplitTargetKind, User

router = APIRouter(prefix="/api")

AI_FAILED_MESSAGE = "いま、うまくお答えできませんでした。少し時間をおいて、もう一度お試しください。"
NO_MODEL_SUMMARY = "まだありません。"

_AI_ERRORS = (
    config.MissingApiKeyError,
    ai.AIResultError,
    ai.AIRefusedError,
    ai.AIUnavailableError,
)
_SPENT_ERRORS = (ai.AIResultError, ai.AIRefusedError)


def split_and_save(
    conn: sqlite3.Connection,
    *,
    target: str,
    place: str,
    utterances: list,
    model_summary: str = NO_MODEL_SUMMARY,
    letter_body: str | None = None,
    earlier: list | None = None,
    mark: tuple[SplitTargetKind, int, int] | None = None,
    client=None,
) -> splitter.SplitReport:
    """発言を言明に分けて保存する。本文に問題があれば、理由を伝えて1回だけやり直す。

    1回だけにするのは、費用と待ち時間を読めるようにするため。残った問題の言明は捨てて数える。
    """
    users = splitter.people_in(conn, [*(earlier or []), *utterances])
    self_user = db.self_user(conn)
    self_name = self_user.name if self_user else "本人"
    transcript = splitter.format_transcript(
        place, splitter.first_spoken_on(utterances), utterances, users, letter_body, earlier
    )

    def _call(feedback: str | None = None):
        try:
            result = ai.split_utterances(transcript, model_summary, feedback, client=client, self_name=self_name)
        except _SPENT_ERRORS as error:
            db.log_ai_call(
                conn,
                role="言明への分割",
                model=error.model,
                input_tokens=error.input_tokens,
                output_tokens=error.output_tokens,
                target=target,
            )
            raise
        db.log_ai_call(
            conn,
            role="言明への分割",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=target,
            seconds=result.seconds,
        )
        return result

    result = _call()
    problems = splitter.quote_problems(result.value, utterances)
    if problems:
        result = _call("\n".join(problems))
    return splitter.save_split(conn, result.value, utterances, users, mark=mark)


def _report_body(report: splitter.SplitReport) -> dict:
    return {
        "statement_count": len(report.statement_ids),
        "interpretation_count": report.interpretation_count,
        "question_count": report.question_count,
        "dropped_question_count": report.dropped_question_count,
        "dropped": [{"body": item.body, "reason": item.reason} for item in report.dropped],
    }


def _split_route(
    request: Request, conn: sqlite3.Connection, kind: SplitTargetKind, target_id: int
) -> dict:
    # jobs は split_and_save を使うので、ここで読み込む。ファイルの先頭で読むと輪になるため。
    from app import jobs

    try:
        report = jobs.split_new(request.app, conn, kind, target_id)
    except _AI_ERRORS as error:
        raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
    return _report_body(report)


@router.post("/conversations/{conversation_id}/statements")
def split_conversation(
    request: Request,
    conversation_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """会話の、まだ分けていない発言を言明に分ける。新しい発言がなければ 0 件の報告を返す。"""
    if conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return _split_route(request, conn, SplitTargetKind.CONVERSATION, conversation_id)


@router.post("/letters/{letter_id}/statements")
def split_thread(
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    _: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """スレッドの、まだ分けていない発言を言明に分ける。新しい発言がなければ 0 件の報告を返す。"""
    if conn.execute("SELECT 1 FROM letters WHERE id = ?", (letter_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return _split_route(request, conn, SplitTargetKind.LETTER, letter_id)
