"""本人の確かめの行を台本に足す。

役割：決まった言葉、田中さんの引用、ツインへの言葉から、本人が「そうです」と答える行を作り、月ごとにまとめて入れる。
4つの極：3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：6（「本人だけ」の言葉は確かめに出さない）、8（人物と記録はすべて架空）、9（本人の文は一字も変えない）、
13（ツインの色は本人が確かめた分だけのる）。

なぜこう作ったか：チャットには確かめの行がない。そのまま流すと本人の言葉はすべて未確認のままで、意思モデルの要素は
すべて AI の見立てになり、ツインの色も訂正の分にしかのらない。

足す決まり：
- 決まった言葉のうち、本人の言葉（印が「本」で始まる行）。2035-09 まで。「だけ」と「訂」の印の行は入れない。
- 田中さんの引用のうち、本人の言葉。2035-09 まで。
- 「ツインへ」の行。2034-09 までの各月で2行まで。
- match は本人の文を1文、一字も変えずに使う。文の途中で切らない。短い文は別の日の言葉に当たるので使わない。
- その月の最後の「閉じる」のあとに、月に1回まとめて置く。確かめの行は裏の処理を全部待つので、待つ回数を増やさないため。
- 訂正の行より前には置かない。ツインの花びらを、訂正の場面まで白いままにするため。それより前の言葉は、訂正のあとの最初のまとめで確かめる
  （2026-10 の言葉は 2026-11 の月末）。
- 2035-10 以降は足さない。本人が打てない時期のため。

記録は流したあとに AI が分けて作るので、合う記録がない行が出る。流すときは --keep-going を付け、飛ばした行の数を見る。

関連するテスト：tests/test_confirm_plan.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

SELF_NAME = "誠一さん"
SELF_RAW = "誠一"
TWIN_ROOM = "ツイン"
SAY_TO_TWIN = "ツインへ"
PRIVATE = "本人だけ"
CITING_PLACES = ("チーム", "ケア会議")
CITER = "田中"
LAST_MONTH = "2035-09"
LAST_TWIN_MONTH = "2034-09"
TWIN_PER_MONTH = 2
MIN_CHARS = 6
# 閉じる行に続けて置かれる行。確かめはこの後ろに置く
_FOLLOW_CLOSE = ("本人だけにする", "推測")
# 確かめに出さない場所。本人だけの言葉と、訂正の文
NOT_RECORDED = (PRIVATE, "訂正")
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]*")
_QUOTE = re.compile(r"「([^「」]+)」")
_FULL_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


@dataclass(frozen=True)
class Target:
    """確かめる1文。at は元の行の時刻で、この時刻より前には置かない。"""

    at: str
    match: str
    why: str


def _month(at: str) -> str:
    return at[:7]


def _marks(row: dict) -> dict:
    return row.get("marks") or {}


def longest_sentence(text: str, part: str | None = None) -> str | None:
    """text の文から1文を選ぶ。part があれば、part と重なる文だけから選ぶ。短い文しかなければ None。

    いちばん長い文を選ぶ。短い文ほど、別の日の同じ言葉に当たるため。
    """
    sentences = [item.strip() for item in _SENTENCE.findall(text)]
    if part is not None:
        sentences = [item for item in sentences if item in part or part in item or item.rstrip("。！？") in part]
    sentences = [item for item in sentences if len(item) >= MIN_CHARS]
    return max(sentences, key=len, default=None)


def _self_rows(rows: list[dict]) -> list[dict]:
    """確かめに出せる本人の行。「本人だけ」の行は出さない。人の部屋の行は言明にならないので、確かめる記録がない。"""
    return [row for row in rows if row["by"] == SELF_RAW and row["place"] not in NOT_RECORDED and "↔" not in row["place"]]


def fixed_targets(rows: list[dict], fixed: list[dict]) -> list[Target]:
    """決まった言葉のうち、本人の言葉を返す。行の全文ではなく、決まった言葉の文から選ぶ。"""
    found = []
    words = [item["text"] for item in fixed if item["by"] == SELF_NAME]
    for row in _self_rows(rows):
        if not str(_marks(row).get("決まった", "")).startswith("本") or _month(row["at"]) > LAST_MONTH:
            continue
        word = next((item for item in words if item in row["text"]), None)
        sentence = longest_sentence(word) if word else longest_sentence(row["text"])
        if sentence:
            found.append(Target(row["at"], sentence, "決まった言葉"))
    return found


def _quotes_of(rows: list[dict]) -> list[tuple[str | None, str]]:
    """田中さんの引用を、その前に書かれた日と一緒に返す。

    本人の記録を引くときは必ず日付を書いている。日付のない「よか」「入る」は引用ではないので、呼ぶ側で外す。
    """
    quotes = []
    for row in rows:
        if row["by"] != CITER or row["place"] not in CITING_PLACES:
            continue
        for found in _QUOTE.finditer(row["text"]):
            before = _FULL_DATE.findall(row["text"][: found.start()])
            day = None
            if before:
                year, month, date = before[-1]
                day = f"{year}-{int(month):02d}-{int(date):02d}"
            quotes.append((day, found.group(1)))
    return quotes


def cited_targets(rows: list[dict]) -> list[Target]:
    """田中さんの引用のうち、本人の言葉を返す。本人の行にない引用（正雄さんの言葉など）は入れない。"""
    mine = _self_rows(rows)
    found = []
    for day, quote in _quotes_of(rows):
        if day is None or len(quote) < MIN_CHARS:
            continue
        # 引用の中のかぎかっこは『』で書くので、元の行の「」にそろえて探す
        quote = quote.replace("『", "「").replace("』", "」")
        # 引用がそのまま1行にないときは、文ごとに探す。行をまたいで引いていることがあるため
        for part in [quote, *_SENTENCE.findall(quote)]:
            candidates = [row for row in mine if part in row["text"]]
            if candidates:
                break
        row = next((item for item in candidates if item["at"][:10] == day), candidates[0] if candidates else None)
        if row is None or _month(row["at"]) > LAST_MONTH:
            continue
        sentence = longest_sentence(row["text"], quote)
        if sentence:
            found.append(Target(row["at"], sentence, "田中さんの引用"))
    return found


def twin_targets(rows: list[dict], taken: set[str]) -> list[Target]:
    """「ツインへ」の行を、各月で2行まで返す。すでに選んだ文の行は数えず、次の行へ進む。"""
    counts: dict[str, int] = defaultdict(int)
    found = []
    for row in _self_rows(rows):
        month = _month(row["at"])
        if row["place"] != SAY_TO_TWIN or month > LAST_TWIN_MONTH or counts[month] >= TWIN_PER_MONTH:
            continue
        if any(item in row["text"] for item in taken):
            continue
        sentence = longest_sentence(row["text"])
        if sentence:
            counts[month] += 1
            found.append(Target(row["at"], sentence, "ツインへ"))
    return found


def choose_targets(rows: list[dict], fixed: list[dict]) -> list[Target]:
    """確かめる文を時刻の順に返す。同じ文は1回だけにする。"""
    chosen: dict[str, Target] = {}
    for target in (*fixed_targets(rows, fixed), *cited_targets(rows)):
        chosen.setdefault(target.match, target)
    for target in twin_targets(rows, set(chosen)):
        chosen.setdefault(target.match, target)
    return sorted(chosen.values(), key=lambda item: item.at)


def _spoken_texts(lines: list[dict]) -> list[str]:
    return [text for line in lines for text in (line.get("text"), line.get("body")) if text]


def _last_close_of_each_month(lines: list[dict]) -> dict[str, int]:
    """月ごとの、最後のツインとの部屋の閉じる行の位置を返す。"""
    last: dict[str, int] = {}
    for position, line in enumerate(lines):
        if line["kind"] == "閉じる" and line.get("room") == TWIN_ROOM and _month(line["at"]) <= LAST_MONTH:
            last[_month(line["at"])] = position
    return last


def add_confirmations(lines: list[dict], rows: list[dict], fixed: list[dict]) -> tuple[list[dict], list[Target]]:
    """行の並びに確かめの行を足した新しい並びと、足せなかった文を返す。

    流さない日の文は足さない（選ぶ版）。その月の最後の閉じるより後に話した文は、次の月にまとめる。記録がまだないため。
    訂正の行より前には置かない。色は本人が確かめた行でのるので、前に置くと、2026-11-02 の節目で花びらが白でなくなるため。
    関連するテスト：tests/test_confirm_plan.py の test_訂正より前には確かめを置かず_次のまとめで確かめる
    """
    spoken = _spoken_texts(lines)
    pending = [target for target in choose_targets(rows, fixed) if any(target.match in text for text in spoken)]
    closes = _last_close_of_each_month(lines)
    corrections = [line["at"] for line in lines if line["kind"] == "訂正"]
    after: dict[int, list[dict]] = defaultdict(list)
    for month in sorted(closes):
        position = closes[month]
        close_at = lines[position]["at"]
        if corrections and close_at < min(corrections):
            continue
        ready = [target for target in pending if target.at <= close_at]
        pending = [target for target in pending if target.at > close_at]
        while position + 1 < len(lines) and lines[position + 1]["kind"] in _FOLLOW_CLOSE and lines[position + 1]["at"] == close_at:
            position += 1
        after[position] = [{"at": close_at, "kind": "確かめ", "by": SELF_NAME, "match": target.match} for target in ready]
    result: list[dict] = []
    for position, line in enumerate(lines):
        result.append(line)
        result.extend(after.get(position, []))
    return result, pending
