"""使った量の集計の道具のテスト。

役割：役割とモデルごとに集計できること、自分用の記録の置き場を読まないことを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のデータだけを読む）。
"""

from __future__ import annotations

import pytest

from app import db
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


def test_自分用の記録の置き場は読まない(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()

    with pytest.raises(SystemExit):
        usage_report.summarize(folder / "narrative_twin.db")
