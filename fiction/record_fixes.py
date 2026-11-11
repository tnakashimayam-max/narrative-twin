"""流し終えた物語の記録の文を、直した行として足す。

役割：台本を流したあとで、前後の文がないと意味が通らなかった記録の文を、元の行を指す新しい行として足す。
      元の行は消さず、履歴として残る（台帳は追記だけ）。決まりのトリガーは外さない。
      同じ番号と同じ文を、data/select の表と、架空用のデータベース（8000番、場面の控えなど）に入れる。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：5（訂正しても元の記録は消さず、履歴として残す）、8（提出物とデモは架空）。

直す文（tyumajinさんの決定、2026-11-07）：
  田中さんの2036年5月2日の書き込みから分けた「蓋は、誠一さんが開けました。」は、前の文がないと
  何の蓋か分からず、代弁の画面の今の様子の先頭に出ていた。書き込みの文の順に並べる直し
  （app/observations.py の now_view）とあわせて、1文で分かる文に直した行を足す。
  作成日時は直す元の行と同じにする。1つの書き込みから分けた文は作成日時が同じで、書き込みの文の順に並ぶため。

使い方：
  python -m fiction.record_fixes db データベース
  python -m fiction.record_fixes tables

理由：docs/decisions.md の「流したあとで当てた直し」
関連するテスト：tests/test_record_fixes.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app import config, db

TABLES = config.ROOT / "data" / "select" / "tables"

# 直した行。番号は一度だけ作って固定し、どのデータにも同じ番号で入れる
FIXES = (
    {
        "id": 913228718303475,
        "replaces_id": 4490136065192365,
        "body": "ヘルパーの初日、炊き上がった炊飯器の蓋を開けて、湯気の立つご飯を最初に確かめたのは誠一さんでした。",
        "table_file": "statements_2036.json",
    },
)
# 元の行から写す列。本文、番号、置き換え先のほかは元の行のままにする
_COPIED = (
    "source_utterance_ids",
    "subject_user_id",
    "speaker_user_id",
    "speaker_is_twin",
    "source",
    "confirm_status",
    "period",
    "theme",
    "petals",
    "share_scope",
    "effective_date",
    "created_at",
)


def apply_db(conn: sqlite3.Connection) -> int:
    """データベースに直した行を足し、足した数を返す。足し済みの行と、元の行がないデータでは何もしない。"""
    added = 0
    with conn:
        for fix in FIXES:
            if conn.execute("SELECT 1 FROM statements WHERE id = ?", (fix["id"],)).fetchone():
                continue
            original = conn.execute("SELECT * FROM statements WHERE id = ?", (fix["replaces_id"],)).fetchone()
            if original is None:
                continue
            values = {name: original[name] for name in _COPIED}
            values.update(id=fix["id"], replaces_id=fix["replaces_id"], body=fix["body"])
            names = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            conn.execute(f"INSERT INTO statements ({names}) VALUES ({marks})", list(values.values()))
            added += 1
    return added


def apply_tables(folder: Path = TABLES) -> int:
    """data/select の表に直した行を足し、足した数を返す。rowid は全部の言明の表の続きにする。"""
    files = sorted(folder.glob("statements_*.json"))
    tables = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in files}
    rowid_at = {name: table["columns"].index("rowid") for name, table in tables.items()}
    last_rowid = max(row[rowid_at[name]] for name, table in tables.items() for row in table["rows"])
    added = 0
    for fix in FIXES:
        table = tables[fix["table_file"]]
        columns = table["columns"]
        id_at = columns.index("id")
        if any(row[id_at] == fix["id"] for row in table["rows"]):
            continue
        original = next((row for row in table["rows"] if row[id_at] == fix["replaces_id"]), None)
        if original is None:
            continue
        last_rowid += 1
        row = dict(zip(columns, original))
        row.update(rowid=last_rowid, id=fix["id"], replaces_id=fix["replaces_id"], body=fix["body"])
        table["rows"].append([row[name] for name in columns])
        (folder / fix["table_file"]).write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
        added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="記録の文を、直した行として足す")
    sub = parser.add_subparsers(dest="command", required=True)
    target = sub.add_parser("db", help="データベースに足す")
    target.add_argument("path", type=Path)
    sub.add_parser("tables", help="data/select の表に足す")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "db":
        conn = db.connect(args.path)
        try:
            print(f"足した行：{apply_db(conn)}")
        finally:
            conn.close()
    else:
        print(f"足した行：{apply_tables()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
