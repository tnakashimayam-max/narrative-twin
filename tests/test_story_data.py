"""流した物語を JSON から作り直す処理のテスト。

役割：新しいクローンの最初の起動で、AI を呼ばずに10年分の記録が開くことを確かめる。
4つの極：特定の極に属さない土台。
守る原則：5（訂正の履歴を残す）、8（提出物とデモは架空）、10（本人だけの記録は本人以外に見えない）。

リポジトリの data/select と data/select_photos を読むテストは、置き場がなければ飛ばす。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import clock, config, db, demo, ledger
from app.main import create_app
from app.schemas import ConversationDraft, Period, Petal, Place, ShareScope, Source, StatementDraft, Theme, UtteranceDraft
from fiction import story_data

MAX_PHOTO_BYTES = 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
# 付加情報を入れられる JPEG の区画。APP1（Exif など）から APP15 と、コメント
_INFO_SEGMENTS = {*range(0xE1, 0xF0), 0xFE}


@pytest.fixture
def source(tmp_path):
    """流し終えたデータベースの小さな見本。"""
    deployment = config.deployment(data_dir=tmp_path / "source")
    db.init_db(deployment.db_path, seed_demo=True)
    conn = db.connect(deployment.db_path)
    self_user = db.self_user(conn)
    conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=date(2033, 11, 17), trigger="試し", life_stage=ledger.life_stage_on(conn, date(2033, 11, 17))))
    said = ledger.add_utterance(conn, UtteranceDraft(conversation_id=conversation.id, speaker_user_id=self_user.id, text="家におれんようになったら、美穂が決めてよか。", spoken_at=datetime(2033, 11, 17, 23, 30), place=Place.DIARY_AND_TALK))
    first = ledger.append_statement(
        conn,
        StatementDraft(
            source_utterance_ids=[said.id],
            subject_user_id=self_user.id,
            speaker_user_id=self_user.id,
            source=Source.SELF_WORDS,
            body=said.text,
            period=Period.NOW,
            theme=Theme.HOME,
            petals=[Petal.COMFORT],
            share_scope=ShareScope.SELF_ONLY,
            effective_date=date(2033, 11, 17),
            created_at=datetime(2033, 11, 17, 23, 30),
        ),
    )
    ledger.confirm_statement(conn, first.id, actor_user_id=self_user.id, at=datetime(2033, 11, 26, 21, 50))
    conn.commit()
    conn.close()
    return deployment


@pytest.fixture
def exported(source, tmp_path):
    out = tmp_path / "select"
    story_data.export(source.db_path, out)
    return out


def _new_database(tmp_path):
    deployment = config.deployment(data_dir=tmp_path / "clone")
    db.init_db(deployment.db_path, seed_demo=True)
    return deployment


def test_書き出したJSONから同じ番号と履歴で作り直せる(source, exported, tmp_path):
    target = _new_database(tmp_path)
    conn = db.connect(target.db_path)

    story_data.load(conn, exported)

    original = sqlite3.connect(source.db_path)
    for table, column in (("statements", "id"), ("utterances", "id")):
        before = original.execute(f"SELECT rowid, {column} FROM {table} ORDER BY rowid").fetchall()
        after = [tuple(row) for row in conn.execute(f"SELECT rowid, {column} FROM {table} ORDER BY rowid")]
        assert after == before
    # 確かめた行は元の行を指したまま残る
    assert conn.execute("SELECT COUNT(*) FROM statements WHERE replaces_id IS NOT NULL").fetchone()[0] == 1
    original.close()
    conn.close()


def test_費用の記録と選んだ版は書き出さない(exported):
    names = {path.stem for path in (exported / "tables").glob("*.json")}

    assert not any(name.startswith(("ai_calls", "ai_call_cache", "job_failures", "access_logs", "advocacy_plan_choices")) for name in names)


def test_記録が入っているデータベースには読み込まない(source, exported):
    conn = db.connect(source.db_path)

    with pytest.raises(ValueError):
        story_data.load(conn, exported)
    conn.close()


def test_最初の起動で読み込み_二度目は読み込まず_AIを呼ばない(exported, tmp_path, monkeypatch):
    monkeypatch.setattr(story_data, "DATA_DIR", exported)
    monkeypatch.setattr(story_data, "PHOTOS_DIR", tmp_path / "ない写真")
    target = _new_database(tmp_path)

    assert demo.ensure_story(target) is True
    assert demo.ensure_story(target) is False

    conn = db.connect(target.db_path)
    assert conn.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    conn.close()


def test_読み込んだ物語は記録の日付で開く(exported, tmp_path, monkeypatch):
    monkeypatch.setattr(story_data, "DATA_DIR", exported)
    monkeypatch.setattr(story_data, "PHOTOS_DIR", tmp_path / "ない写真")
    target = _new_database(tmp_path)
    demo.ensure_story(target)

    app = create_app(deployment=target)
    assert demo.restore_day(app) is True
    assert clock.now(app).date().isoformat() == "2033-11-18"


def test_写真の置き場がなくても画面の一覧は開く(exported, tmp_path, monkeypatch):
    monkeypatch.setattr(story_data, "DATA_DIR", exported)
    monkeypatch.setattr(story_data, "PHOTOS_DIR", tmp_path / "ない写真")
    target = _new_database(tmp_path)
    demo.ensure_story(target)

    client = TestClient(create_app(deployment=target))

    assert client.get("/api/users").status_code == 200


def test_写真の置き場があれば_画面が読む場所へ写し_今ある写真は上書きしない(tmp_path):
    source_dir = tmp_path / "from"
    source_dir.mkdir()
    (source_dir / "a.jpg").write_bytes(b"new")
    (source_dir / "b.jpg").write_bytes(b"new")
    target = tmp_path / "to"
    target.mkdir()
    (target / "a.jpg").write_bytes(b"kept")

    assert story_data.copy_photos(target, source_dir) == 1
    assert (target / "a.jpg").read_bytes() == b"kept"
    assert (target / "b.jpg").read_bytes() == b"new"


# ---- リポジトリに入れたもの ----


def _photo_rows():
    path = story_data.DATA_DIR / "tables" / "photos.json"
    if not path.exists():
        pytest.skip("data/select がありません")
    part = json.loads(path.read_text(encoding="utf-8"))
    index = part["columns"].index("file")
    return [row[index] for row in part["rows"]]


def _info_segments(data: bytes) -> list[int]:
    """JPEG の見出しを順に読み、付加情報の区画の印を返す。画像の本体に入ったら読むのをやめる。"""
    assert data[:2] == b"\xff\xd8"
    found = []
    position = 2
    while position + 4 <= len(data):
        assert data[position] == 0xFF
        marker = data[position + 1]
        if marker == 0xDA:
            break
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        if marker in _INFO_SEGMENTS:
            found.append(marker)
        position += 2 + length
    return found


def test_リポジトリのJSONはどれも1MB以下(tmp_path):
    files = list(story_data.DATA_DIR.rglob("*"))
    if not files:
        pytest.skip("data/select がありません")
    too_big = [path.name for path in files if path.is_file() and path.stat().st_size > MAX_FILE_BYTES]

    assert too_big == []


def test_入れる写真は開くデータが使う分だけで_足りない写真もない():
    wanted = set(_photo_rows())
    if not story_data.PHOTOS_DIR.is_dir():
        pytest.skip("data/select_photos がありません")
    present = {path.name for path in story_data.PHOTOS_DIR.iterdir()}

    assert present == wanted


def test_入れる写真に付加情報がなく_1枚1MB以下():
    if not story_data.PHOTOS_DIR.is_dir():
        pytest.skip("data/select_photos がありません")
    photos = list(story_data.PHOTOS_DIR.iterdir())
    assert photos
    for path in photos:
        data = path.read_bytes()
        assert len(data) <= MAX_PHOTO_BYTES, path.name
        assert _info_segments(data) == [], path.name
