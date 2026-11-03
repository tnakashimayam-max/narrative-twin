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


def _line(op="AI：ツインの答え", outcome="成功", at="2026-11-03T15:00:00+09:00", question="畑のことをどう考えていましたか？"):
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

    assert "畑のことをどう考えていましたか？" in text
    assert '"lead": "暮らしの記録です。"' in text


def test_失敗の行と時刻より前の行は使わない():
    lines = [_line(outcome="失敗", question="失敗の問いの畑のことを"), _line(at="2026-11-03T09:00:00+09:00", question="朝の問いの畑のことを")]

    text, missing = make_examples.render(lines, "2026-11-03T10:00")

    assert "失敗の問い" not in text
    assert "朝の問い" not in text
    assert "田中さんへの回答（要素を選ぶ）" in missing


def test_終わりの時刻より後の行は使わない():
    lines = [_line(at="2026-11-03T15:30:00+09:00", question="あとの問いの畑のことを")]

    text, missing = make_examples.render(lines, "", "2026-11-03T15:00")

    assert "あとの問い" not in text
    assert "田中さんへの回答（要素を選ぶ）" in missing


def test_語の条件に合う行を選ぶ():
    lines = [_line(question="畑のことをどう考えていましたか？"), _line(question="好きな色は？")]

    text, _ = make_examples.render(lines, "")

    assert "畑のことをどう考えていましたか？" in text
    assert "好きな色は？" not in text


def test_古い形の文字列のログも読める():
    line = _line()
    line["text"] = str(line["text"])

    assert make_examples.texts_of(line)["入力"] == "畑のことをどう考えていましたか？"


def test_長い入力は切ったことを書く():
    line = _line(question="畑のことを" + "あ" * 7000)

    text, _ = make_examples.render([line], "")

    assert "全体は 7,005 字" in text


def test_届いた答えを架空用のデータベースから文字を変えずに写す(db_path, conn):
    with conn:
        db.insert_row(
            conn,
            "inference_logs",
            {
                "kind": InferenceKind.TWIN_ANSWER.value,
                "question": "畑のことをどう考えていましたか？",
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
