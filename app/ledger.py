"""言明の台帳（決まった手順で動く層）。

役割：発言と言明を追記し、訂正の連鎖の先頭（いま有効な行）を求める。会話、便り、コメントも
      ここから入れる。データの読み込みもここを通るので、決まりをすり抜けない。AIは呼ばない。
4つの極：3. 読み解き、積み上げる。
守る原則：3（出どころを分ける）、5（訂正しても元の記録を消さない）、9（一字も変えない）。

この製品は本人の意思を長く預かる記録として作る。本人の言葉は作り直せないので上書きしない。
そのため、ここには追記と読み取りの関数だけを置き、更新と削除の関数を作らない。
訂正も新しい行として追記し、replaces_id で元の行を指す。

「いま有効か」「見せるか」の判断は head_of で求めた先頭の行で行う。根拠として指された番号は
置き換える前の行であることが多いので、その行のまま判断すると訂正前の内容で動いてしまう。

関連するテスト：tests/test_ledger.py、tests/test_ids.py
全体の説明：docs/architecture.md
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime

from app import db, verbatim
from app.schemas import (
    Comment,
    CommentDraft,
    ConfirmStatus,
    Conversation,
    ConversationDraft,
    Interpretation,
    InterpretationStatus,
    Letter,
    LetterDraft,
    Period,
    Perspective,
    Petal,
    Place,
    Role,
    ShareScope,
    Source,
    Statement,
    StatementDraft,
    Theme,
    Utterance,
    UtteranceDraft,
)

# 発言の原文の抜き出しでなければならない出どころ。ツインが本人の言葉として届ける文の元になる。
_VERBATIM_SOURCES = {Source.SELF_WORDS, Source.SELF_CORRECTION}

# ツインは役割の決めた言葉にないので、話し手の表の中だけの印で表す
_TWIN = "ツイン"

# 出どころごとに書ける話し手。出どころを本物らしく偽れないようにする。
# 今の様子は人が見て記録するものなので、ツインは書けない。
_ALLOWED_SPEAKERS = {
    Source.SELF_WORDS: {Role.SELF},
    Source.SELF_CORRECTION: {Role.SELF},
    Source.OBSERVATION: {Role.PROFESSIONAL, Role.FAMILY},
    Source.FAMILY_TESTIMONY: {Role.FAMILY},
    Source.CONNECTED_TESTIMONY: {Role.CONNECTED},
    Source.AI_GUESS: {_TWIN},
}


class LedgerError(ValueError):
    """台帳の決まりに合わない追記をしようとしたとき。"""


def add_conversation(conn: sqlite3.Connection, draft: ConversationDraft) -> Conversation:
    """会話を入れる。"""
    with conn:
        conversation_id = db.insert_row(conn, "conversations", draft.model_dump(mode="json"))
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return db.row_to_model(row, Conversation)


def add_letter(conn: sqlite3.Connection, draft: LetterDraft) -> Letter:
    """便りを入れる。"""
    with conn:
        letter_id = db.insert_row(conn, "letters", draft.model_dump(mode="json"))
    row = conn.execute("SELECT * FROM letters WHERE id = ?", (letter_id,)).fetchone()
    return db.row_to_model(row, Letter)


def add_comment(conn: sqlite3.Connection, draft: CommentDraft) -> Comment:
    """コメントを入れる。本文は発言としても保存する。

    発言とコメントは1つの処理で一緒に入れる。片方だけ残ると、原文の控えが宙に浮くため。
    """
    utterance = UtteranceDraft(
        letter_id=draft.letter_id,
        speaker_user_id=draft.author_user_id,
        speaker_is_twin=draft.is_twin_question,
        text=draft.body,
        spoken_at=draft.commented_at,
        place=Place.THREAD,
    )
    try:
        with conn:
            utterance_id = db.insert_row(conn, "utterances", utterance.model_dump(mode="json"))
            comment_id = db.insert_row(
                conn, "comments", {**draft.model_dump(mode="json"), "utterance_id": utterance_id}
            )
    except sqlite3.IntegrityError as error:
        raise LedgerError(f"コメントを入れられません：{error}") from error
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return db.row_to_model(row, Comment)


def add_interpretation(
    conn: sqlite3.Connection,
    *,
    statement_id: int,
    perspective: Perspective,
    viewpoint_role: Role,
    body: str,
    confidence: float,
    caution: str | None,
) -> Interpretation:
    """解釈を、仮説として言明の下に入れる（仮説リーフ）。

    状態を引数で受け取らないのは、AI の結果を「本人が確かめた」として入れさせないため。
    根拠は元の言明1つにする。
    """
    values = Interpretation(
        id=1,
        statement_id=statement_id,
        perspective=perspective,
        viewpoint_role=viewpoint_role,
        body=body,
        confidence=confidence,
        caution=caution,
        evidence_statement_ids=[statement_id],
        status=InterpretationStatus.HYPOTHESIS,
    ).model_dump(mode="json", exclude={"id"})
    with conn:
        interpretation_id = db.insert_row(conn, "interpretations", values)
    row = conn.execute("SELECT * FROM interpretations WHERE id = ?", (interpretation_id,)).fetchone()
    return db.row_to_model(row, Interpretation)


def add_pending_question(
    conn: sqlite3.Connection,
    *,
    question: str,
    theme: Theme | None = None,
    period: Period | None = None,
    petals: list[Petal] | None = None,
) -> None:
    """本人に確かめる問いの候補を入れる。まだ聞いていないので、聞いた日と結果は空にする。"""
    with conn:
        conn.execute(
            "INSERT INTO question_logs (question, theme, period, petals) VALUES (?, ?, ?, ?)",
            (
                question,
                theme.value if theme else None,
                period.value if period else None,
                json.dumps([petal.value for petal in petals or []], ensure_ascii=False),
            ),
        )


def add_utterance(conn: sqlite3.Connection, draft: UtteranceDraft) -> Utterance:
    """発言を追記する。"""
    with conn:
        utterance_id = db.insert_row(conn, "utterances", draft.model_dump(mode="json"))
    return get_utterance(conn, utterance_id)


def get_utterance(conn: sqlite3.Connection, utterance_id: int) -> Utterance | None:
    row = conn.execute("SELECT * FROM utterances WHERE id = ?", (utterance_id,)).fetchone()
    return db.row_to_model(row, Utterance) if row else None


def get_statement(conn: sqlite3.Connection, statement_id: int) -> Statement | None:
    row = conn.execute("SELECT * FROM statements WHERE id = ?", (statement_id,)).fetchone()
    return db.row_to_model(row, Statement) if row else None


def _is_self(conn: sqlite3.Connection, user_id: int | None) -> bool:
    if user_id is None:
        return False
    user = db.get_user(conn, user_id)
    return user is not None and user.role is Role.SELF


def _speaker_is_allowed(conn: sqlite3.Connection, draft: StatementDraft) -> bool:
    """この出どころを、この話し手が書けるかを返す。"""
    allowed = _ALLOWED_SPEAKERS[draft.source]
    if draft.speaker_is_twin:
        return _TWIN in allowed
    user = db.get_user(conn, draft.speaker_user_id)
    return user is not None and user.role in allowed


def _check_draft(conn: sqlite3.Connection, draft: StatementDraft) -> None:
    """追記の前の確かめ。

    本人の言葉は発言の原文の抜き出しに限る。ここを通った文だけが、のちにツインが
    本人の言葉として届ける候補になる。証言と推測は言い換えを含むので、この確かめはしない。
    """
    if not _speaker_is_allowed(conn, draft):
        raise LedgerError(f"この話し手は「{draft.source.value}」を書けません")

    if not draft.source_utterance_ids:
        raise LedgerError("根拠の発言がない言明は入れられません")
    utterances = [get_utterance(conn, number) for number in draft.source_utterance_ids]
    if None in utterances:
        raise LedgerError("根拠の発言が見つかりません")

    # AIの推測のほかは、話した人が根拠の発言の話し手と同じでなければならない。
    # 別の人の発言を、美穂さんの証言などとして記録させないため。
    if draft.source is not Source.AI_GUESS:
        for utterance in utterances:
            if utterance.speaker_is_twin or utterance.speaker_user_id != draft.speaker_user_id:
                raise LedgerError("話した人が、根拠の発言の話し手と合いません")

    if draft.source not in _VERBATIM_SOURCES:
        return

    if len(utterances) != 1:
        raise LedgerError("本人の言葉の根拠の発言は、ちょうど1つです")
    problem = verbatim.quote_problem(utterances[0].text, draft.body)
    if problem:
        raise LedgerError(f"本人の言葉の本文：{problem}")


def _insert(
    conn: sqlite3.Connection, draft: StatementDraft, replaces_id: int | None
) -> Statement:
    _check_draft(conn, draft)
    values = draft.model_dump(mode="json")
    values["created_at"] = values["created_at"] or datetime.now().astimezone().isoformat()
    values["replaces_id"] = replaces_id
    try:
        with conn:
            statement_id = db.insert_row(conn, "statements", values)
    except sqlite3.IntegrityError as error:
        raise LedgerError(f"台帳に追記できません：{error}") from error
    return get_statement(conn, statement_id)


def append_statement(conn: sqlite3.Connection, draft: StatementDraft) -> Statement:
    """言明を1行追記する。確認の状態は未確認から始める。

    確認済みとお休みは本人の操作でだけ、訂正済みは訂正でだけ付く状態なので、
    最初から付けて入れることはできない。他の人の賛同やAIの要約で確認済みにさせないため。
    本人の訂正も、元の行を指す形でしか作らせない（correct_statement を使う）。
    """
    if draft.confirm_status is not ConfirmStatus.UNCONFIRMED:
        raise LedgerError("言明は未確認から始めます")
    if draft.source is Source.SELF_CORRECTION:
        raise LedgerError("本人の訂正は、訂正の処理からだけ作れます")
    return _insert(conn, draft, replaces_id=None)


def correct_statement(
    conn: sqlite3.Connection,
    original_id: int,
    *,
    actor_user_id: int,
    utterance_id: int,
    body: str,
    petals: list[Petal] | None = None,
    effective_date: date | None = None,
) -> Statement:
    """本人の訂正を新しい行として追記し、元の行を指す。元の行はそのまま残る。

    共有範囲は引数で受け取らず、元の行から引き継ぐ。訂正のはずみで、
    本人が決めた範囲より広く見えてしまうことを防ぐため。
    先頭以外の行を指す訂正は受け付けない（連鎖が枝分かれするため）。
    """
    if not _is_self(conn, actor_user_id):
        raise LedgerError("訂正できるのは本人だけです")
    original = get_statement(conn, original_id)
    if original is None:
        raise LedgerError(f"訂正する言明が見つかりません：{original_id}")
    if head_of(conn, original_id).id != original_id:
        raise LedgerError("この行はすでに置き換えられています。いま有効な行を訂正してください")

    draft = StatementDraft(
        source_utterance_ids=[utterance_id],
        subject_user_id=original.subject_user_id,
        speaker_user_id=actor_user_id,
        speaker_is_twin=False,
        source=Source.SELF_CORRECTION,
        confirm_status=ConfirmStatus.CORRECTED,
        body=body,
        period=original.period,
        theme=original.theme,
        petals=original.petals if petals is None else petals,
        share_scope=original.share_scope,
        effective_date=effective_date or date.today(),
    )
    return _insert(conn, draft, replaces_id=original_id)


def head_of(conn: sqlite3.Connection, statement_id: int) -> Statement | None:
    """訂正の連鎖の先頭（いま有効な行）を返す。

    置き換えは1行につき1回までなので、連鎖は一本道になる。この台帳の関数を通る限り輪に
    ならない。表に直接入れると作れるので、そのときに要求が止まらなくならないよう、
    たどった番号を覚えてエラーにする。
    """
    seen: set[int] = set()
    current = get_statement(conn, statement_id)
    while current is not None:
        if current.id in seen:
            raise LedgerError(f"置き換えの連鎖が輪になっています：{current.id}")
        seen.add(current.id)
        row = conn.execute(
            "SELECT id FROM statements WHERE replaces_id = ?", (current.id,)
        ).fetchone()
        if row is None:
            return current
        current = get_statement(conn, row["id"])
    return None


def current_statements(
    conn: sqlite3.Connection, scopes: Iterable[ShareScope] | None = None
) -> list[Statement]:
    """いま有効な言明（誰からも置き換えられていない行）を返す。

    scopes を渡すと、その共有範囲の行だけを返す。だれにどの範囲を見せるかは
    app/consent.py が決める。
    """
    query = (
        "SELECT * FROM statements WHERE id NOT IN "
        "(SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)"
    )
    params: list[str] = []
    if scopes is not None:
        values = [scope.value for scope in scopes]
        if not values:
            return []
        query += f" AND share_scope IN ({', '.join('?' for _ in values)})"
        params = values
    # 番号は乱数なので順番を表さない。時刻と、同じ時刻の中では追記の順で並べる。
    rows = conn.execute(query + " ORDER BY created_at, rowid", params).fetchall()
    return [db.row_to_model(row, Statement) for row in rows]
