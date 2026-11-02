"""型と表が食い違わないことのテスト。

役割：Pydantic の型、データベースの表、決めた言葉が一致していることを確かめる。
4つの極：特定の極に属さない土台。
守る原則：3（出どころの言葉）、8（利用者は架空の4人）、10（共有範囲の言葉）。
"""

from __future__ import annotations

import re
import sqlite3
import types
import typing
from enum import StrEnum

import pytest

from app import db
from app import schemas as s

# 決めた言葉は、あえてコードから読まずにここへ書き写す。
# 言葉を変えるときは、意図して両方を直すことになる。
AGREED_WORDS = {
    s.Source: ["本人の言葉", "今の様子（観察）", "家族の証言", "縁のある人の証言", "AIの推測", "本人の訂正"],
    s.ConfirmStatus: ["未確認", "確認済み", "訂正済み", "お休み"],
    s.Period: ["子ども時代", "若いころ", "働き盛り", "退職後", "今"],
    s.Theme: [
        "家族",
        "仕事・役割",
        "住まい・土地",
        "好きなこと",
        "大切にしてきたこと",
        "人の手を借りること",
        "つらかったこと",
    ],
    s.Petal: ["くつろぎ", "結びつき", "共にあること", "たずさわること", "自分らしさ"],
    s.LifeStage: ["元気な時期", "要支援", "要介護"],
    s.ShareScope: ["本人だけ", "家族", "縁のある人まで", "意思決定支援用"],
    s.ElementKind: ["価値", "判断の決まり", "優先順位", "変化"],
    s.ElementStatus: ["AIの見立て", "本人確認済み"],
    s.Role: ["本人", "家族", "縁のある人", "専門職"],
    s.QuestionResult: ["答えた", "途中", "お休み", "また今度"],
    s.EvidenceVerdict: [
        "本人の言葉で裏付けあり",
        "家族・縁のある人の証言のみ",
        "AIの推測だけ（要確認）",
        "根拠なし",
    ],
    s.Place: ["日記と会話", "スレッド", "チャット", "会議", "グループ"],
    s.Gender: ["男性", "女性", "不明"],
    s.Residence: ["同居", "近く", "遠く"],
    s.FamilyLinkKind: ["親子", "夫婦"],
    s.RelativeKind: ["親", "子", "配偶者", "きょうだい", "その他"],
    s.CandidateStatus: ["候補", "登録した", "ちがう", "今はいい"],
    s.RoomKind: ["ツイン", "1対1", "グループ"],
    s.MessageKind: ["発言", "お知らせ"],
    s.PhotoKind: ["会話", "部屋", "便り", "プロフィール"],
    s.LinkStrength: ["強い", "ふつう", "弱い", "ストレス"],
    s.SupportDirection: ["本人から", "相手から", "お互い", "なし"],
    s.SplitTargetKind: ["会話", "便り"],
    s.CareKind: ["なし", "体の不調", "命に関わる", "強いつらさ"],
    s.GroupTurnKind: ["問い", "答え", "聞けていない", "書かない"],
    s.Perspective: ["表の言葉", "本当のニーズ", "本人の真意", "気持ち"],
    s.InterpretationStatus: ["仮説", "本人が確かめた"],
    s.ConsolidationUnit: ["月", "年", "人生の段階"],
    s.InferenceKind: [
        "次の問い",
        "スレッドの問い",
        "ツインの答え（チャット・会議）",
        "推定意思",
        "原案",
        "根拠チェック",
        "固定化警告",
    ],
    s.EvalQuestionType: ["今の考え", "変化", "知らないこと", "一字一句"],
    s.EvalMethod: ["意思モデル", "RAG"],
}


@pytest.mark.parametrize("enum_cls", AGREED_WORDS.keys(), ids=lambda cls: cls.__name__)
def test_決めた言葉がそのまま入っている(enum_cls):
    assert [member.value for member in enum_cls] == AGREED_WORDS[enum_cls]


def test_決めた言葉の型をすべて確かめている():
    defined = {
        value
        for value in vars(s).values()
        if isinstance(value, type) and issubclass(value, StrEnum) and value is not StrEnum
    }
    assert defined == set(AGREED_WORDS)


@pytest.mark.parametrize("table", db.TABLE_MODELS.keys())
def test_表の列と型の項目が一致する(conn, table):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    assert columns == set(db.TABLE_MODELS[table].model_fields)


def _enum_in(annotation) -> type[StrEnum] | None:
    """型の注釈から Enum を取り出す（Period | None のような形にも対応する）。"""
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        for arg in typing.get_args(annotation):
            found = _enum_in(arg)
            if found:
                return found
    return None


@pytest.mark.parametrize("table", db.TABLE_MODELS.keys())
def test_決めた言葉の列にはデータベースの確かめがある(conn, table):
    """言葉の確かめは、列を足さずに言葉を増やせるよう、CHECK ではなくトリガーで行う。"""
    for name, field in db.TABLE_MODELS[table].model_fields.items():
        enum_cls = _enum_in(field.annotation)
        if enum_cls is None:
            continue
        if (table, name) == ("letters", "share_with"):
            # 便りの共有の相手は、共有範囲のうち2つだけなので、表の CHECK で守る
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()["sql"]
            match = re.search(rf"CHECK \(\s*{name} IN \((.*?)\)", sql, re.S)
            allowed = set(re.findall(r"'([^']*)'", match.group(1)))
            assert allowed == {s.ShareScope.FAMILY.value, s.ShareScope.CONNECTED.value}
            continue
        assert db.WORD_COLUMNS[table][name] is enum_cls, f"{table}.{name} の確かめがありません"


def test_外部キーの確認が有効(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_利用者は架空の4人で_何度起動しても増えない(db_path, conn):
    db.init_db(db_path)
    db.init_db(db_path)

    assert [(user.name, user.role) for user in db.list_users(conn)] == [
        ("誠一さん", s.Role.SELF),
        ("美穂さん", s.Role.FAMILY),
        ("節子さん", s.Role.CONNECTED),
        ("田中さん", s.Role.PROFESSIONAL),
    ]
    assert conn.execute("SELECT COUNT(*) FROM delegations").fetchone()[0] == 2


def test_取り消した託す設定は起動し直しても戻らない(db_path, conn):
    with conn:
        conn.execute("DELETE FROM delegations")

    db.init_db(db_path)

    assert conn.execute("SELECT COUNT(*) FROM delegations").fetchone()[0] == 0


def _add_model_element(conn, petals="[]"):
    conn.execute(
        """
        INSERT INTO model_elements (
            id, kind, body, petals, confidence, status, valid_from, unit, version
        ) VALUES (777, '価値', '本文', ?, 0.5, 'AIの見立て', '2026-11-02', '月', 1)
        """,
        (petals,),
    )


def _add_question_log(conn, petals="[]"):
    conn.execute(
        "INSERT INTO question_logs (id, question, petals) VALUES (777, '問い', ?)", (petals,)
    )


@pytest.mark.parametrize(
    "add_row", [_add_model_element, _add_question_log], ids=["意思モデルの要素", "質問の記録"]
)
def test_花びらは追記でも決めた言葉だけ(conn, add_row):
    with pytest.raises(sqlite3.IntegrityError, match="花びら"):
        with conn:
            add_row(conn, petals='["ねこ"]')


@pytest.mark.parametrize(
    ("add_row", "table"),
    [(_add_model_element, "model_elements"), (_add_question_log, "question_logs")],
    ids=["意思モデルの要素", "質問の記録"],
)
def test_花びらは書き換えでも決めた言葉だけ(conn, add_row, table):
    with conn:
        add_row(conn)

    with pytest.raises(sqlite3.IntegrityError, match="花びら"):
        with conn:
            conn.execute(f"UPDATE {table} SET petals = ? WHERE id = 777", ('["ねこ"]',))

    with conn:
        conn.execute(f"UPDATE {table} SET petals = ? WHERE id = 777", ('["くつろぎ"]',))


def test_まだ聞いていない質問は聞いた日と結果を空にできる(conn):
    with conn:
        conn.execute("INSERT INTO question_logs (id, question) VALUES (1, 'まだ聞けていない問い')")

    row = conn.execute("SELECT * FROM question_logs WHERE id = 1").fetchone()
    log = db.row_to_model(row, s.QuestionLog)

    assert log.asked_on is None
    assert log.result is None


def test_スレッドの発言は便りに_それ以外は会話にぶら下げる(conn, users, conversation_id):
    speaker = users["誠一さん"].id
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO utterances (id, conversation_id, speaker_user_id, text, spoken_at, place)
                VALUES (777, ?, ?, '本文', '2026-11-02T10:00:00', 'スレッド')
                """,
                (conversation_id, speaker),
            )


def test_話した人とツインの両方を空にできない(conn, conversation_id):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO utterances (id, conversation_id, speaker_is_twin, text, spoken_at, place)
                VALUES (777, ?, 0, '本文', '2026-11-02T10:00:00', '日記と会話')
                """,
                (conversation_id,),
            )


# ---- 表の変更 ----


def test_表の変更は適用の前にファイルを控え_データを失わない(tmp_path, monkeypatch):
    path = tmp_path / "keep.db"
    db.init_db(path)
    connection = db.connect(path)
    with connection:
        connection.execute("UPDATE users SET memo = '残す記録' WHERE id = 1")
    connection.close()
    monkeypatch.setattr(db, "MIGRATIONS", [(2, ["ALTER TABLE users ADD COLUMN future_note TEXT"])])

    db.init_db(path)

    connection = db.connect(path)
    assert connection.execute("SELECT memo FROM users WHERE id = 1").fetchone()[0] == "残す記録"
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert [row["name"] for row in connection.execute("PRAGMA table_info(users)")][-1] == "future_note"
    connection.close()
    backup = tmp_path / "keep.db.before_v2"
    assert backup.exists()
    old = db.connect(backup)
    assert old.execute("SELECT memo FROM users WHERE id = 1").fetchone()[0] == "残す記録"
    assert "future_note" not in [row["name"] for row in old.execute("PRAGMA table_info(users)")]
    old.close()


def test_適用した表の変更はくり返さない(tmp_path, monkeypatch):
    path = tmp_path / "keep.db"
    db.init_db(path)
    monkeypatch.setattr(db, "MIGRATIONS", [(2, ["ALTER TABLE users ADD COLUMN future_note TEXT"])])

    db.init_db(path)
    db.init_db(path)

    connection = db.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    connection.close()


def test_古い形のデータベースは起動を止めて知らせる(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, role TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(db.OldDatabaseError):
        db.init_db(path)


def test_自分用の種なしの起動を2回しても人は増えない(tmp_path):
    path = tmp_path / "mine.db"

    db.init_db(path, seed_demo=False)
    db.init_db(path, seed_demo=False)

    connection = db.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    connection.close()
