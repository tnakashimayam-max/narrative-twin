"""架空用のデータベースの控えを取り、控えの時点へ戻す。

役割：デモの撮り直しのために、データベースと写真を控え、サーバーを止めて1つの命令で戻す。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：8（架空のデータだけを扱う。開発者の記録には触れない）。

控えは手で取る（python -m fiction.snapshot save）。前の物語で自動で取った種類の名前も、一覧に出せるよう残す。

戻すときも今のデータベースは消さず、data/trial/replaced/ へ移す。撮り直したあとで、前の状態が要ることがあるため。
動いているサーバーの下でファイルを入れ替えると壊れるので、サーバーが動いていれば戻さない。

開発者の記録に触れないよう、次のようにしている。
- 命令で指せるのは架空用の起動の保存先だけで、場所の指定を受け取らない。
- 自分用の起動と、mine という名前のフォルダを含む場所は断る。
- 確かめるポートは架空用の起動のものだけ。

使い方：
  python -m fiction.snapshot list
  python -m fiction.snapshot save
  python -m fiction.snapshot restore
  python -m fiction.snapshot restore --from 控えの名前

手順：data/README.md
関連するテスト：tests/test_snapshot.py、tests/test_demo.py
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import config

FIRST_START = "first-start"
BEFORE_ADVANCE = "before-advance"
MANUAL = "manual"
_LABELS = {FIRST_START: "最初の起動", BEFORE_ADVANCE: "「10年後へ進む」の直前", MANUAL: "手で取った控え"}
_DB_FILE = "narrative_twin.db"
_PHOTOS = "photos"
_FUTURE_STAGE = "future"
_OWN_RECORDS = "mine"


class SnapshotError(RuntimeError):
    """控えを取れない、または戻せないとき。理由を画面の言葉で持つ。"""


@dataclass(frozen=True)
class Snapshot:
    """1つの控え。"""

    name: str
    folder: Path

    @property
    def db_path(self) -> Path:
        return self.folder / _DB_FILE

    @property
    def photos_dir(self) -> Path:
        return self.folder / _PHOTOS

    @property
    def label(self) -> str:
        kind = self.name.split("-", 2)[-1]
        return _LABELS.get(kind, kind)


def _deployment() -> config.Deployment:
    """命令で扱うのは、架空用の起動の保存先だけ。"""
    return config.deployment(mine=False)


def refuse_own_records(deployment: config.Deployment) -> None:
    """自分用の起動と、自分用の記録の置き場を断る。"""
    if deployment.mine:
        raise SnapshotError("自分用の記録は扱いません。")
    for path in (deployment.db_path, deployment.photos_dir):
        if _OWN_RECORDS in {part.lower() for part in path.resolve().parts}:
            raise SnapshotError("自分用の記録は扱いません。")


def _root(deployment: config.Deployment) -> Path:
    return deployment.db_path.parent / "trial" / "snapshots"


def _aside_root(deployment: config.Deployment) -> Path:
    return deployment.db_path.parent / "trial" / "replaced"


def _has_advanced(path: Path) -> bool:
    """このデータベースが「10年後へ進む」のあとかを返す。読むだけ。"""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT 1 FROM story_marks WHERE stage = ?", (_FUTURE_STAGE,)).fetchone()
    finally:
        connection.close()
    return row is not None


def _new_folder(root: Path, kind: str) -> tuple[str, Path]:
    """名前は時刻から作る。並べると古い順になるよう、同じ時刻の控えを作らない。"""
    while True:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S%f")
        if not root.exists() or not any(root.glob(f"{stamp}-*")):
            name = f"{stamp}-{kind}"
            return name, root / name


def save(deployment: config.Deployment, kind: str = MANUAL) -> Snapshot:
    """今のデータベースと写真の控えを取る。進んだあとの状態は控えにしない。

    SQLite の backup を使う。サーバーが動いていて WAL に書きかけがあっても、そろった中身を写せるため。
    """
    refuse_own_records(deployment)
    if not deployment.db_path.exists():
        raise SnapshotError("データベースがありません。先に架空用のサーバーを起動してください。")
    if _has_advanced(deployment.db_path):
        raise SnapshotError("すでに10年後へ進んでいるので、控えにしません。")
    name, folder = _new_folder(_root(deployment), kind)
    folder.mkdir(parents=True)
    source = sqlite3.connect(deployment.db_path)
    target = sqlite3.connect(folder / _DB_FILE)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    if deployment.photos_dir.exists():
        shutil.copytree(deployment.photos_dir, folder / _PHOTOS)
    return Snapshot(name, folder)


def list_snapshots(deployment: config.Deployment) -> list[Snapshot]:
    """控えを古い順に返す。"""
    refuse_own_records(deployment)
    root = _root(deployment)
    if not root.exists():
        return []
    return [Snapshot(path.name, path) for path in sorted(root.iterdir()) if (path / _DB_FILE).exists()]


def _find(deployment: config.Deployment, name: str | None) -> Snapshot:
    """控えを選ぶ。名前がなければいちばん新しいもの。名前で控えの置き場の外は指せない。"""
    snapshots = list_snapshots(deployment)
    if not snapshots:
        raise SnapshotError("控えがありません。")
    if name is None:
        return snapshots[-1]
    for snapshot in snapshots:
        if snapshot.name == name:
            return snapshot
    raise SnapshotError(f"「{name}」という控えはありません。python -m fiction.snapshot list で確かめてください。")


def server_is_running(port: int) -> bool:
    """このポートで動いているものがあるかを返す。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _live_paths(deployment: config.Deployment) -> list[Path]:
    """入れ替える今のファイル。WAL も一緒に移す。残すと戻した中身に古い書きかけが混ざるため。"""
    db_path = deployment.db_path
    return [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm"), deployment.photos_dir]


def _put_back(moved: list[tuple[Path, Path]], deployment: config.Deployment) -> None:
    """途中で失敗したときに、書きかけを脇へ寄せて元のファイルを戻す。"""
    failed = _aside_root(deployment) / f"failed-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    for path in (deployment.db_path, deployment.photos_dir):
        if path.exists():
            failed.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(failed / path.name))
    for original, aside in moved:
        shutil.move(str(aside), str(original))


def restore(deployment: config.Deployment, name: str | None = None, *, port: int | None = None) -> tuple[Snapshot, Path]:
    """控えへ戻す。戻した控えと、今のファイルを移した先を返す。"""
    refuse_own_records(deployment)
    if server_is_running(deployment.port if port is None else port):
        raise SnapshotError("架空用のサーバーが動いています。止めてから、もう一度この命令を流してください。")
    snapshot = _find(deployment, name)
    if _has_advanced(snapshot.db_path):
        raise SnapshotError("この控えは10年後へ進んだあとのものなので、戻しません。")
    aside = _aside_root(deployment) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    aside.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    # 写真のない控えへ戻すときは、今の写真をそのまま使う。移すと画面の写真が消えるため
    live = [path for path in _live_paths(deployment) if snapshot.photos_dir.exists() or path != deployment.photos_dir]
    try:
        for path in live:
            if path.exists():
                shutil.move(str(path), str(aside / path.name))
                moved.append((path, aside / path.name))
        shutil.copy2(snapshot.db_path, deployment.db_path)
        if snapshot.photos_dir.exists():
            shutil.copytree(snapshot.photos_dir, deployment.photos_dir)
    except OSError as error:
        _put_back(moved, deployment)
        raise SnapshotError("ファイルを入れ替えられなかったので、元に戻しました。データベースを開いているものがないか確かめてください。") from error
    return snapshot, aside


def _counts(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        conversations = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        statements = connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
    finally:
        connection.close()
    return f"会話 {conversations}、言明の行 {statements}"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m fiction.snapshot", description="架空用のデータベースの控えを扱います。")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="控えの一覧を出します。")
    commands.add_parser("save", help="今の状態の控えを取ります。")
    restore_parser = commands.add_parser("restore", help="サーバーを止めたあとで、控えへ戻します。")
    restore_parser.add_argument("--from", dest="name", help="戻す控えの名前。省くといちばん新しい控えです。")
    args = parser.parse_args(argv)
    deployment = _deployment()
    try:
        if args.command == "list":
            snapshots = list_snapshots(deployment)
            if not snapshots:
                print("控えはまだありません。")
            for snapshot in snapshots:
                print(f"{snapshot.name}　{snapshot.label}　{_counts(snapshot.db_path)}")
        elif args.command == "save":
            snapshot = save(deployment)
            print(f"控えを取りました：{snapshot.name}（{_counts(snapshot.db_path)}）")
        else:
            snapshot, aside = restore(deployment, args.name)
            print(f"戻しました：{snapshot.name}　{snapshot.label}（{_counts(deployment.db_path)}）")
            print(f"戻す前のデータベースは {aside} に移しました。")
            print("次の命令でサーバーを起動してください：python -m app")
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
