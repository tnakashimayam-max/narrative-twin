"""架空用と自分用の起動のテスト。

役割：2つの起動の保存先、ポート、表示が分かれていることを確かめる。
4つの極：特定の極に属さない土台。
守る原則：8（提出物とデモは架空。開発者が自分の記録で試すときは手元だけに置く）。

テストでは本物の data/mine/ を使わない。一時的な場所を data/ の代わりに渡す。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import __main__ as entry
from app import config
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def _files_under(folder: Path) -> set[Path]:
    return {path for path in folder.rglob("*") if path.is_file()}


def test_架空用はポート8000でdataの直下に保存する(tmp_path):
    fictional = config.deployment(data_dir=tmp_path)

    assert fictional.port == 8000
    assert fictional.db_path == tmp_path / "narrative_twin.db"
    assert fictional.photos_dir == tmp_path / "photos"
    assert fictional.label == "架空データ"


def test_自分用はポート8001でmineの中に保存する(tmp_path):
    mine = config.deployment(mine=True, data_dir=tmp_path)

    assert mine.port == 8001
    assert mine.db_path.parent == tmp_path / "mine"
    assert mine.photos_dir.parent == tmp_path / "mine"
    assert mine.label == "自分の記録で試用中"


def test_起動の指定を読み分ける():
    assert entry.choose_deployment([]).mine is False
    assert entry.choose_deployment(["--mine"]).mine is True


def test_どちらの起動も127_0_0_1だけで待ち受ける():
    assert entry.HOST == "127.0.0.1"


def test_自分用の起動では_データが指定したmineの中だけに保存される(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    client = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=data_dir)))
    assert client.get("/api/users").status_code == 200

    written = _files_under(data_dir)
    assert written, "保存先にファイルができるはず"
    assert all((data_dir / "mine") in path.parents for path in written)


def test_自分用の起動には架空の人がいない(tmp_path):
    client = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))

    assert client.get("/api/users").json()["items"] == []


def test_架空用の起動には架空の4人がいる(tmp_path):
    client = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))

    assert len(client.get("/api/users").json()["items"]) == 4


def test_画面の隅の表示を起動の種類で分ける(tmp_path):
    fictional = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))
    mine = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))

    assert fictional.get("/api/mode").json() == {"mine": False, "label": "架空データ", "needs_self": False}
    assert mine.get("/api/mode").json() == {"mine": True, "label": "自分の記録で試用中", "needs_self": True}


def test_日付を動かす道は架空用の起動だけにある(tmp_path):
    fictional = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))
    mine = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))

    assert fictional.post("/api/dev/date", json={"date": "2030-05-01"}).json() == {"today": "2030-05-01"}
    assert mine.post("/api/dev/date", json={"date": "2030-05-01"}).status_code == 404


def test_自分の記録の置き場はGitに入らない():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "data/mine/" in ignore
    assert "data/photos/" in ignore


def test_ブラウザを操作する道具は架空用のポートだけを開ける():
    settings = (ROOT / ".mcp.json").read_text(encoding="utf-8")

    assert "127.0.0.1:8000" in settings
    assert "8001" not in settings
