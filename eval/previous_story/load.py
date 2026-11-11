"""保存した架空ログから、AI を呼ばずに記録を作る。

役割：eval/previous_story/story/ の JSON を ledger と splitter を通してデータベースに入れる。何度でも作り直せる。
4つの極：3. 読み解き、積み上げる。
守る原則：8（人物と記録はすべて架空）、9（本人の言葉は一字も変えない）、10（本人だけの話を広げない）。

表に直接書かず、必ず ledger と splitter を通す。決まりに合わない行は入らない。
外側を1つの処理にまとめる。1行ごとに確定すると1万回以上の書き込みで遅くなり、途中で失敗すると半端に残るため。

物語の日付を確かめの行に渡す。今の時刻のままだと、10年分の確かめが読み込んだ日に固まり、
ツインの色の移り変わりが分からなくなるため。

言明の番号は乱数なので作り直すたびに変わる。保存した固める処理の結果は、番号ではなく鍵で言明を指す。

関連するテスト：tests/test_fiction.py
作り方の説明：data/README.md
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app import db, ledger, model_store, observations, splitter
from app.schemas import (
    CommentDraft,
    ConfirmStatus,
    ConsolidationUnit,
    ConversationDraft,
    ElementKind,
    ElementStatus,
    LetterDraft,
    Period,
    Petal,
    Place,
    ShareScope,
    Source,
    SplitResult,
    SplitTargetKind,
    StatementDraft,
    Theme,
    UtteranceDraft,
)
from eval.previous_story import plan
from fiction.log_models import LogComment, LogConversation, LogTurn

STORY_DIR = Path(__file__).resolve().parent / "story"
_ELEMENT_KEY = "el:"


def needs_correction(item: plan.PlanItem) -> bool:
    """ときどき、本人が要約を直す会話にする。決めた言葉の日には使わない。"""
    return item.must_say is None and item.day.toordinal() % 9 == 0


def _read_by_year(prefix: str) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for path in sorted(STORY_DIR.glob(f"{prefix}_*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def _write_by_year(prefix: str, entries: dict[str, dict]) -> None:
    """年ごとのファイルに書く。1ファイルが大きくなりすぎないようにする。"""
    STORY_DIR.mkdir(parents=True, exist_ok=True)
    years: dict[str, dict[str, dict]] = {}
    for key, value in entries.items():
        # 鍵は日付か、conv- や letter- を前に付けた日付
        year = key.removeprefix("conv-").removeprefix("letter-")[:4]
        years.setdefault(year, {})[key] = value
    for year, values in years.items():
        text = json.dumps(dict(sorted(values.items())), ensure_ascii=False, indent=0)
        (STORY_DIR / f"{prefix}_{year}.json").write_text(text, encoding="utf-8")


def read_logs() -> dict[str, dict]:
    return _read_by_year("log")


def write_logs(logs: dict[str, dict]) -> None:
    _write_by_year("log", logs)


def read_splits() -> dict[str, dict]:
    return _read_by_year("split")


def write_splits(splits: dict[str, dict]) -> None:
    _write_by_year("split", splits)


def read_models() -> list[dict]:
    """保存した固める処理の結果を、作った順に返す。"""
    runs: list[dict] = []
    for path in sorted(STORY_DIR.glob("model_*.json")):
        runs.extend(json.loads(path.read_text(encoding="utf-8")))
    return runs


def write_models(year: int, runs: list[dict]) -> None:
    STORY_DIR.mkdir(parents=True, exist_ok=True)
    (STORY_DIR / f"model_{year}.json").write_text(json.dumps(runs, ensure_ascii=False, indent=0), encoding="utf-8")


def _speaker_args(name: str, users: dict) -> dict:
    if name == "ツイン":
        return {"speaker_user_id": None, "speaker_is_twin": True}
    return {"speaker_user_id": users[name].id, "speaker_is_twin": False}


def load_one(conn, item: plan.PlanItem, log: LogConversation) -> dict[str, dict]:
    """1回の会話を入れ、言明に分ける単位を返す。"""
    users = {user.name: user for user in db.list_users(conn)}
    conversation = ledger.add_conversation(
        conn,
        ConversationDraft(
            conversation_date=item.day,
            trigger=log.trigger,
            theme=item.theme,
            life_stage=item.stage,
            self_confirmation=log.confirmation,
            closed_at=datetime.combine(item.day, time(9, 30)),
        ),
    )
    for number, turn in enumerate(log.turns):
        ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation.id,
                text=turn.text,
                spoken_at=datetime.combine(item.day, time(9, number % 60)),
                place=Place.DIARY_AND_TALK,
                **_speaker_args(turn.speaker, users),
            ),
        )
    units = {
        f"conv-{item.key}": {"conversation_id": conversation.id, "place": Place.DIARY_AND_TALK.value, "day": item.key}
    }
    if log.comments and not item.private:
        # 便りの相手は、縁のある人のコメントがあれば縁のある人まで、なければ家族にする
        reach = {"節子さん", "正雄さん"} & {comment.speaker for comment in log.comments}
        letter = ledger.add_letter(
            conn,
            LetterDraft(
                conversation_id=conversation.id,
                body=log.letter or log.trigger,
                share_with=ShareScope.CONNECTED if reach else ShareScope.FAMILY,
                shared_at=datetime.combine(item.day, time(12, 0)),
            ),
        )
        for number, comment in enumerate(log.comments):
            ledger.add_comment(
                conn,
                CommentDraft(
                    letter_id=letter.id,
                    author_user_id=users[comment.speaker].id,
                    body=comment.text,
                    commented_at=datetime.combine(item.day, time(18, number % 60)),
                ),
            )
        units[f"letter-{item.key}"] = {"letter_id": letter.id, "place": Place.THREAD.value, "day": item.key}
    return units


def load_logs(conn, items: list[plan.PlanItem], logs: dict[str, dict]) -> dict[str, dict]:
    """予定の順に会話を入れる。ログがない予定は飛ばす。"""
    units: dict[str, dict] = {}
    for item in items:
        entry = logs.get(item.key)
        if entry is not None:
            units.update(load_one(conn, item, LogConversation.model_validate(entry)))
    return units


def day_of(key: str) -> date:
    return date.fromisoformat(key.removeprefix("conv-").removeprefix("letter-"))


def _remember(conn, key: str, statement_id: int) -> None:
    conn.execute("INSERT INTO story_keys (key, statement_id) VALUES (?, ?)", (key, statement_id))


def _key_map(conn) -> dict[str, int]:
    return {row["key"]: row["statement_id"] for row in conn.execute("SELECT key, statement_id FROM story_keys")}


def _load_split(conn, unit_id: str, unit: dict, split: dict | None, item: plan.PlanItem, users) -> list[int]:
    """保存した分割の結果を、splitter を通して入れる。入った言明の番号を返す。"""
    # 処理済みの印を一緒に入れる。印がないと、起動のときの流し直しが AI で分け直して言明が二重になるため
    if "letter_id" in unit:
        kind, target = SplitTargetKind.LETTER, unit["letter_id"]
    else:
        kind, target = SplitTargetKind.CONVERSATION, unit["conversation_id"]
    utterances, last_rowid = splitter.load_after_mark(conn, kind, target, 0)
    mark = (kind, target, last_rowid)
    if split is None:
        splitter.save_split(conn, SplitResult(statements=[]), utterances, users, mark=mark)
        return []
    result = SplitResult.model_validate(split)
    if item.private:
        # 本人だけの話は、テーマを「つらかったこと」に固定する。託すテーマで広がらないようにするため
        for statement in result.statements:
            statement.theme = Theme.HARDSHIP
    report = splitter.save_split(conn, result, utterances, users, mark=mark)
    for index, statement_id in enumerate(report.statement_ids):
        _remember(conn, f"{unit_id}#{index}", statement_id)
    return report.statement_ids


def _add_fixed_word(conn, item: plan.PlanItem, unit: dict, self_user, new_ids: list[int]) -> None:
    """決めた言葉が分割で言明にならなかったときに足す。デモと確かめの手がかりになる言葉なので、落とさない。"""
    bodies = [ledger.get_statement(conn, number) for number in new_ids]
    if any(statement.source is Source.SELF_WORDS and item.must_say in statement.body for statement in bodies):
        return
    utterances = splitter.load_utterances(conn, conversation_id=unit["conversation_id"])
    spoken = next(
        utterance for utterance in utterances if utterance.speaker_user_id == self_user.id and item.must_say in utterance.text
    )
    statement = ledger.append_statement(
        conn,
        StatementDraft(
            source_utterance_ids=[spoken.id],
            subject_user_id=self_user.id,
            speaker_user_id=self_user.id,
            source=Source.SELF_WORDS,
            body=item.must_say,
            period=Period.NOW,
            theme=item.theme,
            share_scope=ShareScope.SELF_ONLY,
            effective_date=item.day,
            created_at=datetime.combine(item.day, time(9, 40)),
        ),
    )
    _remember(conn, f"conv-{item.key}#fixed", statement.id)
    new_ids.append(statement.id)


def _add_demo_guess(conn, item: plan.PlanItem, unit: dict, self_user) -> None:
    """デモの訂正の場面に使う AI の推測を足す。確かめないまま残す。"""
    utterances = splitter.load_utterances(conn, conversation_id=unit["conversation_id"])
    spoken = next(utterance for utterance in utterances if item.must_say in utterance.text)
    statement = ledger.append_statement(
        conn,
        StatementDraft(
            source_utterance_ids=[spoken.id],
            subject_user_id=self_user.id,
            speaker_is_twin=True,
            source=Source.AI_GUESS,
            body=plan.DEMO_GUESS,
            period=Period.NOW,
            theme=item.theme,
            share_scope=ShareScope.SELF_ONLY,
            effective_date=item.day,
            created_at=datetime.combine(item.day, time(9, 41)),
        ),
    )
    _remember(conn, f"conv-{item.key}#guess", statement.id)


def _confirm(conn, statement_id: int, day: date, self_user) -> None:
    head = ledger.head_of(conn, statement_id)
    if head.source is Source.SELF_WORDS and head.confirm_status is ConfirmStatus.UNCONFIRMED:
        ledger.confirm_statement(conn, head.id, actor_user_id=self_user.id, at=datetime.combine(day, time(9, 45)))


def _apply_themes(conn, statement_ids: list[int], themes: set[Theme], day: date, self_user) -> None:
    """本人が決めたテーマの言葉を、意思決定支援用の行にする。"""
    for number in statement_ids:
        head = ledger.head_of(conn, number)
        if head.theme in themes and head.share_scope is not ShareScope.DECISION_SUPPORT:
            if head.confirm_status is not ConfirmStatus.RESTING:
                ledger.change_share_scope(
                    conn,
                    head.id,
                    actor_user_id=self_user.id,
                    share_scope=ShareScope.DECISION_SUPPORT,
                    at=datetime.combine(day, time(9, 50)),
                )


def _chosen_themes(conn) -> set[Theme]:
    return {Theme(row["theme"]) for row in conn.execute("SELECT theme FROM handover_themes")}


def replay(conn, items: list[plan.PlanItem], *, confirm: bool = True, logs=None, splits=None) -> int:
    """予定の会話を入れ、分割の結果を入れ、確かめと託す範囲を当てる。入れた言明の数を返す。"""
    logs = read_logs() if logs is None else logs
    splits = read_splits() if splits is None else splits
    users = db.list_users(conn)
    self_user = db.self_user(conn)
    themes = _chosen_themes(conn)
    total = 0
    with ledger.atomic(conn):
        for item in items:
            entry = logs.get(item.key)
            if entry is None:
                continue
            log = LogConversation.model_validate(entry)
            units = load_one(conn, item, log)
            conv_id = f"conv-{item.key}"
            new_ids = _load_split(conn, conv_id, units[conv_id], splits.get(conv_id), item, users)
            if item.must_say:
                _add_fixed_word(conn, item, units[conv_id], self_user, new_ids)
            if item.day == plan.DEMO_GUESS_DAY:
                _add_demo_guess(conn, item, units[conv_id], self_user)
            letter_ids: list[int] = []
            letter_id = f"letter-{item.key}"
            if letter_id in units:
                letter_ids = _load_split(conn, letter_id, units[letter_id], splits.get(letter_id), item, users)
            if confirm and log.confirmation == "合っています":
                for number in new_ids:
                    _confirm(conn, number, item.day, self_user)
            _apply_themes(conn, [*new_ids, *letter_ids], themes, item.day, self_user)
            total += len(new_ids) + len(letter_ids)
    return total


def load_observations(conn, *, until: date | None = None) -> None:
    """決めた今の様子を入れる。"""
    users = {user.name: user for user in db.list_users(conn)}
    self_user = db.self_user(conn)
    with ledger.atomic(conn):
        for item in plan.FIXED_OBSERVATIONS:
            if until is None or item.day <= until:
                observations.save_observation(
                    conn, users[item.speaker], self_user, item.words, Theme.HOME, datetime.combine(item.day, time(17, 0))
                )


def load_model_runs(conn, runs: list[dict]) -> int:
    """保存した固める処理の結果を入れる。AI は呼ばない。入れた要素の数を返す。"""
    keys = _key_map(conn)
    heads = ledger.head_map(conn)
    total = 0
    with ledger.atomic(conn):
        for run in runs:
            unit = ConsolidationUnit(run["unit"])
            start = date.fromisoformat(run["start"])
            output: list[int] = []
            for index, element in enumerate(run["elements"]):
                evidence = [keys[key] for key in element["evidence_keys"] if key in keys]
                if not evidence:
                    continue
                previous = keys.get(_ELEMENT_KEY + element["previous_key"]) if element.get("previous_key") else None
                kind = ElementKind(element["kind"])
                confirmed = any(
                    number in heads and heads[number].confirm_status in {ConfirmStatus.CONFIRMED, ConfirmStatus.CORRECTED}
                    for number in evidence
                )
                saved = model_store.add_element(
                    conn,
                    kind=kind,
                    body=element["body"],
                    condition=element.get("condition"),
                    petals=[Petal(petal) for petal in element.get("petals", [])],
                    confidence=element["confidence"],
                    status=ElementStatus.SELF_CONFIRMED
                    if confirmed and kind is not ElementKind.CHANGE
                    else ElementStatus.AI_VIEW,
                    valid_from=start,
                    evidence_statement_ids=sorted(evidence),
                    unit=unit,
                    previous_version_id=previous,
                )
                _remember(conn, f"{_ELEMENT_KEY}{run['id']}#{index}", saved.id)
                keys[f"{_ELEMENT_KEY}{run['id']}#{index}"] = saved.id
                output.append(saved.id)
                total += 1
            model_store.add_run(
                conn,
                unit=unit,
                period_start=start,
                period_end=date.fromisoformat(run["end"]),
                input_statement_ids=[keys[key] for key in run["input_keys"] if key in keys],
                output_element_ids=output,
                model=run["model"],
                input_tokens=run["input_tokens"],
                output_tokens=run["output_tokens"],
                run_at=datetime.combine(date.fromisoformat(run["end"]), time(23, 0)),
            )
    return total


def key_of_statement(conn) -> dict[int, str]:
    """言明の番号から鍵を引く辞書を返す。"""
    return {
        row["statement_id"]: row["key"]
        for row in conn.execute("SELECT key, statement_id FROM story_keys")
        if not row["key"].startswith(_ELEMENT_KEY)
    }


def key_of_element(conn) -> dict[int, str]:
    return {
        row["statement_id"]: row["key"].removeprefix(_ELEMENT_KEY)
        for row in conn.execute("SELECT key, statement_id FROM story_keys")
        if row["key"].startswith(_ELEMENT_KEY)
    }


def remember_element(conn, key: str, element_id: int) -> None:
    _remember(conn, _ELEMENT_KEY + key, element_id)


def mark_loaded(conn, stage: str, at: datetime) -> None:
    conn.execute("INSERT INTO story_marks (stage, loaded_at) VALUES (?, ?)", (stage, at.isoformat()))


def is_loaded(conn, stage: str) -> bool:
    return conn.execute("SELECT 1 FROM story_marks WHERE stage = ?", (stage,)).fetchone() is not None
