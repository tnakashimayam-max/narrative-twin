"""言明の台帳のテスト。

役割：台帳が「追記だけ」であること、本人の言葉が原文のままであることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：3（出どころを分ける）、5（訂正しても元の記録を消さない）、9（一字も変えない）。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import ledger
from app.ledger import LedgerError
from app.schemas import ConfirmStatus, Petal, ShareScope, Source


# ---- 追記 ----


def test_追記すると行が増え_内容がそのまま読める(conn, add_utterance, make_draft):
    utterance = add_utterance("朝の散歩が好きです。")

    statement = ledger.append_statement(
        conn, make_draft([utterance.id], "朝の散歩が好きです。")
    )

    assert statement.id is not None
    assert statement.created_at is not None
    assert ledger.get_statement(conn, statement.id) == statement
    assert statement.body == "朝の散歩が好きです。"
    assert statement.source_utterance_ids == [utterance.id]


def test_本人の言葉は原文の抜き出しなら入る(conn, add_utterance, make_draft):
    utterance = add_utterance("雨の日は家にいます。朝の散歩が好きです。")

    statement = ledger.append_statement(
        conn, make_draft([utterance.id], "朝の散歩が好きです。")
    )

    assert statement.body == "朝の散歩が好きです。"


def test_本人の言葉は言い換えると入らない(conn, add_utterance, make_draft):
    utterance = add_utterance("朝の散歩が好きです。")

    with pytest.raises(LedgerError):
        ledger.append_statement(conn, make_draft([utterance.id], "朝に散歩をするのが好きです。"))


@pytest.mark.parametrize("count", [0, 2])
def test_本人の言葉の根拠の発言はちょうど1つ(conn, add_utterance, make_draft, count):
    ids = [add_utterance("朝の散歩が好きです。").id for _ in range(count)]

    with pytest.raises(LedgerError):
        ledger.append_statement(conn, make_draft(ids, "朝の散歩が好きです。"))


def test_家族の発言を本人の言葉として入れられない(conn, users, add_utterance, make_draft):
    utterance = add_utterance("父は朝の散歩が好きです。", speaker="美穂さん")

    with pytest.raises(LedgerError):
        ledger.append_statement(conn, make_draft([utterance.id], "朝の散歩が好きです。"))


def test_家族の証言として本人のことを記録できる(conn, users, add_utterance, make_draft):
    """TSS：話した人は美穂さん、誰についての記録かは誠一さん、と分けて持つ。"""
    utterance = add_utterance("父は朝の散歩が好きです。", speaker="美穂さん")

    statement = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            "朝の散歩が好きなようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.FAMILY,
        ),
    )

    assert statement.speaker_user_id == users["美穂さん"].id
    assert statement.subject_user_id == users["誠一さん"].id


def test_AIの推測はツインの発言としてまたがって持てる(conn, users, add_utterance, make_draft):
    first = add_utterance("朝の散歩が好きです。")
    second = add_utterance("庭の花も見ます。")

    statement = ledger.append_statement(
        conn,
        make_draft(
            [first.id, second.id],
            "外の空気を大切にしているのかもしれません",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
        ),
    )

    assert statement.speaker_is_twin is True
    assert statement.source_utterance_ids == [first.id, second.id]


def test_存在しない発言を根拠にできない(conn, make_draft):
    with pytest.raises(LedgerError):
        ledger.append_statement(conn, make_draft([99999], "朝の散歩が好きです。"))


def test_根拠の発言がない言明は入らない(conn, users, make_draft):
    with pytest.raises(LedgerError):
        ledger.append_statement(
            conn,
            make_draft(
                [],
                "朝の散歩が好きなようだ",
                source=Source.FAMILY_TESTIMONY,
                speaker_user_id=users["美穂さん"].id,
            ),
        )


# ---- 出どころごとの話し手 ----

# 出どころごとに書ける人。ツインは利用者ではないので None で表す。
# 期待する表はコードから読まず、ここに書き写す。
ALLOWED_SPEAKERS = {
    Source.SELF_WORDS: {"誠一さん"},
    Source.OBSERVATION: {"美穂さん", "田中さん"},
    Source.FAMILY_TESTIMONY: {"美穂さん"},
    Source.CONNECTED_TESTIMONY: {"節子さん"},
    Source.AI_GUESS: {None},
}
SPEAKERS = ["誠一さん", "美穂さん", "節子さん", "田中さん", None]


def _draft_by(users, make_draft, utterance_id, source, speaker):
    return make_draft(
        [utterance_id],
        "朝の散歩が好きです。",
        source=source,
        speaker_user_id=None if speaker is None else users[speaker].id,
        speaker_is_twin=speaker is None,
    )


@pytest.mark.parametrize(
    ("source", "said_by", "written_by"),
    [
        (Source.FAMILY_TESTIMONY, "節子さん", "美穂さん"),
        (Source.FAMILY_TESTIMONY, "誠一さん", "美穂さん"),
        (Source.FAMILY_TESTIMONY, None, "美穂さん"),
        (Source.CONNECTED_TESTIMONY, "美穂さん", "節子さん"),
        (Source.OBSERVATION, "美穂さん", "田中さん"),
        (Source.OBSERVATION, None, "美穂さん"),
        (Source.SELF_WORDS, "美穂さん", "誠一さん"),
    ],
)
def test_話した人を根拠の発言の話し手と違う人にできない(
    conn, users, add_utterance, make_draft, source, said_by, written_by
):
    utterance = add_utterance("朝の散歩が好きです。", speaker=said_by, is_twin=said_by is None)

    with pytest.raises(LedgerError, match="話し手"):
        ledger.append_statement(conn, _draft_by(users, make_draft, utterance.id, source, written_by))


def test_AIの推測は人の発言を根拠にできる(conn, users, add_utterance, make_draft):
    utterance = add_utterance("朝の散歩が好きです。", speaker="美穂さん")
    draft = make_draft(
        [utterance.id],
        "外の空気を大切にしているのかもしれません",
        source=Source.AI_GUESS,
        speaker_user_id=None,
        speaker_is_twin=True,
    )

    assert ledger.append_statement(conn, draft).speaker_is_twin is True


@pytest.mark.parametrize("speaker", SPEAKERS, ids=lambda s: s or "ツイン")
@pytest.mark.parametrize("source", ALLOWED_SPEAKERS.keys(), ids=lambda s: s.value)
def test_出どころごとに書ける人が決まっている(conn, users, add_utterance, make_draft, source, speaker):
    # 話した人と根拠の発言の話し手を揃える。ここでは役割の決まりだけを確かめるため。
    utterance = add_utterance(
        "朝の散歩が好きです。", speaker=speaker or "誠一さん", is_twin=speaker is None
    )
    draft = _draft_by(users, make_draft, utterance.id, source, speaker)

    if speaker in ALLOWED_SPEAKERS[source]:
        assert ledger.append_statement(conn, draft).source is source
    else:
        with pytest.raises(LedgerError):
            ledger.append_statement(conn, draft)


# ---- 確認の状態は未確認から始まる ----


@pytest.mark.parametrize(
    "status",
    [ConfirmStatus.CONFIRMED, ConfirmStatus.CORRECTED, ConfirmStatus.RESTING],
    ids=lambda s: s.value,
)
def test_追記は未確認からしか始められない(conn, add_utterance, make_draft, status):
    utterance = add_utterance("朝の散歩が好きです。")

    with pytest.raises(LedgerError):
        ledger.append_statement(
            conn, make_draft([utterance.id], "朝の散歩が好きです。", confirm_status=status)
        )


def test_本人の訂正は訂正の処理からしか入らない(conn, add_utterance, make_draft):
    """元の行を指さない訂正を作らせない。"""
    utterance = add_utterance("朝の散歩が好きです。")

    with pytest.raises(LedgerError):
        ledger.append_statement(
            conn,
            make_draft([utterance.id], "朝の散歩が好きです。", source=Source.SELF_CORRECTION),
        )


# ---- 訂正（新しい行として追記し、元の行を指す）----


def test_訂正は新しい行を足し_元の行はそのまま残る(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。", scope=ShareScope.FAMILY)
    said = add_utterance("娘とは毎日話します。")

    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )

    assert corrected.id != original.id
    assert corrected.replaces_id == original.id
    assert corrected.source is Source.SELF_CORRECTION
    assert corrected.confirm_status is ConfirmStatus.CORRECTED
    assert corrected.body == "娘とは毎日話します。"
    # 元の行は1文字も変わらず残る（原則5）
    assert ledger.get_statement(conn, original.id) == original


def test_訂正は元の共有範囲を引き継ぐ(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。", scope=ShareScope.DECISION_SUPPORT)
    said = add_utterance("娘とは毎日話します。")

    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )

    assert corrected.share_scope is ShareScope.DECISION_SUPPORT


def test_訂正で花びらは変えられる(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("娘とは毎日話します。")

    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
        petals=[Petal.COMFORT, Petal.IDENTITY],
    )

    assert corrected.petals == [Petal.COMFORT, Petal.IDENTITY]
    assert ledger.get_statement(conn, original.id).petals == original.petals


def test_訂正の本文も発言の原文の抜き出しでなければならない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("娘とは毎日話します。")

    with pytest.raises(LedgerError):
        ledger.correct_statement(
            conn,
            original.id,
            actor_user_id=users["誠一さん"].id,
            utterance_id=said.id,
            body="娘とはほぼ毎日話します。",
        )


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_本人以外は訂正できない(conn, users, add_statement, add_utterance, name):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("娘とは毎日話します。")

    with pytest.raises(LedgerError):
        ledger.correct_statement(
            conn,
            original.id,
            actor_user_id=users[name].id,
            utterance_id=said.id,
            body="娘とは毎日話します。",
        )


def test_本人が話していない発言では訂正できない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("父は娘と毎日話します。", speaker="美穂さん")

    with pytest.raises(LedgerError):
        ledger.correct_statement(
            conn,
            original.id,
            actor_user_id=users["誠一さん"].id,
            utterance_id=said.id,
            body="娘と毎日話します。",
        )


def test_同じ行を2回置き換えられない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    first = add_utterance("娘とは毎日話します。")
    second = add_utterance("娘とは二日に一度話します。")
    ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=first.id,
        body="娘とは毎日話します。",
    )

    with pytest.raises(LedgerError):
        ledger.correct_statement(
            conn,
            original.id,
            actor_user_id=users["誠一さん"].id,
            utterance_id=second.id,
            body="娘とは二日に一度話します。",
        )


# ---- 先頭（いま有効な行）を求める ----


def test_先頭は訂正の連鎖を最後までたどる(conn, users, add_statement, add_utterance):
    first = add_statement("娘とは週に一度話します。")
    second_said = add_utterance("娘とは毎日話します。")
    second = ledger.correct_statement(
        conn,
        first.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=second_said.id,
        body="娘とは毎日話します。",
    )
    third_said = add_utterance("娘とは二日に一度話します。")
    third = ledger.correct_statement(
        conn,
        second.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=third_said.id,
        body="娘とは二日に一度話します。",
    )

    assert ledger.head_of(conn, first.id) == third
    assert ledger.head_of(conn, second.id) == third
    assert ledger.head_of(conn, third.id) == third


def test_いま有効な一覧に置き換えられた行は入らない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    other = add_statement("朝の散歩が好きです。")
    said = add_utterance("娘とは毎日話します。")
    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )

    ids = {statement.id for statement in ledger.current_statements(conn)}

    assert ids == {other.id, corrected.id}


# ---- データベースの決まりで守る（Python を通らない書き込みも止める）----


def test_UPDATEはデータベースが拒否する(conn, add_statement):
    statement = add_statement("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE statements SET body = '書き換え' WHERE id = ?", (statement.id,))

    assert ledger.get_statement(conn, statement.id) == statement


def test_DELETEはデータベースが拒否する(conn, add_statement):
    statement = add_statement("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("DELETE FROM statements WHERE id = ?", (statement.id,))

    assert ledger.get_statement(conn, statement.id) == statement


def test_INSERT_OR_REPLACEでも上書きできない(conn, add_statement):
    """REPLACE は行を消してから入れ直す。消す処理のトリガーをすり抜けるので別に止める。"""
    statement = add_statement("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO statements (
                    id, source_utterance_ids, subject_user_id, speaker_user_id, speaker_is_twin,
                    source, confirm_status, body, period, theme, petals, share_scope,
                    effective_date, replaces_id, created_at
                ) VALUES (?, '[1]', 1, 1, 0, '本人の言葉', '未確認', '書き換え', '今', '家族', '[]',
                          '本人だけ', '2026-11-02', NULL, '2026-11-02T00:00:00')
                """,
                (statement.id,),
            )

    assert ledger.get_statement(conn, statement.id) == statement


_RAW_STATEMENT = """
    {verb} INTO statements (
        id, source_utterance_ids, subject_user_id, speaker_user_id, speaker_is_twin,
        source, confirm_status, body, period, theme, petals, share_scope,
        effective_date, replaces_id, created_at
    ) VALUES (?, '[]', 1, 2, 0, '家族の証言', '未確認', '本文', '今', '家族', ?,
              '本人だけ', '2026-11-02', ?, '2026-11-02T00:00:00')
"""


def _raw_statement(verb="INSERT", id_value=777, petals="[]", replaces_id=None):
    """表に直接1行入れる SQL と値を作る。台帳の関数を通らない書き込みを試すため。"""
    return _RAW_STATEMENT.format(verb=verb), (id_value, petals, replaces_id)


def test_追記の順の番号を指定した上書きもできない(conn, add_statement):
    """番号の列とは別に、SQLite が行に振る rowid を指定した REPLACE も止める。"""
    statement = add_statement("朝の散歩が好きです。")
    rowid = conn.execute("SELECT rowid FROM statements WHERE id = ?", (statement.id,)).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError, match="追記だけ"):
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO statements (
                    rowid, id, source_utterance_ids, subject_user_id, speaker_user_id,
                    speaker_is_twin, source, confirm_status, body, period, theme, petals,
                    share_scope, effective_date, replaces_id, created_at
                ) VALUES (?, 777, '[]', 1, 2, 0, '家族の証言', '未確認', '書き換え', '今', '家族',
                          '[]', '本人だけ', '2026-11-02', NULL, '2026-11-02T00:00:00')
                """,
                (rowid,),
            )

    assert ledger.get_statement(conn, statement.id) == statement


def test_REPLACE_INTOでも上書きできない(conn, add_statement):
    statement = add_statement("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(*_raw_statement(verb="REPLACE", id_value=statement.id))

    assert ledger.get_statement(conn, statement.id) == statement


def test_置き換え先が重なるREPLACEでも前の訂正は消えない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("娘とは毎日話します。")
    corrected = ledger.correct_statement(
        conn,
        original.id,
        actor_user_id=users["誠一さん"].id,
        utterance_id=said.id,
        body="娘とは毎日話します。",
    )

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(*_raw_statement(verb="INSERT OR REPLACE", replaces_id=original.id))

    assert ledger.get_statement(conn, corrected.id) == corrected


def test_発言もINSERT_OR_REPLACEで上書きできない(conn, add_utterance):
    utterance = add_utterance("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO utterances (
                    id, conversation_id, speaker_user_id, text, spoken_at, place
                ) VALUES (?, ?, ?, '書き換え', '2026-11-02T10:00:00', '日記と会話')
                """,
                (utterance.id, utterance.conversation_id, utterance.speaker_user_id),
            )

    assert ledger.get_utterance(conn, utterance.id) == utterance


def test_自分自身を置き換える行は入らない(conn):
    with pytest.raises(sqlite3.IntegrityError, match="自分自身"):
        with conn:
            conn.execute(*_raw_statement(id_value=1000, replaces_id=1000))


def test_輪ができていても先頭を探す処理は止まる(db_path, conn):
    """外部キーの確認を切った接続でだけ作れる輪で、処理が止まらなくならないことを確かめる。"""
    raw = sqlite3.connect(db_path)
    try:
        with raw:
            raw.execute(*_raw_statement(id_value=1000, replaces_id=2000))
            raw.execute(*_raw_statement(id_value=2000, replaces_id=1000))
    finally:
        raw.close()

    with pytest.raises(LedgerError):
        ledger.head_of(conn, 1000)


@pytest.mark.parametrize(
    "petals",
    ['["ねこ"]', '["くつろぎ","くつろぎ"]', '"くつろぎ"', "[1]", "こわれた値"],
    ids=["決めた言葉でない", "重なり", "配列でない", "文字でない", "JSONでない"],
)
def test_花びらはデータベースでも決めた言葉だけ(conn, petals):
    with pytest.raises(sqlite3.IntegrityError, match="花びら"):
        with conn:
            conn.execute(*_raw_statement(petals=petals))


def test_花びらは重ねられない_型で止める(make_draft):
    with pytest.raises(ValidationError):
        make_draft([1], "本文", petals=[Petal.COMFORT, Petal.COMFORT])


def test_発言の原文も書き換えと削除を拒否する(conn, add_utterance):
    """言明が原文の抜き出しであることは、原文が変わらないから保てる。"""
    utterance = add_utterance("朝の散歩が好きです。")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE utterances SET text = '書き換え' WHERE id = ?", (utterance.id,))
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("DELETE FROM utterances WHERE id = ?", (utterance.id,))


def test_存在しない行は置き換え先にできない(conn):
    """外部キーの確認が有効なことも同時に確かめる（SQLite は既定で無効）。"""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with conn:
            conn.execute(*_raw_statement(replaces_id=99999))


# ---- 決めた言葉と個数 ----


def test_花びらは2個まで_型で止める(make_draft):
    with pytest.raises(ValidationError):
        make_draft([1], "本文", petals=[Petal.COMFORT, Petal.ATTACHMENT, Petal.IDENTITY])


def test_花びらは2個まで_データベースでも止める(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute(*_raw_statement(petals='["くつろぎ","結びつき","自分らしさ"]'))


def test_決めた言葉以外はデータベースが拒否する(conn):
    sql, values = _raw_statement()

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with conn:
            conn.execute(sql.replace("家族の証言", "想像"), values)


def test_話した人とツインは_どちらか一方だけ(make_draft, users):
    with pytest.raises(ValidationError):
        make_draft([1], "本文", speaker_user_id=users["誠一さん"].id, speaker_is_twin=True)
    with pytest.raises(ValidationError):
        make_draft([1], "本文", speaker_user_id=None, speaker_is_twin=False)


# ---- 書き換えの処理そのものが、コードにないこと ----


def test_アプリのコードに言明と発言の更新と削除がない():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    forbidden = re.compile(
        r"UPDATE\s+(OR\s+\w+\s+)?(statements|utterances)\b"
        r"|DELETE\s+FROM\s+(statements|utterances)\b"
        r"|INSERT\s+OR\s+REPLACE|REPLACE\s+INTO|ON\s+CONFLICT",
        re.IGNORECASE,
    )

    found = [
        path.name
        for path in app_dir.rglob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]

    assert found == []


def test_有効になった日は指定した日のまま残る(conn, add_utterance, make_draft):
    utterance = add_utterance("朝の散歩が好きです。")

    statement = ledger.append_statement(
        conn,
        make_draft([utterance.id], "朝の散歩が好きです。", effective_date=date(2030, 4, 1)),
    )

    assert ledger.get_statement(conn, statement.id).effective_date == date(2030, 4, 1)
