"""Claude の使った量を、役割とモデルごとに集計する。

役割：架空用のデータベースの ai_calls と、架空ログを作ったときの記録を読み、回数、トークン、費用を表にする。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のデータだけを読む。自分用の記録は読まない）。

読むのは data/narrative_twin.db と data/trial/ の中のファイルだけ。別の場所を指定されても、
自分用の記録の置き場は読まない。結果は docs/tools.md に写す。

使い方：
  python -m scripts.usage_report
  python -m scripts.usage_report --db data/trial/story_build.db

関連するテスト：tests/test_usage_report.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app import config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = config.DATA_DIR / "narrative_twin.db"
STORY_STATE = config.DATA_DIR / "trial" / "story_state.json"


def refuse_own_records(path: Path) -> None:
    """自分用の記録の置き場を読まない。"""
    if "mine" in {part.lower() for part in path.resolve().parts}:
        raise SystemExit("自分用の記録は読みません。")


def summarize(path: Path) -> list[dict]:
    """役割とモデルと、バッチかどうかごとに集計する。"""
    refuse_own_records(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # キャッシュの量は別の表にある。ai_calls の表を変えないため。古いデータベースには表がない
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        cache_join = "LEFT JOIN ai_call_cache c ON c.ai_call_id = a.id" if "ai_call_cache" in tables else ""
        cache_read = "SUM(c.cache_read_tokens)" if cache_join else "0"
        cache_write = "SUM(c.cache_write_tokens)" if cache_join else "0"
        rows = connection.execute(
            f"""
            SELECT a.role AS role, a.model AS model, a.is_batch AS is_batch, COUNT(*) AS calls,
                   SUM(a.input_tokens) AS input_tokens, SUM(a.output_tokens) AS output_tokens,
                   {cache_read} AS cache_read_tokens, {cache_write} AS cache_write_tokens,
                   AVG(a.seconds) AS average_seconds, MAX(a.seconds) AS longest_seconds
            FROM ai_calls a {cache_join}
            GROUP BY a.role, a.model, a.is_batch ORDER BY a.role, a.model, a.is_batch
            """
        ).fetchall()
    finally:
        connection.close()
    summary = []
    for row in rows:
        item = dict(row)
        item["cache_read_tokens"] = item["cache_read_tokens"] or 0
        item["cache_write_tokens"] = item["cache_write_tokens"] or 0
        item["cost_usd"] = round(
            config.cost_usd(item["model"], item["input_tokens"], item["output_tokens"], batch=bool(item["is_batch"]))
            + config.cache_cost_usd(item["model"], item["cache_read_tokens"], item["cache_write_tokens"]),
            4,
        )
        summary.append(item)
    return summary


def as_table(summary: list[dict]) -> str:
    lines = [
        "| 役割 | モデル | バッチ | 回数 | 入力 | 出力 | キャッシュ読み | キャッシュ書き | 平均秒 | 最長秒 | 費用（ドル） |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in summary:
        average = f"{item['average_seconds']:.2f}" if item["average_seconds"] is not None else "—"
        longest = f"{item['longest_seconds']:.2f}" if item["longest_seconds"] is not None else "—"
        lines.append(
            f"| {item['role']} | {item['model']} | {'はい' if item['is_batch'] else 'いいえ'} | {item['calls']} | "
            f"{item['input_tokens']:,} | {item['output_tokens']:,} | {item['cache_read_tokens']:,} | "
            f"{item['cache_write_tokens']:,} | {average} | {longest} | {item['cost_usd']:.4f} |"
        )
    total = sum(item["cost_usd"] for item in summary)
    lines.append(f"\n合計 {total:.4f} ドル")
    return "\n".join(lines)


def story_usage() -> list[dict]:
    """架空ログを作ったときの費用を読む。作っていなければ空にする。"""
    if not STORY_STATE.exists():
        return []
    return json.loads(STORY_STATE.read_text(encoding="utf-8")).get("usage", [])


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Claude の使った量を集計します。")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    if args.db.exists():
        print(as_table(summarize(args.db)))
    else:
        print(f"{args.db} はありません。")
    usage = story_usage()
    if usage:
        print("\n架空ログを作った分：")
        for row in usage:
            print(f"- {row['stage']}：{row['count']}件、{row['cost_usd']:.4f} ドル")
        print(f"合計 {sum(row['cost_usd'] for row in usage):.4f} ドル")
    return 0


if __name__ == "__main__":
    sys.exit(main())
