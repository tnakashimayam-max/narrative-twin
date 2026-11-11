"""言明への分割の入口。

役割：AI の層（app/ai.py）と決まった手順の層（app/splitter.py）をつなぎ、発言を言明に分けて保存する。
4つの極：3. 読み解き、積み上げる。
守る原則：1（判断は人が行う）、9（本人の言葉は一字も変えない）。

呼び出しのモデルとトークン数は ai_calls の表に残す。ふだんは会話を閉じたあとや、スレッドとグループの
発言のあとに裏で動く（app/jobs.py）。

発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択する（app/twin_model.py の
reason_to_split_deep）。選ぶ理由と決まりの表は docs/tools.md の「重い言葉の分割でモデルを選ぶ」にある。

関連するテスト：tests/test_intake.py、tests/test_twin.py、tests/test_twin_model.py
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app import ai, config, db, splitter, twin, twin_model
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
# 選んだモデルが答えられなかったと見て、Haiku 5.5 へ切り替える失敗。API キーがないときは
# どのモデルでも呼べないので入れない。
_DEEP_FAILED_ERRORS = (*_SPENT_ERRORS, ai.AIUnavailableError)


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
    """発言を言明に分けて保存する。本文に問題があるか、本人の発言やその一部の文が言明になっていなければ、理由を伝えて1回だけやり直す。

    やり直しても言明にならない本人の発言は、本文を入れずに記録に残し、数えられるようにする。
    1回だけにするのは、費用と待ち時間を読めるようにするため。残った問題の言明は捨てて数える。

    発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択する。見るのは今回分ける本人の発言だけで、
    earlier は文脈として渡すだけなので見ない。やり直しは答えたモデルと同じモデルで行う。
    選んだモデルが答えられないときは Haiku 5.5 の答えを使う。何も残らないより良いため。
    Haiku 5.5 も答えられなければ例外のまま上げる。印は進まないので、次の処理で流し直せる。
    """
    users = splitter.people_in(conn, [*(earlier or []), *utterances])
    self_user = db.self_user(conn)
    self_name = self_user.name if self_user else "本人"
    transcript = splitter.format_transcript(
        place, splitter.first_spoken_on(utterances), utterances, users, letter_body, earlier
    )
    self_words = [item.text for item in utterances if self_user and item.speaker_user_id == self_user.id]
    deep = twin_model.reason_to_split_deep(place, self_words, twin.detect_care) is not None

    def _ask(feedback: str | None, *, use_deep: bool, harder: bool = False):
        role = (config.SPLIT_DEEP_RETRY_ROLE if harder else config.SPLIT_DEEP_ROLE) if use_deep else "言明への分割"
        try:
            result = ai.split_utterances(
                transcript, model_summary, feedback, client=client, self_name=self_name, deep=use_deep, harder=harder
            )
        except _SPENT_ERRORS as error:
            db.log_ai_call(
                conn,
                role=role,
                model=error.model,
                input_tokens=error.input_tokens,
                output_tokens=error.output_tokens,
                target=target,
            )
            raise
        db.log_ai_call(
            conn,
            role=role,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=target,
            seconds=result.seconds,
        )
        return result

    def _call(feedback: str | None = None, *, harder: bool = False):
        nonlocal deep
        if deep:
            try:
                return _ask(feedback, use_deep=True, harder=harder)
            except _DEEP_FAILED_ERRORS:
                # ここから先は Haiku 5.5 が答えるので、本文のやり直しも Haiku 5.5 で行う
                deep = False
        return _ask(feedback, use_deep=False)

    result = _call()
    problems = splitter.quote_problems(result.value, utterances)
    missing = splitter.missing_self_problems(result.value, utterances, self_user)
    partial = splitter.partial_self_problems(result.value, utterances, self_user)
    if problems or missing or partial:
        # 本人の発言や文が落ちたときだけ深くする。同じ深さでやり直すと、同じ答えが返るため（理由は app/config.py）
        result = _call("\n".join([*problems, *missing, *partial]), harder=bool(missing or partial))
    report = splitter.save_split(conn, result.value, utterances, users, mark=mark)
    for _, utterance in splitter.missing_self_lines(result.value, utterances, self_user):
        report.dropped.append(splitter.DroppedStatement(utterance.text, splitter.MISSING_SELF_WORDS))
    for _, _, left in splitter.partial_self_lines(result.value, utterances, self_user):
        report.dropped.extend(splitter.DroppedStatement(piece, splitter.MISSING_SELF_SENTENCE) for piece in left)
    return report


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
