"""比べるための BM25 のテスト。

役割：語の一致で順に並ぶこと、語が重ならない文を返さないことを確かめる。
4つの極：3. 読み解き、積み上げる（比べるためだけの道具）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.bm25 import BM25, tokens

APP = Path(__file__).resolve().parent.parent / "app"


def test_文字の2文字組に分ける():
    assert tokens("庭の松") == ["庭の", "の松"]


def test_全角と半角をそろえ_記号で切る():
    assert tokens("ＡＩ、庭") == ["ai", "庭"]


def test_問いの語を含む文が先に並ぶ():
    index = BM25(["毎朝庭を見ます。", "将棋の会に行きます。", "味噌汁が好きです。"])

    assert index.rank("将棋のことを教えてください")[0][0] == 1


def test_語が1つも重ならない文は返さない():
    index = BM25(["毎朝庭を見ます。", "将棋の会に行きます。"])

    assert index.rank("味噌汁") == []


def test_多くの文に出る語より_少ない文にだけ出る語を重く見る():
    index = BM25(["庭の話です。", "庭の話をします。", "庭の話でした。", "床板の話です。"])

    assert index.rank("庭と床板")[0][0] == 3


def test_同じ点数なら前の文を先にする():
    index = BM25(["庭を見ます。", "庭を見ます。"])

    assert [number for number, _ in index.rank("庭を見")] == [0, 1]


def test_文がなくても失敗しない():
    assert BM25([]).rank("庭") == []


def test_アプリは_BM25を読み込まない():
    """アプリは質問のたびに似た文を探さない。この部品は比べる道具からだけ使う（CLAUDE.md の「方式」）。"""
    for path in APP.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("scripts"), path.name
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith("scripts") for alias in node.names), path.name
