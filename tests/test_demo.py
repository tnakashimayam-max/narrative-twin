"""記録を整理する処理と、流した物語の起動のテスト。

役割：託す範囲が新しい言葉に当たること、記録の日付で開くこと、記録に物語を重ねないことを確かめる。
4つの極：3. 読み解き、4. 10年後に代弁する。
守る原則：8（自分用の起動では動かない）、10（本人が託す範囲を決める）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import ai, clock, config, db, demo, ledger
from app.main import create_app
from app.schemas import ConversationDraft, Period, Place, ShareScope, Source, StatementDraft, Theme, UtteranceDraft
from fiction import snapshot, story_data
from tests.fake_claude import RoleClaude


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def fresh(tmp_path):
    """架空の人だけのデータベースのアプリ。"""
    deployment = config.deployment(data_dir=tmp_path / "d")
    app = create_app(deployment=deployment)
    connection = db.connect(deployment.db_path)
    users = {user.name: user for user in db.list_users(connection)}
    yield app, connection, lambda name: TestClient(app, headers={"X-NT-User": str(users[name].id)}), users
    connection.close()


@pytest.fixture
def add_later():
    """本人の言葉を、発言ごと今日の日付で入れる。裏の分割が言明を作ったのと同じ形にする。"""

    def _add(conn, body, theme):
        self_user = db.self_user(conn)
        now = datetime.now().astimezone()
        conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=now.date()))
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(conversation_id=conversation.id, speaker_user_id=self_user.id, text=body, spoken_at=now, place=Place.DIARY_AND_TALK),
        )
        return ledger.append_statement(
            conn,
            StatementDraft(
                source_utterance_ids=[utterance.id],
                subject_user_id=self_user.id,
                speaker_user_id=self_user.id,
                source=Source.SELF_WORDS,
                body=body,
                period=Period.NOW,
                theme=theme,
                share_scope=ShareScope.SELF_ONLY,
                effective_date=now.date(),
                created_at=now,
            ),
        )

    return _add


def _set_handover(client, users):
    themes = [theme.value for theme in Theme if theme is not Theme.HARDSHIP]
    assert client.put("/api/handover", json={"themes": themes, "delegate_ids": [users["美穂さん"].id]}).status_code == 200


def test_託す範囲を決めたあとに入れた言葉も_記録を整理すると本人の決めた範囲になる(fresh, claude, add_later):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    shared = add_later(conn, "来年も畑をやりたい。", Theme.LIKES)
    claude.answers["固める処理"] = {"elements": []}

    assert me.post("/api/model/consolidate").status_code == 200

    assert ledger.head_of(conn, shared.id).share_scope is ShareScope.DECISION_SUPPORT


def test_本人が託さなかったテーマの言葉は_記録を整理しても広がらない(fresh, claude, add_later):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    private = add_later(conn, "兄のことはまだ話せない。", Theme.HARDSHIP)
    claude.answers["固める処理"] = {"elements": []}

    assert me.post("/api/model/consolidate").status_code == 200

    assert ledger.head_of(conn, private.id).share_scope is ShareScope.SELF_ONLY


def test_自分用の起動では物語の今日を返さない(tmp_path):
    deployment = config.deployment(mine=True, data_dir=tmp_path / "mine")
    app = create_app(deployment=deployment)
    connection = db.connect(deployment.db_path)
    with connection:
        connection.execute("INSERT INTO users (id, name, role) VALUES (1, '本人の試し', '本人')")
    connection.close()

    assert TestClient(app, headers={"X-NT-User": "1"}).get("/api/dev/story").status_code == 404


def test_物語がないときの起動でも_顔が入って開く(tmp_path, monkeypatch):
    # 本物の起動の入口を通す。サーバーの待ち受けと、止まった処理の流し直しは差し替える
    from PIL import Image

    from app import __main__ as entry
    from fiction import faces

    folder = tmp_path / "faces"
    folder.mkdir()
    for name in faces.FACE_FILES.values():
        Image.new("RGB", (64, 64), (200, 180, 150)).save(folder / name, format="JPEG")
    monkeypatch.setattr(config, "DEMO_FACES_DIR", folder)
    monkeypatch.setattr(story_data, "DATA_DIR", tmp_path / "ない物語")
    deployment = config.deployment(data_dir=tmp_path / "d")
    monkeypatch.setattr(entry, "choose_deployment", lambda: deployment)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry.jobs, "catch_up", lambda app: None)

    entry.main()

    connection = db.connect(deployment.db_path)
    rows = connection.execute("SELECT name, photo_id, profile_photo_id FROM users WHERE name = '誠一さん'").fetchall()
    utterances = connection.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    connection.close()
    assert rows[0]["photo_id"] and rows[0]["profile_photo_id"]
    assert utterances == 0


# ---- 流した物語のデータベース ----


@pytest.fixture
def story_db(tmp_path):
    """記録が入ったデータベース。最後に固めた日は2036年5月5日。"""
    deployment = config.deployment(data_dir=tmp_path / "data")
    db.init_db(deployment.db_path, seed_demo=True)
    connection = db.connect(deployment.db_path)
    self_user = db.self_user(connection)
    conversation = ledger.add_conversation(connection, ConversationDraft(conversation_date=date(2036, 5, 4), trigger="試し"))
    ledger.add_utterance(
        connection,
        UtteranceDraft(conversation_id=conversation.id, speaker_user_id=self_user.id, text="炊きたてなら、よか。", spoken_at=datetime(2036, 5, 4, 21, 0), place=Place.DIARY_AND_TALK),
    )
    connection.execute(
        """
        INSERT INTO consolidation_runs (id, unit, period_start, period_end, input_statement_ids, output_element_ids, model, input_tokens, output_tokens, run_at)
        VALUES (1, '月', '2036-05-01', '2036-05-31', '[]', '[]', 'claude-sonnet-5-5', 0, 0, '2036-05-05T23:59:00+09:00')
        """
    )
    connection.commit()
    connection.close()
    return deployment


def _utterances(deployment):
    connection = db.connect(deployment.db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    finally:
        connection.close()


def test_記録が入っているデータベースに物語を重ねない(story_db):
    before = _utterances(story_db)

    assert demo.ensure_story(story_db) is False

    assert _utterances(story_db) == before


def test_流した物語は起動し直しても同じ日付になる(story_db):
    first = create_app(deployment=story_db)
    assert demo.restore_day(first) is True
    assert clock.now(first).date() == date(2036, 5, 6)

    # 撮影中に記録を整理すると、最後に固めた日は動く。それでも日付は進まない
    connection = db.connect(story_db.db_path)
    connection.execute("UPDATE consolidation_runs SET run_at = '2036-05-06T10:00:00+09:00'")
    connection.commit()
    connection.close()

    second = create_app(deployment=story_db)
    assert demo.restore_day(second) is True
    assert clock.now(second).date() == date(2036, 5, 6)


def test_流した物語の日付の印があっても_控えとして出し入れできる(story_db, monkeypatch):
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: False)
    demo.restore_day(create_app(deployment=story_db))

    saved = snapshot.save(story_db)
    restored, _ = snapshot.restore(story_db, saved.name)

    assert restored.name == saved.name


def test_画面に今日の日付を返し_記録を足す入口はない(story_db):
    app = create_app(deployment=story_db)
    demo.restore_day(app)
    connection = db.connect(story_db.db_path)
    self_id = db.self_user(connection).id
    connection.close()
    client = TestClient(app, headers={"X-NT-User": str(self_id)})
    before = _utterances(story_db)

    assert client.get("/api/dev/story").json() == {"today": "2036-05-06"}
    assert client.post("/api/dev/advance").status_code in (404, 405)
    assert _utterances(story_db) == before
