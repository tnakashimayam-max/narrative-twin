"""ツインの返事の処理の記録を読む道具。

役割：inference_logs の「ツインの読み」から、AI が書いた返事と、決まりに通した結果を人が読める形で出す。
4つの極：3. 読み解き、積み上げる（確かめの道具）。
守る原則：3（AI の読みと本人の言葉を分ける）、8（架空のデータだけを扱う。開発者の記録には触れない）。

何を書き、何が落ちたかが分かると、返事に違和感があったときに直す場所が分かる。
データベースは読むだけ。自分用の置き場は断る。

使い方：python -m fiction.twin_trace データベース [--last 5]
関連するテスト：tests/test_heart.py の test_返事の記録を読む道具は返事と決まりを出す
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app.schemas import InferenceKind

_PARTS = (("返事", "AI が書いた返事"), ("決まり", "決まりに通した結果"))


def _lines(part: dict) -> list[str]:
    lines = []
    for key, value in part.items():
        if value in (None, "", []):
            continue
        if isinstance(value, list) and value and isinstance(value[0], (str, dict)):
            lines.append(f"    {key}：")
            lines.extend(f"      - {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}" for item in value)
        else:
            lines.append(f"    {key}：{value}")
    return lines


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ツインの返事の処理の記録を読む。")
    parser.add_argument("database", type=Path)
    parser.add_argument("--last", type=int, default=5, help="新しいほうから何回分を出すか")
    args = parser.parse_args(argv)
    if "mine" in {part.lower() for part in args.database.resolve().parts}:
        print("自分用の記録は扱いません。", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT question, result, created_at FROM inference_logs WHERE kind = ? ORDER BY rowid DESC LIMIT ?",
            (InferenceKind.TWIN_READING.value, args.last),
        ).fetchall()
    finally:
        conn.close()
    for question, result, created_at in reversed(rows):
        print(f"\n■ {created_at[:19]}　{question}")
        trace = json.loads(result).get("trace") or {}
        if not trace:
            print("  処理の記録はありません（記録を始める前の返事です）")
        for key, label in _PARTS:
            if key in trace:
                print(f"  {label}")
                print("\n".join(_lines(trace[key])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
