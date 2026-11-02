"""データベースの表と接続。

役割：SQLite の表を作り、接続を渡し、行を型に変換する。架空用の起動では利用者4人を入れる。
4つの極：特定の極に属さない土台。
守る原則：5（言明の表は書き換えと削除を拒否する）、8（提出物とデモは架空）。

決めた言葉の確かめは app/schemas.py の Enum からトリガーを作る。手で書き写すと、型と表の言葉が
食い違う余地が生まれるため。CHECK にしないのは、言葉を足すたびに追記だけの表を作り直すことに
なるため。本人の言葉は作り直せないので、表の作り直しを避ける。関連するテスト：tests/test_schema.py
表の変更は MIGRATIONS に追加だけの手順で書き、適用の前にファイルを隣に控える。
台帳の決まりの説明は docs/architecture.md にある。
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app import schemas as s

ModelT = TypeVar("ModelT", bound=BaseModel)

# 番号の上限。JavaScript で安全に扱える最大の整数にする。これより大きい番号は入口で止める。
MAX_ID = 2**53 - 1

# 本人以外から隠れる行を持つ表の番号の宣言。
# 通し番号だと、番号の飛びから「本人だけ」の記録の数が分かるので、乱数を new_id で作って渡す。
# INTEGER PRIMARY KEY にしないのは、指定し忘れると SQLite が通し番号を付けてしまうため。
# この形なら指定し忘れは NOT NULL で止まり、追記の順は応答に出ない rowid に残る。
# 利用者などの表は、本人以外に番号を出さないか行が隠れないので、通し番号のままにしている。
# 関連するテスト：tests/test_ids.py
_RANDOM_ID = f"id INTEGER NOT NULL UNIQUE CHECK (id BETWEEN 1 AND {MAX_ID})"

# JSON の配列で持つ列。参照を要素→言明→原文の順にたどるだけなので、つなぎの表を増やさない。
# 番号が実在するかは、表の決まりでは確かめない（言明の根拠の発言は app/ledger.py が確かめる）。
JSON_COLUMNS = {
    "source_utterance_ids",
    "petals",
    "evidence_statement_ids",
    "evidence_element_ids",
    "input_statement_ids",
    "output_element_ids",
    "element_ids",
    "statement_ids",
}

# 表の形の番号。MIGRATIONS を足すと増える。
BASE_SCHEMA_VERSION = 1

# 表の変更の手順。(その手順を適用したあとの番号, SQL の文の並び)。追加だけにする。
# 新しい表は SCHEMA に足せば、起動のときに作られるのでここには書かない。
# 既にある表に列を足すときは、SCHEMA の定義とここの両方に書く。
MIGRATIONS: list[tuple[int, list[str]]] = []


class OldDatabaseError(RuntimeError):
    """古い形のデータベースを開こうとしたとき。"""


def _enum(column: str, nullable: bool = False) -> str:
    """決めた言葉の列の定義を作る。言葉の確かめは WORD_COLUMNS のトリガーが行う。"""
    not_null = "" if nullable else " NOT NULL"
    return f"{column} TEXT{not_null}"


def _petals(column: str = "petals") -> str:
    return (
        f"{column} TEXT NOT NULL DEFAULT '[]' "
        f"CHECK (json_valid({column}) AND json_array_length({column}) <= 2)"
    )


def _json_list(column: str) -> str:
    return f"{column} TEXT NOT NULL DEFAULT '[]' CHECK (json_valid({column}))"


_THREAD = s.Place.THREAD.value
_LIFE_STAGE_DEFAULT = s.LifeStage.ACTIVE.value

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    {_enum("role")},
    called_name TEXT,
    birth_year INTEGER,
    {_enum("gender", nullable=True)},
    relation TEXT,
    {_enum("residence", nullable=True)},
    is_deceased INTEGER NOT NULL DEFAULT 0 CHECK (is_deceased IN (0, 1)),
    memo TEXT,
    photo_id INTEGER,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS delegations (
    id INTEGER PRIMARY KEY,
    delegate_user_id INTEGER NOT NULL REFERENCES users(id),
    {_enum("scope")},
    decided_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_logs (
    id INTEGER PRIMARY KEY,
    viewer_user_id INTEGER NOT NULL REFERENCES users(id),
    target TEXT NOT NULL,
    opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    {_RANDOM_ID},
    conversation_date TEXT NOT NULL,
    trigger TEXT,
    {_enum("theme", nullable=True)},
    life_stage TEXT NOT NULL DEFAULT '{_LIFE_STAGE_DEFAULT}',
    summary TEXT,
    self_confirmation TEXT,
    room_id INTEGER REFERENCES rooms(id),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    {_RANDOM_ID},
    file TEXT NOT NULL,
    taken_on TEXT,
    description TEXT,
    owner_user_id INTEGER REFERENCES users(id),
    {_enum("kind")},
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS letters (
    {_RANDOM_ID},
    conversation_id INTEGER REFERENCES conversations(id),
    body TEXT NOT NULL,
    photo_id INTEGER REFERENCES photos(id),
    share_with TEXT NOT NULL CHECK (
        share_with IN ('{s.ShareScope.FAMILY.value}', '{s.ShareScope.CONNECTED.value}')
    ),
    shared_at TEXT NOT NULL
);

-- 発言：どこで話したかと、ぶら下がる先（会話か便り）の組み合わせも表で守る
CREATE TABLE IF NOT EXISTS utterances (
    {_RANDOM_ID},
    conversation_id INTEGER REFERENCES conversations(id),
    letter_id INTEGER REFERENCES letters(id),
    speaker_user_id INTEGER REFERENCES users(id),
    speaker_is_twin INTEGER NOT NULL DEFAULT 0 CHECK (speaker_is_twin IN (0, 1)),
    text TEXT NOT NULL,
    spoken_at TEXT NOT NULL,
    {_enum("place")},
    CHECK ((speaker_user_id IS NULL) = (speaker_is_twin = 1)),
    CHECK (
        (place = '{_THREAD}' AND letter_id IS NOT NULL AND conversation_id IS NULL)
        OR (place <> '{_THREAD}' AND conversation_id IS NOT NULL AND letter_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS comments (
    {_RANDOM_ID},
    letter_id INTEGER NOT NULL REFERENCES letters(id),
    author_user_id INTEGER REFERENCES users(id),
    body TEXT NOT NULL,
    commented_at TEXT NOT NULL,
    is_twin_question INTEGER NOT NULL DEFAULT 0 CHECK (is_twin_question IN (0, 1)),
    utterance_id INTEGER NOT NULL REFERENCES utterances(id),
    client_key TEXT,
    CHECK ((author_user_id IS NULL) = (is_twin_question = 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS comments_once
    ON comments(letter_id, author_user_id, client_key) WHERE client_key IS NOT NULL;

-- 言明の台帳：追記だけ。書き換えと削除は下のトリガーで拒否する
CREATE TABLE IF NOT EXISTS statements (
    {_RANDOM_ID},
    {_json_list("source_utterance_ids")},
    subject_user_id INTEGER NOT NULL REFERENCES users(id),
    speaker_user_id INTEGER REFERENCES users(id),
    speaker_is_twin INTEGER NOT NULL DEFAULT 0 CHECK (speaker_is_twin IN (0, 1)),
    {_enum("source")},
    {_enum("confirm_status")},
    body TEXT NOT NULL,
    {_enum("period")},
    {_enum("theme")},
    {_petals()},
    {_enum("share_scope")},
    effective_date TEXT NOT NULL,
    replaces_id INTEGER REFERENCES statements(id),
    created_at TEXT NOT NULL,
    CHECK ((speaker_user_id IS NULL) = (speaker_is_twin = 1))
);

-- 同じ行を2回置き換えると連鎖が枝分かれし、どれが「いま有効」か決まらなくなるので防ぐ
CREATE UNIQUE INDEX IF NOT EXISTS statements_replaces_once
    ON statements(replaces_id) WHERE replaces_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS interpretations (
    {_RANDOM_ID},
    statement_id INTEGER NOT NULL REFERENCES statements(id),
    {_enum("perspective")},
    {_enum("viewpoint_role")},
    body TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    caution TEXT,
    {_json_list("evidence_statement_ids")},
    {_enum("status")}
);

CREATE TABLE IF NOT EXISTS model_elements (
    {_RANDOM_ID},
    {_enum("kind")},
    body TEXT NOT NULL,
    condition TEXT,
    {_petals()},
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    {_enum("status")},
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    {_json_list("evidence_statement_ids")},
    {_enum("unit")},
    version INTEGER NOT NULL,
    previous_version_id INTEGER REFERENCES model_elements(id),
    last_confirmed_on TEXT,
    unconfirmed_reconsolidations INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consolidation_runs (
    id INTEGER PRIMARY KEY,
    {_enum("unit")},
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    {_json_list("input_statement_ids")},
    {_json_list("output_element_ids")},
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    run_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inference_logs (
    {_RANDOM_ID},
    {_enum("kind")},
    question TEXT,
    result TEXT NOT NULL,
    {_json_list("element_ids")},
    {_json_list("statement_ids")},
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question_logs (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    {_enum("theme", nullable=True)},
    {_enum("period", nullable=True)},
    {_petals()},
    asked_on TEXT,
    {_enum("result", nullable=True)},
    conversation_id INTEGER REFERENCES conversations(id),
    letter_id INTEGER REFERENCES letters(id),
    room_id INTEGER REFERENCES rooms(id),
    utterance_id INTEGER REFERENCES utterances(id)
);

CREATE TABLE IF NOT EXISTS care_plan_drafts (
    id INTEGER PRIMARY KEY,
    self_intent TEXT NOT NULL,
    long_term_goal TEXT NOT NULL,
    {_json_list("evidence_statement_ids")},
    {_json_list("evidence_element_ids")},
    {_enum("verdict")},
    created_on TEXT NOT NULL,
    previous_version_id INTEGER REFERENCES care_plan_drafts(id)
);

CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    is_batch INTEGER NOT NULL DEFAULT 0 CHECK (is_batch IN (0, 1)),
    target TEXT,
    called_at TEXT NOT NULL,
    seconds REAL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    {_enum("question_type")},
    {_enum("method")},
    result TEXT,
    is_correct INTEGER CHECK (is_correct IN (0, 1)),
    read_amount INTEGER
);

-- 部屋：ツインとの部屋、1対1、グループ
CREATE TABLE IF NOT EXISTS rooms (
    {_RANDOM_ID},
    {_enum("kind")},
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    joined_at TEXT NOT NULL,
    left_at TEXT,
    PRIMARY KEY (room_id, user_id)
);

-- グループにツインが入っている間。入るたびに会話を1行作り、ツインが読むのはその会話の発言だけにする
CREATE TABLE IF NOT EXISTS twin_presences (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    joined_at TEXT NOT NULL,
    left_at TEXT
);

-- 部屋に出す行。追記だけ。AI に渡す発言は utterances にだけ本文を持ち、ここからは番号で指す。
-- 人だけの部屋の本文はここにだけあり、AI に渡す処理からは読めない。
CREATE TABLE IF NOT EXISTS room_messages (
    {_RANDOM_ID},
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    sender_user_id INTEGER REFERENCES users(id),
    is_twin INTEGER NOT NULL DEFAULT 0 CHECK (is_twin IN (0, 1)),
    {_enum("kind")},
    text TEXT,
    photo_id INTEGER REFERENCES photos(id),
    utterance_id INTEGER REFERENCES utterances(id),
    conversation_id INTEGER REFERENCES conversations(id),
    question_log_id INTEGER REFERENCES question_logs(id),
    client_key TEXT,
    sent_at TEXT NOT NULL,
    CHECK (utterance_id IS NULL OR text IS NULL),
    CHECK (is_twin = 0 OR sender_user_id IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS room_messages_once
    ON room_messages(room_id, sender_user_id, client_key) WHERE client_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS family_links (
    id INTEGER PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id),
    to_user_id INTEGER NOT NULL REFERENCES users(id),
    {_enum("kind")},
    created_at TEXT NOT NULL,
    UNIQUE (from_user_id, to_user_id, kind),
    CHECK (from_user_id <> to_user_id)
);

CREATE TABLE IF NOT EXISTS map_places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    created_at TEXT NOT NULL,
    removed_at TEXT
);

-- 線の強さと向きは本人が選ぶ。AI の候補の表には持たない
CREATE TABLE IF NOT EXISTS map_links (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id),
    place_id INTEGER UNIQUE REFERENCES map_places(id),
    {_enum("strength")},
    {_enum("direction")},
    updated_at TEXT NOT NULL,
    CHECK ((user_id IS NULL) <> (place_id IS NULL))
);

CREATE TABLE IF NOT EXISTS person_candidates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    relation TEXT,
    {_enum("relative_kind")},
    related_to_name TEXT,
    is_deceased INTEGER NOT NULL DEFAULT 0 CHECK (is_deceased IN (0, 1)),
    {_enum("gender")},
    evidence_utterance_id INTEGER NOT NULL REFERENCES utterances(id),
    {_enum("status")},
    created_at TEXT NOT NULL,
    decided_at TEXT
);

-- どこまで言明に分けたかの印。発言は追記だけなので rowid は増えるだけ
CREATE TABLE IF NOT EXISTS split_marks (
    {_enum("target_kind")},
    target_id INTEGER NOT NULL,
    last_utterance_rowid INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (target_kind, target_id)
);

-- 裏の処理の失敗の記録。本文は入れず、処理の名前と例外の種類だけを残す
CREATE TABLE IF NOT EXISTS job_failures (
    id INTEGER PRIMARY KEY,
    job TEXT NOT NULL,
    target TEXT,
    message TEXT NOT NULL,
    failed_at TEXT NOT NULL
);
"""

# 決めた言葉の列の一覧。ここからトリガーを作る。tests/test_schema.py が型との食い違いを確かめる。
WORD_COLUMNS: dict[str, dict[str, type[StrEnum]]] = {
    "users": {"role": s.Role, "gender": s.Gender, "residence": s.Residence},
    "delegations": {"scope": s.ShareScope},
    "conversations": {"theme": s.Theme, "life_stage": s.LifeStage},
    "photos": {"kind": s.PhotoKind},
    "utterances": {"place": s.Place},
    "statements": {
        "source": s.Source,
        "confirm_status": s.ConfirmStatus,
        "period": s.Period,
        "theme": s.Theme,
        "share_scope": s.ShareScope,
    },
    "interpretations": {
        "perspective": s.Perspective,
        "viewpoint_role": s.Role,
        "status": s.InterpretationStatus,
    },
    "model_elements": {"kind": s.ElementKind, "status": s.ElementStatus, "unit": s.ConsolidationUnit},
    "consolidation_runs": {"unit": s.ConsolidationUnit},
    "inference_logs": {"kind": s.InferenceKind},
    "question_logs": {"theme": s.Theme, "period": s.Period, "result": s.QuestionResult},
    "care_plan_drafts": {"verdict": s.EvidenceVerdict},
    "evaluations": {"question_type": s.EvalQuestionType, "method": s.EvalMethod},
    "rooms": {"kind": s.RoomKind},
    "room_messages": {"kind": s.MessageKind},
    "family_links": {"kind": s.FamilyLinkKind},
    "map_links": {"strength": s.LinkStrength, "direction": s.SupportDirection},
    "person_candidates": {
        "relative_kind": s.RelativeKind,
        "gender": s.Gender,
        "status": s.CandidateStatus,
    },
    "split_marks": {"target_kind": s.SplitTargetKind},
}


def _word_triggers() -> str:
    """決めた言葉の確かめのトリガーを作る。起動のたびに作り直すので、言葉を足すだけで効く。"""
    parts = []
    for table, columns in WORD_COLUMNS.items():
        conditions = []
        for column, enum_cls in columns.items():
            words = ", ".join(f"'{member.value}'" for member in enum_cls)
            conditions.append(f"(NEW.{column} IS NOT NULL AND NEW.{column} NOT IN ({words}))")
        condition = " OR ".join(conditions)
        message = f"{table} の決めた言葉に合いません"
        parts.append(f"""
DROP TRIGGER IF EXISTS {table}_words;
CREATE TRIGGER {table}_words BEFORE INSERT ON {table}
WHEN {condition}
BEGIN SELECT RAISE(ABORT, '{message}'); END;

DROP TRIGGER IF EXISTS {table}_words_update;
CREATE TRIGGER {table}_words_update BEFORE UPDATE ON {table}
WHEN {condition}
BEGIN SELECT RAISE(ABORT, '{message}'); END;
""")
    return "".join(parts)


def _append_only_triggers(table: str, *, label: str, unique_replaces: bool = False) -> str:
    """書き換えと削除を、データベースの段階で拒否するトリガーを作る。

    本人の言葉は作り直せないので上書きさせない。アプリのコードに更新の処理がなくても、
    別の経路の SQL で壊せるなら「追記だけ」とは言えないため、表そのものに決まりを置く。
    REPLACE は行を消してから入れ直すが、そのとき削除のトリガーは動かない。
    そこで INSERT の前にも確かめる。関連するテスト：tests/test_ledger.py
    """
    message = f"{label}は追記だけです"
    replace_check = (
        f" OR (NEW.replaces_id IS NOT NULL AND EXISTS "
        f"(SELECT 1 FROM {table} WHERE replaces_id = NEW.replaces_id))"
        if unique_replaces
        else ""
    )
    return f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{message}'); END;

CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{message}'); END;

CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS (SELECT 1 FROM {table} WHERE id = NEW.id)
    OR EXISTS (SELECT 1 FROM {table} WHERE rowid = NEW.rowid){replace_check}
BEGIN SELECT RAISE(ABORT, '{message}'); END;
"""


def _valid_petals_trigger(table: str) -> str:
    """花びらの列に、決めた言葉だけが重ならずに入ることを、追記の前に確かめる。

    CHECK では JSON の中身を調べられないので、トリガーで調べる。
    壊れた値が1行でも入ると、その行を読む一覧全体が読めなくなるため。
    CASE を使うのは、JSON でない値のときに json_each まで進ませないため。
    書き換えのできる表があるので、追記のときと、花びらを書き換えるときの両方で確かめる。
    """
    words = ", ".join(f"'{petal.value}'" for petal in s.Petal)
    condition = f"""CASE
    WHEN NOT json_valid(NEW.petals) THEN 1
    WHEN json_type(NEW.petals) <> 'array' THEN 1
    WHEN EXISTS (SELECT 1 FROM json_each(NEW.petals) WHERE value NOT IN ({words})) THEN 1
    WHEN (SELECT COUNT(*) FROM json_each(NEW.petals))
        <> (SELECT COUNT(DISTINCT value) FROM json_each(NEW.petals)) THEN 1
    ELSE 0
END"""
    message = "花びらは決めた言葉を重ならずに入れてください"
    return f"""
CREATE TRIGGER IF NOT EXISTS {table}_valid_petals BEFORE INSERT ON {table}
WHEN {condition}
BEGIN SELECT RAISE(ABORT, '{message}'); END;

CREATE TRIGGER IF NOT EXISTS {table}_valid_petals_update BEFORE UPDATE OF petals ON {table}
WHEN {condition}
BEGIN SELECT RAISE(ABORT, '{message}'); END;
"""


TRIGGERS = (
    _append_only_triggers("statements", label="言明の台帳", unique_replaces=True)
    + _append_only_triggers("utterances", label="発言の原文")
    + _append_only_triggers("room_messages", label="部屋の発言")
    + """
CREATE TRIGGER IF NOT EXISTS statements_no_self_reference BEFORE INSERT ON statements
WHEN NEW.replaces_id = NEW.id
BEGIN SELECT RAISE(ABORT, '言明は自分自身を置き換えられません'); END;
"""
    + "".join(_valid_petals_trigger(table) for table in ("statements", "model_elements", "question_logs"))
    + _word_triggers()
)

# 表の名前と型の対応。列が型と食い違わないことを tests/test_schema.py で確かめる。
TABLE_MODELS: dict[str, type[BaseModel]] = {
    "users": s.User,
    "delegations": s.Delegation,
    "access_logs": s.AccessLog,
    "conversations": s.Conversation,
    "photos": s.Photo,
    "letters": s.Letter,
    "utterances": s.Utterance,
    "comments": s.Comment,
    "statements": s.Statement,
    "interpretations": s.Interpretation,
    "model_elements": s.ModelElement,
    "consolidation_runs": s.ConsolidationRun,
    "inference_logs": s.InferenceLog,
    "question_logs": s.QuestionLog,
    "care_plan_drafts": s.CarePlanDraft,
    "ai_calls": s.AICall,
    "evaluations": s.Evaluation,
    "rooms": s.Room,
    "room_members": s.RoomMember,
    "twin_presences": s.TwinPresence,
    "room_messages": s.RoomMessage,
    "family_links": s.FamilyLink,
    "map_places": s.MapPlace,
    "map_links": s.MapLink,
    "person_candidates": s.PersonCandidate,
    "split_marks": s.SplitMark,
    "job_failures": s.JobFailure,
}

# デモの利用者（架空の4人）。番号を固定し、起動し直しても同じ人を指すようにする。
# (番号, 名前, 立場, 続柄, 住まい)
DEMO_USERS = [
    (1, "誠一さん", s.Role.SELF, None, None),
    (2, "美穂さん", s.Role.FAMILY, "長女", s.Residence.NEAR),
    (3, "節子さん", s.Role.CONNECTED, "ご近所の友人", s.Residence.NEAR),
    (4, "田中さん", s.Role.PROFESSIONAL, "ケアマネジャー", s.Residence.NEAR),
]

# 美穂さんは託された人、田中さんは招かれた専門職として、最初から設定しておく。
DEMO_DELEGATIONS = [
    (2, s.ShareScope.DECISION_SUPPORT, "2026-04-01"),
    (4, s.ShareScope.DECISION_SUPPORT, "2026-04-01"),
]


def connect(path: Path | str) -> sqlite3.Connection:
    """接続を作る。

    SQLite は外部キーの確認が既定で無効なので、接続のたびに有効にする。
    FastAPI は依存の処理と本体を別のスレッドで動かすことがあるので、
    check_same_thread を外す。接続は1回の要求の中でだけ使い、使い回さない。
    """
    connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # 部屋の発言を3秒ごとに読みながら、裏で言明を保存する。読む人と書く人が待たせ合わないようにする。
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(path: Path | str, *, seed_demo: bool = True) -> None:
    """表とトリガーを作る。デモの利用者と託す設定は、表を初めて作るときにだけ入れる。

    託す設定を起動のたびに入れ直すと、本人が取り消した設定が起動し直しで戻ってしまう。
    自分用の起動（seed_demo=False）では架空の人を入れない。
    関連するテスト：tests/test_schema.py、tests/test_modes.py
    """
    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_file)
    try:
        first_time = not _has_users_table(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if not first_time and version == 0:
            raise OldDatabaseError(
                f"古い形のデータベースです。{db_file.name} を別の名前に移してから起動してください。"
            )
        latest = max([BASE_SCHEMA_VERSION, *(number for number, _ in MIGRATIONS)])
        if not first_time:
            _migrate(connection, db_file, version)
        connection.executescript(SCHEMA + TRIGGERS)
        connection.execute(f"PRAGMA user_version = {latest}")
        if first_time and seed_demo:
            with connection:
                connection.executemany(
                    "INSERT INTO users (id, name, role, called_name, relation, residence) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (user_id, name, role.value, name, relation, residence.value if residence else None)
                        for user_id, name, role, relation, residence in DEMO_USERS
                    ],
                )
                connection.executemany(
                    "INSERT INTO delegations (delegate_user_id, scope, decided_on) "
                    "VALUES (?, ?, ?)",
                    [(user_id, scope.value, day) for user_id, scope, day in DEMO_DELEGATIONS],
                )
    finally:
        connection.close()


def _has_users_table(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    return row is not None


def _migrate(connection: sqlite3.Connection, db_file: Path, version: int) -> None:
    """まだ適用していない表の変更を、番号の順に適用する。

    適用の前にファイルを隣に控える。本人の言葉は作り直せないので、変更に失敗しても戻せるようにする。
    控えを取る前に WAL の中身をファイルへ書き戻す。書き戻さないと控えに最近の記録が入らないため。
    関連するテスト：tests/test_schema.py
    """
    for number, statements in sorted(MIGRATIONS):
        if number <= version:
            continue
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(db_file, db_file.with_name(f"{db_file.name}.before_v{number}"))
        with connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number}")


def new_id(connection: sqlite3.Connection, table: str) -> int:
    """隠れる行がある表の、新しい番号を作る。

    時刻を含む形（ULID など）は順番と時刻が読めるので使わない。同じ番号が出たら作り直す。
    入れるときの一意の制約の失敗を捕まえて再試行しないのは、追記だけのトリガーの拒否も
    同じ例外で出るので、本当の違反を隠してしまうため。
    """
    while True:
        candidate = secrets.randbelow(MAX_ID) + 1
        exists = connection.execute(f"SELECT 1 FROM {table} WHERE id = ?", (candidate,)).fetchone()
        if not exists:
            return candidate


def insert_row(connection: sqlite3.Connection, table: str, values: dict) -> int:
    """隠れる行がある表に、番号を作って1行入れ、その番号を返す。

    コミットはしない。発言とコメントのように、複数の行を一緒に入れられるようにするため。
    rowid と番号は別のものなので、cursor.lastrowid を番号として使ってはいけない。
    """
    row_id = new_id(connection, table)
    row = {"id": row_id}
    for column, value in values.items():
        row[column] = json.dumps(value, ensure_ascii=False) if column in JSON_COLUMNS else value
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))
    return row_id


def row_to_model(row: sqlite3.Row, model: type[ModelT]) -> ModelT:
    """データベースの1行を型に変換する。JSON の列は配列に戻す。"""
    values = dict(row)
    for column in JSON_COLUMNS & values.keys():
        values[column] = json.loads(values[column])
    return model.model_validate(values)


def list_users(connection: sqlite3.Connection) -> list[s.User]:
    """はずしていない人を返す。亡くなった方も含む。本人を先頭にし、あとは登録の順に並べる。"""
    rows = connection.execute(
        "SELECT * FROM users WHERE removed_at IS NULL ORDER BY role <> ?, rowid",
        (s.Role.SELF.value,),
    ).fetchall()
    return [row_to_model(row, s.User) for row in rows]


def login_users(connection: sqlite3.Connection) -> list[s.User]:
    """ログインできる人を返す。はずした人と亡くなった方は入らない。"""
    return [user for user in list_users(connection) if not user.is_deceased]


def self_user(connection: sqlite3.Connection) -> s.User | None:
    """本人を返す。まだ登録していなければ None を返す。"""
    row = connection.execute(
        "SELECT * FROM users WHERE role = ? AND removed_at IS NULL LIMIT 1", (s.Role.SELF.value,)
    ).fetchone()
    return row_to_model(row, s.User) if row else None


def get_user(connection: sqlite3.Connection, user_id: int) -> s.User | None:
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_model(row, s.User) if row else None


def log_ai_call(
    connection: sqlite3.Connection,
    *,
    role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    target: str | None = None,
    is_batch: bool = False,
    seconds: float | None = None,
) -> None:
    """AI の呼び出しを記録する。どのモデルで作ったかと、費用の元になるトークン数を残す。"""
    with connection:
        connection.execute(
            "INSERT INTO ai_calls "
            "(role, model, input_tokens, output_tokens, is_batch, target, called_at, seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                role,
                model,
                input_tokens,
                output_tokens,
                int(is_batch),
                target,
                datetime.now().astimezone().isoformat(),
                seconds,
            ),
        )


def log_job_failure(connection: sqlite3.Connection, job: str, target: str | None, error: BaseException) -> None:
    """裏の処理の失敗を記録する。本文を残さないよう、例外の種類だけを書く。"""
    with connection:
        connection.execute(
            "INSERT INTO job_failures (job, target, message, failed_at) VALUES (?, ?, ?, ?)",
            (job, target, type(error).__name__, datetime.now().astimezone().isoformat()),
        )


def log_access(connection: sqlite3.Connection, viewer_user_id: int, target: str) -> None:
    """閲覧の記録を残す。誰がいつ何を開いたかを表に追記する。"""
    with connection:
        connection.execute(
            "INSERT INTO access_logs (viewer_user_id, target, opened_at) VALUES (?, ?, ?)",
            (viewer_user_id, target, datetime.now().astimezone().isoformat()),
        )
