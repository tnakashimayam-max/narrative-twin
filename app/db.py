"""データベースの表と接続。

役割：SQLite の表を作り、接続を渡し、行を型に変換する。デモの利用者4人を入れる。
4つの極：特定の極に属さない土台。
守る原則：5（言明の表は書き換えと削除を拒否する）、8（利用者は架空の4人）。

決めた言葉の制約（CHECK）は app/schemas.py の Enum から作る。手で書き写すと、
型と表の言葉が食い違う余地が生まれるため。関連するテスト：tests/test_schema.py
台帳の決まりの説明は docs/architecture.md にある。
"""

from __future__ import annotations

import json
import secrets
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


def _enum(column: str, enum_cls: type[StrEnum], *, nullable: bool = False) -> str:
    """決めた言葉だけが入る列の定義を作る。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    not_null = "" if nullable else " NOT NULL"
    return f"{column} TEXT{not_null} CHECK ({column} IN ({values}))"


def _petals(column: str = "petals") -> str:
    return (
        f"{column} TEXT NOT NULL DEFAULT '[]' "
        f"CHECK (json_valid({column}) AND json_array_length({column}) <= 2)"
    )


def _json_list(column: str) -> str:
    return f"{column} TEXT NOT NULL DEFAULT '[]' CHECK (json_valid({column}))"


_THREAD = s.Place.THREAD.value

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    {_enum("role", s.Role)}
);

CREATE TABLE IF NOT EXISTS delegations (
    id INTEGER PRIMARY KEY,
    delegate_user_id INTEGER NOT NULL REFERENCES users(id),
    {_enum("scope", s.ShareScope)},
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
    {_enum("theme", s.Theme, nullable=True)},
    {_enum("life_stage", s.LifeStage)},
    summary TEXT,
    self_confirmation TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    {_RANDOM_ID},
    file TEXT NOT NULL,
    taken_on TEXT,
    description TEXT
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
    {_enum("place", s.Place)},
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
    CHECK ((author_user_id IS NULL) = (is_twin_question = 1))
);

-- 言明の台帳：追記だけ。書き換えと削除は下のトリガーで拒否する
CREATE TABLE IF NOT EXISTS statements (
    {_RANDOM_ID},
    {_json_list("source_utterance_ids")},
    subject_user_id INTEGER NOT NULL REFERENCES users(id),
    speaker_user_id INTEGER REFERENCES users(id),
    speaker_is_twin INTEGER NOT NULL DEFAULT 0 CHECK (speaker_is_twin IN (0, 1)),
    {_enum("source", s.Source)},
    {_enum("confirm_status", s.ConfirmStatus)},
    body TEXT NOT NULL,
    {_enum("period", s.Period)},
    {_enum("theme", s.Theme)},
    {_petals()},
    {_enum("share_scope", s.ShareScope)},
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
    {_enum("perspective", s.Perspective)},
    {_enum("viewpoint_role", s.Role)},
    body TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    caution TEXT,
    {_json_list("evidence_statement_ids")},
    {_enum("status", s.InterpretationStatus)}
);

CREATE TABLE IF NOT EXISTS model_elements (
    {_RANDOM_ID},
    {_enum("kind", s.ElementKind)},
    body TEXT NOT NULL,
    condition TEXT,
    {_petals()},
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    {_enum("status", s.ElementStatus)},
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    {_json_list("evidence_statement_ids")},
    {_enum("unit", s.ConsolidationUnit)},
    version INTEGER NOT NULL,
    previous_version_id INTEGER REFERENCES model_elements(id),
    last_confirmed_on TEXT,
    unconfirmed_reconsolidations INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS consolidation_runs (
    id INTEGER PRIMARY KEY,
    {_enum("unit", s.ConsolidationUnit)},
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
    {_enum("kind", s.InferenceKind)},
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
    {_enum("theme", s.Theme, nullable=True)},
    {_enum("period", s.Period, nullable=True)},
    {_petals()},
    asked_on TEXT,
    {_enum("result", s.QuestionResult, nullable=True)}
);

CREATE TABLE IF NOT EXISTS care_plan_drafts (
    id INTEGER PRIMARY KEY,
    self_intent TEXT NOT NULL,
    long_term_goal TEXT NOT NULL,
    {_json_list("evidence_statement_ids")},
    {_json_list("evidence_element_ids")},
    {_enum("verdict", s.EvidenceVerdict)},
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
    called_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    {_enum("question_type", s.EvalQuestionType)},
    {_enum("method", s.EvalMethod)},
    result TEXT,
    is_correct INTEGER CHECK (is_correct IN (0, 1)),
    read_amount INTEGER
);
"""


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
    + """
CREATE TRIGGER IF NOT EXISTS statements_no_self_reference BEFORE INSERT ON statements
WHEN NEW.replaces_id = NEW.id
BEGIN SELECT RAISE(ABORT, '言明は自分自身を置き換えられません'); END;
"""
    + "".join(_valid_petals_trigger(table) for table in ("statements", "model_elements", "question_logs"))
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
}

# デモの利用者（架空の4人）。番号を固定し、起動し直しても同じ人を指すようにする。
DEMO_USERS = [
    (1, "誠一さん", s.Role.SELF),
    (2, "美穂さん", s.Role.FAMILY),
    (3, "節子さん", s.Role.CONNECTED),
    (4, "田中さん", s.Role.PROFESSIONAL),
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
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: Path | str) -> None:
    """表とトリガーを作る。デモの利用者と託す設定は、表を初めて作るときにだけ入れる。

    託す設定を起動のたびに入れ直すと、本人が取り消した設定が起動し直しで戻ってしまう。
    関連するテスト：tests/test_schema.py
    """
    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_file)
    try:
        first_time = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            is None
        )
        connection.executescript(SCHEMA + TRIGGERS)
        if first_time:
            with connection:
                connection.executemany(
                    "INSERT INTO users (id, name, role) VALUES (?, ?, ?)",
                    [(user_id, name, role.value) for user_id, name, role in DEMO_USERS],
                )
                connection.executemany(
                    "INSERT INTO delegations (delegate_user_id, scope, decided_on) "
                    "VALUES (?, ?, ?)",
                    [(user_id, scope.value, day) for user_id, scope, day in DEMO_DELEGATIONS],
                )
    finally:
        connection.close()


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
    rows = connection.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [row_to_model(row, s.User) for row in rows]


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
) -> None:
    """AI の呼び出しを記録する。どのモデルで作ったかと、費用の元になるトークン数を残す。"""
    with connection:
        connection.execute(
            "INSERT INTO ai_calls "
            "(role, model, input_tokens, output_tokens, is_batch, target, called_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                role,
                model,
                input_tokens,
                output_tokens,
                int(is_batch),
                target,
                datetime.now().astimezone().isoformat(),
            ),
        )


def log_access(connection: sqlite3.Connection, viewer_user_id: int, target: str) -> None:
    """閲覧の記録を残す。誰がいつ何を開いたかを表に追記する。"""
    with connection:
        connection.execute(
            "INSERT INTO access_logs (viewer_user_id, target, opened_at) VALUES (?, ?, ?)",
            (viewer_user_id, target, datetime.now().astimezone().isoformat()),
        )
