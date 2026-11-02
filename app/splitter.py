"""AI が分けた言明を確かめて保存する（決まった手順で動く層）。

役割：AI の結果を台帳の決まりに照らし、合うものだけを保存し、入らなかったものを理由つきで数える。
      AI は呼ばない。
4つの極：3. 読み解き、積み上げる。
守る原則：3（出どころを分ける）、4（推測は言い切らない）、9（本人の言葉は一字も変えない）、
          10（共有範囲は本人が決める）。

AI の答えは守られるとは限らないので、次のことをプログラムで決める。
- 共有範囲は AI から受け取らず、すべて「本人だけ」で保存する。見せる範囲は本人が決めるため。
- 確認の状態は未確認で保存する。本人の操作でだけ変わるため。
- 本人の言葉は、文の切れ目で始まり終わる原文の抜き出しだけを入れる（app/verbatim.py）。
- 解釈は仮説として保存する（仮説リーフ）。言明の根拠に本人の発言がないときは、本人の真意の
  確からしさを 0.4 までにし、決まった注意書きを必ず付ける。
- AIの推測には解釈を付けない。推測の推測になるため。
- AIの推測は、文末が言い切らない形でなければ捨てる。人の発言を根拠にしない推測も捨てる。
- 本人に確かめる問いの候補は、問いが1つで「合っていますか？」で終わるものだけを残す。
  「つらかったこと」の問いは残さない。つらい出来事は AI から聞かないため。

合わない言明を救うために書き換えることはしない。本人の言葉を変えないため。
発言の原文は残るので、あとから作り直せる。

関連するテスト：tests/test_splitter.py
全体の説明：docs/architecture.md
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from app import db, ledger, verbatim
from app.prompts import TWIN_LABEL
from app.schemas import (
    Perspective,
    Place,
    Role,
    ShareScope,
    Source,
    SplitResult,
    SplitStatement,
    StatementDraft,
    Theme,
    Utterance,
    User,
)

# 本人の発言がないときの、本人の真意の確からしさの上限
SELF_INTENT_CAP = 0.4
_SELF_INTENT_CAUTION = "本人の発言がない記録からの仮説です。本人に確かめていません。"

# AIの推測の文末の形。文ごとに確かめる。本文のどこかに目印があるだけでは、言い切りを止められないため。
_HEDGED_ENDINGS = (
    "かもしれません",
    "かもしれない",
    "のでしょうか",
    "でしょうか",
    "でしょう",
    "だろう",
    "ようです",
    "ようだ",
    "らしいです",
    "らしい",
    "と思われます",
)
_CONFIRM_ENDING = "合っていますか？"

_VERBATIM_SOURCES = {Source.SELF_WORDS}


@dataclass
class DroppedStatement:
    """入らなかった言明と、その理由。"""

    body: str
    reason: str


@dataclass
class SplitReport:
    """保存の結果。"""

    statement_ids: list[int] = field(default_factory=list)
    interpretation_count: int = 0
    question_count: int = 0
    dropped_question_count: int = 0
    dropped: list[DroppedStatement] = field(default_factory=list)


def format_transcript(
    place: str,
    day: date,
    utterances: list[Utterance],
    users: list[User],
    letter_body: str | None = None,
) -> str:
    """発言を、AI に渡す形にする。発言には1始まりの番号を振る。

    スレッドでは、コメントが何を指すかが分かるよう、便りの本文を文脈として渡す。
    便りの本文には番号を振らないので、言明の根拠にはならない。
    """
    by_id = {user.id: user for user in users}
    people = "、".join(f"{user.name}（{user.role.value}）" for user in users)
    lines = [f"## 場面\n{place}、{day.isoformat()}", f"## 人物の一覧\n{people}、{TWIN_LABEL}"]
    if letter_body:
        lines.append(f"## 便りの本文（文脈です。言明の根拠にしません）\n{letter_body}")
    lines.append("## 発言")
    for number, utterance in enumerate(utterances, start=1):
        if utterance.speaker_is_twin:
            speaker = TWIN_LABEL
        else:
            user = by_id[utterance.speaker_user_id]
            speaker = f"{user.name}（{user.role.value}）"
        lines.append(f"[{number}] {speaker}：{utterance.text}")
    return "\n".join(lines)


def _pick(utterances: list[Utterance], numbers: list[int]) -> list[Utterance] | None:
    """1始まりの番号から発言を取り出す。入力にない番号があれば None を返す。"""
    if not all(1 <= number <= len(utterances) for number in numbers):
        return None
    return [utterances[number - 1] for number in numbers]


def quote_problems(result: SplitResult, utterances: list[Utterance]) -> list[str]:
    """本人の言葉の本文の問題を、やり直しのときに AI へ伝える文にする。"""
    problems = []
    for item in result.statements:
        if item.source not in _VERBATIM_SOURCES:
            continue
        picked = _pick(utterances, item.utterance_numbers)
        if picked is None or len(picked) != 1:
            problems.append(f"「{item.body}」：根拠の発言の番号を1つにしてください")
            continue
        problem = verbatim.quote_problem(picked[0].text, item.body)
        if problem:
            problems.append(f"「{item.body}」：{problem}")
    return problems


def _resolve_speaker(name: str, users_by_name: dict[str, User]) -> tuple[int | None, bool] | None:
    """話した人の名前を、利用者の番号とツインの印にする。分からなければ None を返す。"""
    if name == TWIN_LABEL:
        return None, True
    user = users_by_name.get(name)
    return (user.id, False) if user else None


def _drop_reason(item: SplitStatement, users_by_name: dict[str, User], picked) -> str | None:
    """保存の前に分かる、入れられない理由を返す。台帳の決まりは ledger が確かめる。"""
    if item.source is Source.SELF_CORRECTION:
        return "本人の訂正は、訂正の処理からだけ作れます"
    if picked is None or not picked:
        return "根拠の発言の番号が入力にありません"
    if item.subject not in users_by_name:
        return "誰についてかが、人物の一覧にありません"
    if _resolve_speaker(item.speaker, users_by_name) is None:
        return "話した人が、人物の一覧にありません"
    if item.source is Source.AI_GUESS:
        if all(utterance.speaker_is_twin for utterance in picked):
            return "AIの推測の根拠に、人の発言がありません"
        if not _is_hedged(item.body):
            return "AIの推測が言い切っています"
    if item.source is Source.OBSERVATION and picked[0].place is Place.THREAD:
        return "スレッドのコメントは、書いた人の役割の証言にします"
    return None


def _is_hedged(body: str) -> bool:
    """すべての文が、言い切らない形で終わっているかを返す。"""
    sentences = [part.strip() for part in re.split(r"[。！？!?\n]+", body) if part.strip()]
    return bool(sentences) and all(sentence.endswith(_HEDGED_ENDINGS) for sentence in sentences)


def _question_is_usable(item: SplitStatement) -> bool:
    """本人に確かめる問いの候補として残せるかを返す。"""
    question = item.confirm_question.strip()
    return (
        item.theme is not Theme.HARDSHIP
        and question.endswith(_CONFIRM_ENDING)
        and sum(question.count(mark) for mark in "？?") == 1
    )


def save_split(
    conn: sqlite3.Connection,
    result: SplitResult,
    utterances: list[Utterance],
    users: list[User],
) -> SplitReport:
    """AI の結果を確かめて保存する。"""
    report = SplitReport()
    users_by_name = {user.name: user for user in users}
    users_by_id = {user.id: user for user in users}

    def _spoken_by_self(utterance: Utterance) -> bool:
        return (
            not utterance.speaker_is_twin
            and users_by_id[utterance.speaker_user_id].role is Role.SELF
        )

    # 全部入るか、全部入らないかにする。途中で失敗しても、同じ発言からやり直せるようにするため。
    with ledger.atomic(conn):
        for item in result.statements:
            picked = _pick(utterances, item.utterance_numbers)
            reason = _drop_reason(item, users_by_name, picked)
            if reason:
                report.dropped.append(DroppedStatement(item.body, reason))
                continue

            speaker_user_id, speaker_is_twin = _resolve_speaker(item.speaker, users_by_name)
            draft = StatementDraft(
                source_utterance_ids=[utterance.id for utterance in picked],
                subject_user_id=users_by_name[item.subject].id,
                speaker_user_id=speaker_user_id,
                speaker_is_twin=speaker_is_twin,
                source=item.source,
                body=item.body,
                period=item.period,
                theme=item.theme,
                petals=item.petals,
                share_scope=ShareScope.SELF_ONLY,
                effective_date=picked[0].spoken_at.date(),
                created_at=picked[0].spoken_at,
            )
            try:
                statement = ledger.append_statement(conn, draft)
            except ledger.LedgerError as error:
                report.dropped.append(DroppedStatement(item.body, str(error)))
                continue
            report.statement_ids.append(statement.id)

            self_spoke = any(_spoken_by_self(utterance) for utterance in picked)
            interpretations = [] if item.source is Source.AI_GUESS else item.interpretations
            for interpretation in interpretations:
                confidence, caution = interpretation.confidence, interpretation.caution
                if not self_spoke and interpretation.perspective is Perspective.INTENT:
                    confidence = min(confidence, SELF_INTENT_CAP)
                    # AI の注意書きが空や「特になし」でも残るよう、決まった文を必ず先に置く
                    extra = caution.strip() if caution else ""
                    caution = f"{_SELF_INTENT_CAUTION} {extra}".strip()
                ledger.add_interpretation(
                    conn,
                    statement_id=statement.id,
                    perspective=interpretation.perspective,
                    viewpoint_role=interpretation.viewpoint_role,
                    body=interpretation.body,
                    confidence=confidence,
                    caution=caution,
                )
                report.interpretation_count += 1

            if item.confirm_question and not _question_is_usable(item):
                report.dropped_question_count += 1
            elif item.confirm_question:
                ledger.add_pending_question(
                    conn,
                    question=item.confirm_question,
                    theme=item.theme,
                    period=item.period,
                    petals=item.petals,
                )
                report.question_count += 1
    return report


def load_utterances(
    conn: sqlite3.Connection, *, conversation_id: int | None = None, letter_id: int | None = None
) -> list[Utterance]:
    """会話かスレッドの発言を、話した順に読む。"""
    column, value = ("conversation_id", conversation_id) if conversation_id else ("letter_id", letter_id)
    rows = conn.execute(
        f"SELECT * FROM utterances WHERE {column} = ? ORDER BY spoken_at, rowid", (value,)
    ).fetchall()
    return [db.row_to_model(row, Utterance) for row in rows]


def already_split(conn: sqlite3.Connection, utterances: list[Utterance]) -> bool:
    """この発言を根拠にした言明がすでにあるかを返す。同じ記録を2回分けないために使う。

    呼び出しの記録で判定しないのは、途中で失敗した記録をもう一度処理できるようにするため。
    """
    numbers = [utterance.id for utterance in utterances]
    marks = ", ".join("?" for _ in numbers)
    row = conn.execute(
        f"SELECT 1 FROM statements, json_each(statements.source_utterance_ids) "
        f"WHERE json_each.value IN ({marks}) LIMIT 1",
        numbers,
    ).fetchone()
    return row is not None


def first_spoken_on(utterances: list[Utterance]) -> date:
    return utterances[0].spoken_at.date() if utterances else datetime.now().date()
