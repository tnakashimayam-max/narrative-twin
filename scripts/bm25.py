"""BM25 で文を探す部品。今の方式と比べるための、簡単な RAG の探す部分。

役割：問いに語が似ている順に、言明の本文を並べる。AI は使わない。
4つの極：3. 読み解き、積み上げる（比べるためだけの道具）。
守る原則：なし。探す先を同意で絞るのは呼ぶ側（eval/previous_story/compare_rag.py）。

アプリからは呼ばない。アプリは「質問のたびに似た文を探す RAG は使わない」方式なので、比べる道具だけを scripts/ に置く。
日本語は単語に分ける道具を足さず、文字の2文字組で数える。語の切れ目を決めなくても、語の一致を取りこぼしにくいため。

関連するテスト：tests/test_bm25.py
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

# Okapi BM25 でよく使われる値。k1 は同じ語が何度も出たときの伸びの抑え、b は文の長さの補正
K1 = 1.2
B = 0.75

_NOT_WORD = re.compile(r"[^\w]", re.UNICODE)


def tokens(text: str) -> list[str]:
    """文を2文字組に分ける。全角と半角をそろえ、記号と空白は切れ目にする。1文字だけの塊はそのまま1語にする。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    result: list[str] = []
    for chunk in _NOT_WORD.split(normalized):
        if len(chunk) == 1:
            result.append(chunk)
        result.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return result


class BM25:
    """文の一覧を受け取り、問いへの点数を返す。"""

    def __init__(self, documents: list[str]) -> None:
        self._counts = [Counter(tokens(document)) for document in documents]
        self._lengths = [sum(counts.values()) for counts in self._counts]
        self._average = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        self._frequency: Counter[str] = Counter()
        for counts in self._counts:
            self._frequency.update(counts.keys())

    def _idf(self, token: str) -> float:
        total = len(self._counts)
        found = self._frequency[token]
        return math.log((total - found + 0.5) / (found + 0.5) + 1)

    def score(self, query: str, index: int) -> float:
        """1つの文の点数。問いの同じ語は1度だけ数える。"""
        counts = self._counts[index]
        length = self._lengths[index]
        value = 0.0
        for token in set(tokens(query)):
            count = counts[token]
            if count:
                adjust = 1 - B + B * (length / self._average)
                value += self._idf(token) * count * (K1 + 1) / (count + K1 * adjust)
        return value

    def rank(self, query: str) -> list[tuple[int, float]]:
        """点数の高い順に（文の番号、点数）を返す。語が1つも重ならない文は返さない。同点は前の文を先にする。"""
        scored = [(index, self.score(query, index)) for index in range(len(self._counts))]
        return sorted((pair for pair in scored if pair[1] > 0), key=lambda pair: (-pair[1], pair[0]))
