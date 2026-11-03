"""使った量の集計の道具のテスト。

役割：役割とモデルごとに集計できること、自分用の記録の置き場を読まないことを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のデータだけを読む）。
"""

from __future__ import annotations

import json

import pytest

from app import config, db
from scripts import usage_report


def test_役割とモデルとバッチごとに集計して費用を出す(db_path, conn):
    db.log_ai_call(conn, role="ツインの会話", model="claude-haiku-5-5", input_tokens=100, output_tokens=50, seconds=1.0)
    db.log_ai_call(conn, role="ツインの会話", model="claude-haiku-5-5", input_tokens=300, output_tokens=50, seconds=3.0)
    db.log_ai_call(conn, role="言明への分割", model="claude-haiku-5-5", input_tokens=10, output_tokens=5, is_batch=True)

    summary = usage_report.summarize(db_path)

    talk = next(item for item in summary if item["role"] == "ツインの会話")
    assert (talk["calls"], talk["input_tokens"], talk["output_tokens"]) == (2, 400, 100)
    assert talk["average_seconds"] == 2.0
    assert talk["cost_usd"] >= 0
    split = next(item for item in summary if item["role"] == "言明への分割")
    assert split["is_batch"] == 1


def test_表に合計を出す(db_path, conn):
    db.log_ai_call(conn, role="ツインの会話", model="claude-haiku-5-5", input_tokens=100, output_tokens=50)

    table = usage_report.as_table(usage_report.summarize(db_path))

    assert "ツインの会話" in table
    assert "合計" in table


def test_キャッシュの読み書きを別の表から足して費用に入れる(db_path, conn):
    db.log_ai_call(
        conn,
        role="ツインの答え",
        model="claude-sonnet-5-5",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=1000,
        cache_write_tokens=2000,
    )
    db.log_ai_call(conn, role="ツインの答え", model="claude-sonnet-5-5", input_tokens=100, output_tokens=50)

    item = usage_report.summarize(db_path)[0]

    assert (item["calls"], item["cache_read_tokens"], item["cache_write_tokens"]) == (2, 1000, 2000)
    expected = config.cost_usd("claude-sonnet-5-5", 200, 100) + config.cache_cost_usd("claude-sonnet-5-5", 1000, 2000)
    assert item["cost_usd"] == round(expected, 4)


def test_開発のログから役割とモデルごとに数え_AIでない行と時刻より前の行は数えない(tmp_path):
    log = tmp_path / "fictional.jsonl"
    lines = [
        {"at": "2026-11-03T15:00", "op": "AI：言葉を選ぶ", "model": "claude-sonnet-5-5", "input_tokens": 100, "output_tokens": 10, "ms": 2000},
        {"at": "2026-11-03T15:01", "op": "AI：言葉を選ぶ", "model": "claude-sonnet-5-5", "input_tokens": 100, "output_tokens": 10, "ms": 4000},
        {"at": "2026-11-03T14:00", "op": "AI：言葉を選ぶ", "model": "claude-sonnet-5-5", "input_tokens": 999, "output_tokens": 99, "ms": 1},
        {"at": "2026-11-03T15:02", "op": "POST /api/advocacy/ask", "status": 200},
    ]
    log.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")

    summary = usage_report.summarize_log(log, since="2026-11-03T14:30")

    assert [(item["role"], item["calls"], item["input_tokens"]) for item in summary] == [("言葉を選ぶ", 2, 200)]
    assert summary[0]["average_seconds"] == 3.0


def test_自分用のログは読まない(tmp_path):
    with pytest.raises(SystemExit):
        usage_report.summarize_log(tmp_path / "mine.jsonl")


def test_自分用の記録の置き場は読まない(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()

    with pytest.raises(SystemExit):
        usage_report.summarize(folder / "narrative_twin.db")
