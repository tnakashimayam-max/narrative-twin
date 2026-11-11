"""流した物語を、リポジトリの JSON から作り直す。

役割：台本を本物の AI に流した結果を表ごとの JSON に書き出し、新しいクローンの最初の起動でデータベースへ読み込む。
      読み込みは AI を呼ばないので、API キーがなくても10年分の記録、要素、保存したツインの考えが開く。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：5（訂正の履歴を残す）、8（提出物とデモは架空）、10（本人だけの記録の絞り込みはサーバーが行う）。

データベースをそのままリポジトリに入れず JSON にするのは、中身を読めるようにし、1ファイルの大きさを抑えるため。
番号は流したときのまま読み込む。要素の根拠と、保存したツインの考えが言明を番号で指しているため。
追記の順で読む表があるので（room_messages など）、rowid も同じにする。

使い方：
  python -m fiction.story_data export 控えのデータベース

読み込みは app/demo.py の ensure_story が最初の起動で呼ぶ。手順：data/README.md
関連するテスト：tests/test_story_data.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

from app import config, db, ledger

DATA_DIR = config.ROOT / "data" / "select"
PHOTOS_DIR = config.ROOT / "data" / "select_photos"
TABLES_DIR_NAME = "tables"
# 流したときの費用の記録と、動かすたびに変わる記録は入れない。どの版を使うかは開いた人が選ぶので、選択も入れない
_LEFT_OUT = {"ai_calls", "ai_call_cache", "job_failures", "access_logs", "advocacy_plan_choices", "evaluations"}
# 行の多い表は年ごとに分ける。1ファイル 1MB 以下を目安にするため。値は年を読む列
_BY_YEAR = {
    "utterances": "spoken_at",
    "statements": "effective_date",
    "room_messages": "sent_at",
    "comments": "commented_at",
    "conversations": "conversation_date",
    "letters": "shared_at",
}
_INTERPRETATIONS_YEAR = "SELECT effective_date FROM statements WHERE id = interpretations.statement_id"


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [row[0] for row in rows if row[0] not in _LEFT_OUT]


def _rowid_is_a_column(conn: sqlite3.Connection, table: str) -> bool:
    """INTEGER PRIMARY KEY の表では rowid は列そのものなので、別に書かない。"""
    primary = [row for row in conn.execute(f'PRAGMA table_info("{table}")') if row[5]]
    return len(primary) == 1 and primary[0][2].upper() == "INTEGER"


def _write(path: Path, columns: list[str], rows: list) -> None:
    path.write_text(json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")


def export(db_path: Path, out_dir: Path | None = None) -> list[Path]:
    """控えを表ごとの JSON に書き出す。元のデータベースは読むだけ。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    tables_dir = (out_dir or DATA_DIR) / TABLES_DIR_NAME
    tables_dir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for table in _table_names(conn):
            names = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            columns = names if _rowid_is_a_column(conn, table) else ["rowid", *names]
            quoted = ", ".join(f'"{name}"' if name != "rowid" else "rowid" for name in columns)
            if table == "interpretations":
                year = f"substr(({_INTERPRETATIONS_YEAR}), 1, 4)"
            elif table in _BY_YEAR:
                year = f'substr("{_BY_YEAR[table]}", 1, 4)'
            else:
                year = "''"
            groups: dict[str, list] = {}
            for row in conn.execute(f'SELECT {year} AS y, {quoted} FROM "{table}" ORDER BY rowid'):
                groups.setdefault(row[0], []).append(list(row[1:]))
            for key, rows in sorted(groups.items()):
                path = tables_dir / (f"{table}_{key}.json" if key else f"{table}.json")
                _write(path, columns, rows)
                written.append(path)
    finally:
        conn.close()
    return written


def has_story(data_dir: Path | None = None) -> bool:
    return ((data_dir or DATA_DIR) / TABLES_DIR_NAME).is_dir()


def load(conn: sqlite3.Connection, data_dir: Path | None = None) -> dict[str, int]:
    """JSON をデータベースへ読み込み、表ごとの行数を返す。全部入るか、全部入らないかにする。

    種になった架空の人は流したときの人に置き換える。記録が入っているデータベースには呼ばない。
    """
    if conn.execute("SELECT 1 FROM utterances LIMIT 1").fetchone() is not None:
        raise ValueError("記録が入っているデータベースには読み込みません。")
    counts: dict[str, int] = {}
    files = sorted(((data_dir or DATA_DIR) / TABLES_DIR_NAME).glob("*.json"))
    with ledger.atomic(conn):
        # 表の順に関わらず読めるよう、外部キーの確かめを確定のときまで待つ
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("DELETE FROM delegations")
        conn.execute("DELETE FROM users")
        for path in files:
            part = json.loads(path.read_text(encoding="utf-8"))
            table = path.stem.rsplit("_", 1)[0] if path.stem.rsplit("_", 1)[-1].isdigit() else path.stem
            names = ", ".join(f'"{name}"' if name != "rowid" else "rowid" for name in part["columns"])
            marks = ", ".join("?" for _ in part["columns"])
            conn.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', part["rows"])
            counts[table] = counts.get(table, 0) + len(part["rows"])
    return counts


def copy_photos(photos_dir: Path, source: Path | None = None) -> int:
    """リポジトリの写真を、画面が読む置き場へ写す。写真の置き場がなければ何もしない。画面は写真なしでも崩れない。"""
    source = source or PHOTOS_DIR
    if not source.is_dir():
        return 0
    photos_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in source.iterdir():
        target = photos_dir / path.name
        if path.is_file() and not target.exists():
            shutil.copy2(path, target)
            copied += 1
    return copied


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[0] != "export":
        print("使い方：python -m fiction.story_data export 控えのデータベース", file=sys.stderr)
        return 2
    source = Path(arguments[1])
    if "mine" in {part.lower() for part in source.resolve().parts}:
        print("自分用の記録は扱いません。", file=sys.stderr)
        return 1
    written = export(source)
    print(f"書き出しました：{len(written)} ファイル、合計 {sum(path.stat().st_size for path in written) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
