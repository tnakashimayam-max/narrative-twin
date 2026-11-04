"""チャットの行の並びに、ツインが問う日と、本人の短い受け答えの行を入れる。

役割：fiction/convert_chat.py が作った行の並びを受け取り、次の決まりで `ask` と `受け答え` の行を足す。
4つの極：1. 書く・話す、2. 分かち合う（架空の記録を作る道具）。
守る原則：7（答えのない問いを重ねない）、8（すべて架空）、9（チャットの行は一字も変えない）。

なぜこう作ったか：チャットは本物のツインの返事を見ずに書いた。そのまま流すと、ツインの問いに誰も答えないまま
閉じる日が大量に並び、次の行も問いへの答えにならない。そこで次のようにする。

- **対象の日**（動画の期間、決まった言葉の日、田中さんの引用の日、ツインへの日）：ツインが問うたら、
  本人の短い受け答えを足す。足すのは問いが出たときだけで、チャットの行はそのまま別の吹き出しで流す。
  本人の行が1つの日は、受け答えのあとにツインが受け止めて終わる。2行以上の日は、最後の行では問わない。
- **それ以外の日**：ツインは受け止めるだけで問わない。本人が話を続けなかった日として扱う。
- **本人の時期**：2032-10 から短文、2034-10 から途切れた短い言葉、2035-10 からは足さず、ツインも問わない
  （fiction/added_words.py の limit_for）。
- **コメントのあとの問い**：同じ決まりにする。回数を絞るため、動画の期間、決まった言葉の日、引用の日だけで、
  最初に人がコメントしたときに問い、受け答えを足す。それ以外のコメントでは問わない。
- **グループ**：ツインは問わない。答える人がいないため。

事実は未来ログと、その日のその時刻までのチャットから渡す。後の行を渡すと、チャットの次の行を写してしまい、
同じ文が2回並ぶため（実際に3件起きた）。本人のいない部屋の話は、本人が知らないので渡さない。
病名や泊まりの語がある文は渡さない。足す文に入らないようにするため。

関連するテスト：tests/test_added_words.py
決まりの説明：docs/script-format.md、data/README.md
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from fiction import added_words

DEFAULT_FUTURE_LOG = Path(r"C:\dev\nt-story\未来ログ_全日.csv")
TWIN_ROOM = "ツイン"
SELF_NAME = "誠一さん"
SELF_RAW = "誠一"
VIDEO_PERIODS = (("2026-10-01", "2026-11-03"), ("2036-01-18", "2036-05-06"))
_FACT_COLUMNS = ("天気", "最高気温", "暦と町", "庭の花", "野球", "誠一の1日", "美穂", "正雄", "節子", "井ノ口内科", "ほか")
_HINT_CHARS = 1500
_STYLE_LINES = 6
_STYLE_DAYS = 120
_STYLE_ROOMS = ("誠一↔美穂", "誠一↔正雄", "誠一↔節子", "誠一↔健太", "誠一↔井ノ口", "コメント")
_WRITTEN_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")


def read_future_log(path: Path = DEFAULT_FUTURE_LOG) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["日付"]: row for row in csv.DictReader(handle)}


def _day(at: str) -> str:
    return at[:10]


def _marks(row: dict) -> dict:
    return row.get("marks") or {}


def key_days(rows: list[dict]) -> set[str]:
    """案の対象の日を返す。ツインとの部屋が開かない日も含むが、問いの判断には開く日だけが使われる。"""
    days = _fixed_and_cited(rows) | {_day(row["at"]) for row in rows if row["place"] == "ツインへ"}
    return days | {_day(row["at"]) for row in rows if _in_video(_day(row["at"]))}


def thread_days(rows: list[dict]) -> set[str]:
    """コメントのあとに問う日を返す。問いが増えすぎないよう、動画の期間、決まった言葉の日、引用の日だけにする。"""
    return _fixed_and_cited(rows) | {_day(row["at"]) for row in rows if _in_video(_day(row["at"]))}


def _in_video(day: str) -> bool:
    return any(first <= day <= last for first, last in VIDEO_PERIODS)


def _fixed_and_cited(rows: list[dict]) -> set[str]:
    days = {_day(row["at"]) for row in rows if "決まった" in _marks(row)}
    for row in rows:
        if row["by"] == "田中" and row["place"] in ("チーム", "ケア会議"):
            for year, month, day in _WRITTEN_DATE.findall(row["text"]):
                days.add(f"{year}-{int(month):02d}-{int(day):02d}")
    return days


class _Hints:
    """受け答えに渡す事実と話し方の例を作る。"""

    def __init__(self, rows: list[dict], future_log: dict[str, dict], forbidden) -> None:
        self.future_log = future_log
        self.forbidden = forbidden
        self.by_day: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            self.by_day[_day(row["at"])].append(row)
        self.replies = [
            row for row in rows if row["by"] == SELF_RAW and row["place"] in _STYLE_ROOMS and len(row["text"]) <= 25 and not forbidden(row["text"])
        ]

    def _safe(self, text: str) -> str:
        """病名や泊まりの語がある文を落とす。足す文に入らないようにするため。"""
        return "".join(sentence for sentence in _SENTENCE.findall(text) if not self.forbidden(sentence))

    def _facts(self, day: str, until: str) -> list[str]:
        row = self.future_log.get(day, {})
        facts = [f"{name}：{self._safe(row[name])}" for name in _FACT_COLUMNS if row.get(name) and self._safe(row[name])]
        for item in self.by_day.get(day, []):
            if item["at"] > until:
                break
            # 本人のいない部屋の話は本人が知らない
            if SELF_RAW not in item["place"] and item["place"] not in ("つぶやき", "つぶやき家族", "ツインへ", "コメント", "今の様子"):
                continue
            if item["place"] == "本人だけ":
                continue
            text = self._safe(item["text"])
            if text:
                facts.append(f"{item['at'][11:16]} {item['place']} {item['by']}：{text}")
        return facts

    def _style(self, until: str) -> list[str]:
        """その時刻までの、その時期の本人の短い言葉を返す。後の行は入れない。写してしまうため。"""
        year, month = int(until[:4]), int(until[5:7])
        earliest = f"{year if month > _STYLE_DAYS // 30 else year - 1}-{(month - _STYLE_DAYS // 30 - 1) % 12 + 1:02d}"
        near = [row["text"] for row in self.replies if earliest <= row["at"][:7] and row["at"] <= until]
        return near[-_STYLE_LINES:]

    def hint(self, at: str) -> str:
        day = _day(at)
        facts = "\n".join(self._facts(day, at))[:_HINT_CHARS]
        style = "\n".join(self._style(at))
        return f"### この日の事実\n{facts}\n### この時期の本人の話し方の例\n{style}"


def apply_answers(lines: list[dict], rows: list[dict], *, future_log: dict[str, dict], forbidden) -> list[dict]:
    """行の並びに `ask` と `受け答え` の行を足した新しい並びを返す。チャットの本文には手を加えない。"""
    key = key_days(rows)
    in_thread = thread_days(rows)
    hints = _Hints(rows, future_log, forbidden)
    turns = _twin_turns(lines)
    asking = _asking_comments(lines, in_thread)
    result: list[dict] = []
    for position, line in enumerate(lines):
        kind = line["kind"]
        day = _day(line["at"])
        limit = added_words.limit_for(day)
        if kind == "発言" and line.get("room") == TWIN_ROOM:
            turn = turns[position]
            asks = day in key and limit is not None and turn.may_ask
            result.append({**line, "ask": asks} if not asks else dict(line))
            if asks:
                result.append(
                    {
                        "at": line["at"],
                        "kind": "受け答え",
                        "by": SELF_NAME,
                        "room": TWIN_ROOM,
                        "hint": hints.hint(line["at"]),
                        "limit": limit,
                        "twin_replies": turn.only_turn,
                    }
                )
        elif kind == "発言" and line.get("room"):
            # 人だけの部屋では、もともとツインは動かない。ツインのいるグループでは問わない
            result.append({**line, "ask": False})
        elif kind == "コメント":
            asks = position in asking and limit is not None
            result.append(dict(line) if asks else {**line, "ask": False})
            if asks:
                result.append(
                    {
                        "at": line["at"],
                        "kind": "受け答え",
                        "by": SELF_NAME,
                        "letter": line["letter"],
                        "hint": hints.hint(line["at"]),
                        "limit": limit,
                    }
                )
        else:
            result.append(line)
    return result


class _Turn:
    def __init__(self, may_ask: bool, only_turn: bool) -> None:
        self.may_ask = may_ask
        self.only_turn = only_turn


def _twin_turns(lines: list[dict]) -> dict[int, _Turn]:
    """ツインとの部屋の発言ごとに、問うてよいかを決める。

    同じ時刻に続く行（2枚目の写真）は1回の話なので、最後の行でだけ問う。本人の行が1つの日は問うてよい。
    2行以上の日は最後の行で問わない。答えのない問いを残さず、足す文を増やさないため。
    """
    by_day: dict[str, list[int]] = defaultdict(list)
    for position, line in enumerate(lines):
        if line["kind"] == "発言" and line.get("room") == TWIN_ROOM:
            by_day[_day(line["at"])].append(position)
    turns: dict[int, _Turn] = {}
    for positions in by_day.values():
        groups: list[list[int]] = []
        for position in positions:
            if groups and lines[groups[-1][-1]]["at"] == lines[position]["at"]:
                groups[-1].append(position)
            else:
                groups.append([position])
        for index, group in enumerate(groups):
            is_last = index == len(groups) - 1
            only = len(groups) == 1
            for position in group:
                turns[position] = _Turn(may_ask=position == group[-1] and (only or not is_last), only_turn=only)
    return turns


def _asking_comments(lines: list[dict], days: set[str]) -> set[int]:
    """問いを出すコメントの位置を返す。対象の日の便りで、最初に本人以外がコメントした行だけ。"""
    seen: set[str] = set()
    asking: set[int] = set()
    for position, line in enumerate(lines):
        if line["kind"] != "コメント" or line["letter"] in seen or line["by"] == SELF_NAME:
            continue
        seen.add(line["letter"])
        if _day(line["at"]) in days:
            asking.add(position)
    return asking
