"""実行結果の見本の道具のテスト。

役割：ログの文字を変えずに写すこと、条件に合う行だけを選ぶこと、自分用のログを読まないことを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のログだけを読む）。
"""

from __future__ import annotations

import json

import pytest

from app import db
from app.schemas import InferenceKind
from scripts import make_examples

ANSWER = {"direct_numbers": [1], "lead": "暮らしの記録です。"}


def _line(op="AI：ツインの答え", outcome="成功", at="2026-11-03T15:00:00+09:00", question="どうしても家で暮らせなくなったら？"):
    return {
        "at": at,
        "op": op,
        "outcome": outcome,
        "model": "claude-sonnet-5-5",
        "input_tokens": "29",
        "output_tokens": "97",
        "ms": "2948",
        "text": {"入力": question, "答え": json.dumps(ANSWER, ensure_ascii=False)},
    }


def test_入力と答えの文字を変えずに写す():
    text, _ = make_examples.render([_line()], "")

    assert "どうしても家で暮らせなくなったら？" in text
    assert '"lead": "暮らしの記録です。"' in text


def test_失敗の行と時刻より前の行は使わない():
    lines = [_line(outcome="失敗", question="失敗の問いのどうしても家で"), _line(at="2026-11-03T09:00:00+09:00", question="朝の問いのどうしても家で")]

    text, missing = make_examples.render(lines, "2026-11-03T10:00")

    assert "失敗の問い" not in text
    assert "朝の問い" not in text
    assert "田中さんへの回答（要素を選ぶ）" in missing


def test_終わりの時刻より後の行は使わない():
    lines = [_line(at="2026-11-03T15:30:00+09:00", question="あとの問いのどうしても家で")]

    text, missing = make_examples.render(lines, "", "2026-11-03T15:00")

    assert "あとの問い" not in text
    assert "田中さんへの回答（要素を選ぶ）" in missing


def test_語の条件に合う行を選ぶ():
    lines = [_line(question="どうしても家で暮らせなくなったら？"), _line(question="好きな色は？")]

    text, _ = make_examples.render(lines, "")

    assert "どうしても家で暮らせなくなったら？" in text
    assert "好きな色は？" not in text


def test_古い形の文字列のログも読める():
    line = _line()
    line["text"] = str(line["text"])

    assert make_examples.texts_of(line)["入力"] == "どうしても家で暮らせなくなったら？"


def test_長い入力は切ったことを書く():
    line = _line(question="どうしても家で" + "あ" * 7000)

    text, _ = make_examples.render([line], "")

    assert "全体は 7,007 字" in text


def test_届いた答えを架空用のデータベースから文字を変えずに写す(db_path, conn):
    with conn:
        db.insert_row(
            conn,
            "inference_logs",
            {
                "kind": InferenceKind.TWIN_ANSWER.value,
                "question": "どうしても家で暮らせなくなったら？",
                "result": json.dumps({"self_words": [{"body": "来年も畑をやりたい。"}]}, ensure_ascii=False),
                "element_ids": [],
                "statement_ids": [],
                "created_at": "2026-11-03T15:00:00+09:00",
            },
        )

    text, missing = make_examples.render_delivered(db_path)

    assert "来年も畑をやりたい。" in text
    assert "田中さんに届いた答え（本人の言葉がある問い）" not in missing
    assert "田中さんに届いた推定の枠" in missing


def test_自分用の記録の置き場のデータベースは読まない(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()

    with pytest.raises(SystemExit):
        make_examples.render_delivered(folder / "narrative_twin.db")


def test_自分用のログは読まない(tmp_path):
    path = tmp_path / "mine.jsonl"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        make_examples.read_lines(path)


def test_あとに渡したログの行を優先し_先のデータベースから答えを探す(db_path, conn, tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps(_line(question="どうしても家で 先のログ"), ensure_ascii=False), encoding="utf-8")
    second.write_text(json.dumps(_line(question="どうしても家で あとのログ"), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "examples.md"

    make_examples.main(["--log", str(first), str(second), "--db", str(tmp_path / "ない.db"), str(db_path), "--out", str(out)])

    written = out.read_text(encoding="utf-8")
    assert "あとのログ" in written
    assert "先のログ" not in written
