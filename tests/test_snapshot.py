"""架空用のデータベースの控えと、戻す道具のテスト。

役割：控えの時点へ戻せること、今のファイルを消さないこと、サーバーが動いていれば戻さないこと、
      自分用の記録に触れないことを確かめる。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：8（架空のデータだけを扱う。開発者の記録には触れない）。

データベースはすべて一時フォルダに作り、本物の data/ には触れない。
流した物語と組み合わせた確かめは tests/test_demo.py にある。
"""

from __future__ import annotations

import shutil
import socket
import sqlite3

import pytest

from app import config, db
from fiction import snapshot

MARK = "控えのあとに入れた話"


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    target = config.deployment(data_dir=tmp_path / "data")
    db.init_db(target.db_path, seed_demo=True)
    target.photos_dir.mkdir(parents=True)
    (target.photos_dir / "1.jpg").write_bytes(b"first")
    # テストの間に本物のサーバーが動いていても、結果が変わらないようにする
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: False)
    return target


def _add_conversation(deployment, trigger=MARK):
    connection = db.connect(deployment.db_path)
    with connection:
        db.insert_row(connection, "conversations", {"conversation_date": "2026-11-03", "trigger": trigger, "life_stage": "元気な時期"})
    connection.close()


def _triggers(path):
    connection = sqlite3.connect(path)
    rows = connection.execute("SELECT trigger FROM conversations").fetchall()
    connection.close()
    return [row[0] for row in rows]


def _mark_advanced(path):
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("INSERT INTO story_marks (stage, loaded_at) VALUES ('future', '2036-05-01T00:00:00')")
    connection.close()


def test_控えへ戻すと_控えの時点のデータベースと写真になる(deployment):
    saved = snapshot.save(deployment)
    _add_conversation(deployment)
    (deployment.photos_dir / "1.jpg").write_bytes(b"changed")
    (deployment.photos_dir / "2.jpg").write_bytes(b"added")

    restored, _ = snapshot.restore(deployment)

    assert restored.name == saved.name
    assert MARK not in _triggers(deployment.db_path)
    assert (deployment.photos_dir / "1.jpg").read_bytes() == b"first"
    assert not (deployment.photos_dir / "2.jpg").exists()


def test_戻す前のデータベースと写真は消さずに脇へ移す(deployment):
    snapshot.save(deployment)
    _add_conversation(deployment)
    (deployment.photos_dir / "2.jpg").write_bytes(b"added")

    _, aside = snapshot.restore(deployment)

    assert MARK in _triggers(aside / "narrative_twin.db")
    assert (aside / "photos" / "2.jpg").read_bytes() == b"added"


def test_控えは書きかけのWALの中身も入る(deployment):
    connection = db.connect(deployment.db_path)
    with connection:
        db.insert_row(connection, "conversations", {"conversation_date": "2026-11-03", "trigger": "書きかけ", "life_stage": "元気な時期"})

    saved = snapshot.save(deployment)
    connection.close()

    assert "書きかけ" in _triggers(saved.db_path)


def test_サーバーが動いていると戻さず何も変えない(deployment, monkeypatch):
    snapshot.save(deployment)
    _add_conversation(deployment)
    monkeypatch.undo()
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        port = listening.getsockname()[1]

        with pytest.raises(snapshot.SnapshotError, match="動いています"):
            snapshot.restore(deployment, port=port)

    assert MARK in _triggers(deployment.db_path)


def test_動いているかを確かめるのは架空用のポートだけ(deployment, monkeypatch):
    asked = []
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: asked.append(port) or False)
    snapshot.save(deployment)

    snapshot.restore(deployment)

    assert asked == [config.deployment().port]


def test_控えがなければ戻さず何も変えない(deployment):
    _add_conversation(deployment)

    with pytest.raises(snapshot.SnapshotError, match="控えがありません"):
        snapshot.restore(deployment)

    assert MARK in _triggers(deployment.db_path)


def test_名前を省くといちばん新しい控えへ_名前を付けると選んだ控えへ戻す(deployment):
    first = snapshot.save(deployment, snapshot.FIRST_START)
    _add_conversation(deployment, "進む前の話")
    newest = snapshot.save(deployment, snapshot.BEFORE_ADVANCE)

    assert snapshot.list_snapshots(deployment)[-1].name == newest.name
    snapshot.restore(deployment)
    assert "進む前の話" in _triggers(deployment.db_path)

    snapshot.restore(deployment, first.name)
    assert "進む前の話" not in _triggers(deployment.db_path)


def test_同じ秒に取っても前の控えを上書きしない(deployment):
    names = {snapshot.save(deployment).name for _ in range(3)}

    assert len(names) == 3


def test_進んだあとの状態は控えにしない(deployment):
    _mark_advanced(deployment.db_path)

    with pytest.raises(snapshot.SnapshotError, match="進んでいる"):
        snapshot.save(deployment)


def test_進んだあとの中身の控えへは戻さない(deployment):
    saved = snapshot.save(deployment)
    _mark_advanced(saved.db_path)
    _add_conversation(deployment)

    with pytest.raises(snapshot.SnapshotError, match="進んだあと"):
        snapshot.restore(deployment)

    assert MARK in _triggers(deployment.db_path)


def test_入れ替えの途中で失敗すると元のファイルへ戻す(deployment, monkeypatch):
    snapshot.save(deployment)
    _add_conversation(deployment)

    def broken_copy(*_):
        raise OSError("ファイルを開いているものがある")

    monkeypatch.setattr(shutil, "copytree", broken_copy)
    with pytest.raises(snapshot.SnapshotError, match="元に戻しました"):
        snapshot.restore(deployment)

    assert MARK in _triggers(deployment.db_path)
    assert (deployment.photos_dir / "1.jpg").read_bytes() == b"first"


# ---- 自分用の記録に触れない ----


def test_命令で扱うのは架空用の起動だけ():
    assert snapshot._deployment().mine is False


def test_自分用の起動は控えも戻すことも一覧も断る(tmp_path):
    own = config.deployment(mine=True, data_dir=tmp_path)

    for action in (snapshot.save, snapshot.restore, snapshot.list_snapshots):
        with pytest.raises(snapshot.SnapshotError, match="自分用"):
            action(own)


def test_自分用の置き場を指す保存先も断る(tmp_path):
    folder = tmp_path / "mine"
    target = config.deployment(data_dir=folder)

    with pytest.raises(snapshot.SnapshotError, match="自分用"):
        snapshot.save(target)


def test_隣に自分用の記録があっても_写さず変えない(deployment):
    own = deployment.db_path.parent / "mine"
    own.mkdir()
    record = own / "narrative_twin.db"
    record.write_bytes(b"own record")
    stamp = record.stat().st_mtime_ns

    saved = snapshot.save(deployment)
    _add_conversation(deployment)
    _, aside = snapshot.restore(deployment)

    assert record.read_bytes() == b"own record"
    assert record.stat().st_mtime_ns == stamp
    assert not list(saved.folder.rglob("mine"))
    assert not list(aside.rglob("mine"))


def test_控えの名前で控えの置き場の外は指せない(deployment):
    snapshot.save(deployment)

    with pytest.raises(snapshot.SnapshotError, match="控えはありません"):
        snapshot.restore(deployment, "../mine")


# ---- 命令 ----


def test_命令で戻すと次の手順を知らせる(deployment, monkeypatch, capsys):
    monkeypatch.setattr(snapshot, "_deployment", lambda: deployment)
    assert snapshot.main(["save"]) == 0
    _add_conversation(deployment)

    assert snapshot.main(["restore"]) == 0

    output = capsys.readouterr().out
    assert "戻しました" in output
    assert "python -m app" in output
    assert MARK not in _triggers(deployment.db_path)


def test_命令で戻せないときは理由を出して1を返す(deployment, monkeypatch, capsys):
    monkeypatch.setattr(snapshot, "_deployment", lambda: deployment)

    assert snapshot.main(["restore"]) == 1
    assert "控えがありません" in capsys.readouterr().err


def test_一覧に控えの種類を出す(deployment, monkeypatch, capsys):
    monkeypatch.setattr(snapshot, "_deployment", lambda: deployment)
    snapshot.save(deployment, snapshot.BEFORE_ADVANCE)

    snapshot.main(["list"])

    assert "「10年後へ進む」の直前" in capsys.readouterr().out


def test_写真のない控えへ戻しても_今の写真は消さない(deployment):
    saved = snapshot.save(deployment)
    shutil.rmtree(saved.photos_dir)
    _add_conversation(deployment)

    snapshot.restore(deployment)

    assert MARK not in _triggers(deployment.db_path)
    assert (deployment.photos_dir / "1.jpg").read_bytes() == b"first"
