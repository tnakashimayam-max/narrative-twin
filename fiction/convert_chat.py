"""チャットの全件を、fiction/run_script.py に流す script.jsonl へ変える。

役割：架空の10年分のチャット（1行1発言）を読み、今あるアプリの道で流せる行の並びに直す。
      乱数を使わず、同じ入力から同じ出力を作る。全件と、日を選んだ版（--select）を切り替える。
4つの極：1. 書く・話す、2. 分かち合う、4. 10年後に代弁する（架空の記録を作る道具）。
守る原則：3（出どころを分ける。代筆やケアマネジャーの文を、本人の行や便りのコメントに混ぜない）、
          8（人物と記録はすべて架空）、9（本文は一字も変えない）、10（「本人だけ」の行には守る行を必ず付ける）、
          15（人だけの部屋にツインを入れない）。

作りは2段階にしている。まず入力の各行の行き先（どの部屋の発言か、便りのコメントか）を決め、そのあとで
部屋を作る行と人を足す行を、実際に流れる最初の行の直前に差し込む。代筆の部屋には代筆の投稿だけでなく、
その投稿へのコメントと田中さんのコメントも入るので、部屋の最初の行と部屋の人は、行き先を決めたあとでないと分からないため。

サーバーも AI も使わない。ファイルを読んで書くだけなので、app.config 以外は読み込まない。

関連するテスト：tests/test_convert_chat.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from app import config

DEFAULT_CHAT = Path(r"C:\dev\nt-story\チャット_全件.jsonl")
DEFAULT_PHOTOS = config.DATA_DIR / "demo_photos"
DEFAULT_CORRECTION_MATCH = "庭"
DEFAULT_FIXED = DEFAULT_CHAT.with_name("決まった言葉.jsonl")

SELF_RAW = "誠一"
SELF_NAME = "誠一さん"
PROFESSIONAL_RAW = "田中"
TWIN_ROOM = "ツイン"
MEETING_ROOM = "ケア会議"
TEAM_ROOM = "チーム"
TEAM_MEMBERS = ("田中さん", "美穂さん", "井ノ口先生")
PAIR_MARK = "↔"

NAMES = {
    "誠一": "誠一さん",
    "美穂": "美穂さん",
    "正雄": "正雄さん",
    "節子": "節子さん",
    "健太": "健太くん",
    "井ノ口": "井ノ口先生",
    "田中": "田中さん",
}

# 投稿の場所と、便りの共有範囲
POST_PLACES = {"つぶやき": "縁のある人まで", "つぶやき家族": "家族"}
SAY_TO_TWIN_PLACE = "ツインへ"
PRIVATE_PLACE = "本人だけ"
TWIN_PLACES = {*POST_PLACES, SAY_TO_TWIN_PLACE, PRIVATE_PLACE}
COMMENT_PLACE = "コメント"
OBSERVATION_PLACE = "今の様子"
CORRECTION_PLACE = "訂正"

# 代筆の部屋を2つに分けるのは、部屋に人を足す道がないため。田中さんを招く前に作った部屋へ、あとから田中さんを入れられない。
PROXY_ROOM_BEFORE = "誠一さんのそば"
PROXY_ROOM_AFTER = "誠一さんのそば（田中さんと）"
PROXY_SPLIT_DAY = "2036-01-18"
PROXY_ROOMS = (PROXY_ROOM_BEFORE, PROXY_ROOM_AFTER)

HANDOVER_DAY = "2026-11-03"
HANDOVER_THEMES = ("家族", "仕事・役割", "住まい・土地", "好きなこと", "大切にしてきたこと", "人の手を借りること", "つらかったこと")

GUESS_DAY = "2026-10-25"
GUESS_TEXT = "庭のある暮らしそのものを大切にされているのかもしれません。"
GUESS_MATCH = "庭の世話があるけん"

# 物語の設定に合わせて、最初から入っている5人を直す。residence の null は「同居」を消すために書く（docs/script-format.md）
FIRST_AT = "2026-10-01T00:00"
FIRST_PEOPLE = (
    {"name": "誠一さん", "birth_year": 1952},
    {"name": "和子さん", "relation": "妻", "birth_year": 1954, "residence": None, "is_deceased": True},
    {"name": "美穂さん", "relation": "長女", "birth_year": 1985, "residence": "遠く"},
    {"name": "正雄さん", "relation": "友人", "birth_year": 1953, "residence": "近く"},
    {"name": "節子さん", "relation": "ご近所さん", "birth_year": 1949, "residence": "近く"},
)

# あとから物語に現れる人。入力の日付を書かず、最初に現れる行から決めるので、選ぶ版でも部屋の前に必ず入る
LATER_PEOPLE = {
    "井ノ口先生": {
        "kind": "人",
        "by": SELF_NAME,
        "name": "井ノ口先生",
        "role": "縁のある人",
        "relation": "かかりつけ医",
        "gender": "男性",
        "residence": "近く",
    },
    "健太くん": {
        "kind": "人",
        "by": SELF_NAME,
        "name": "健太くん",
        "role": "家族",
        "relation": "孫",
        "birth_year": 2024,
        "gender": "男性",
        "residence": "遠く",
    },
    "田中さん": {
        "kind": "招く",
        "by": "美穂さん",
        "name": "田中さん",
        "called_name": "田中さん",
        "relation": "ケアマネジャー",
    },
}

# 節目の行。チャットの行とは関係なく、決まった時刻に入れる
MILESTONES = (
    {"at": "2026-11-02T23:59", "kind": "整理", "by": SELF_NAME},
    {"at": "2032-10-01T00:00", "kind": "段階", "by": SELF_NAME, "stage": "要支援"},
    {"at": "2034-09-14T00:00", "kind": "段階", "by": SELF_NAME, "stage": "要介護"},
    {"at": "2036-05-05T23:59", "kind": "整理", "by": SELF_NAME},
    {"at": "2036-09-30T23:59", "kind": "整理", "by": SELF_NAME},
)

# 選ぶ版で、どの月にも残す日数。話の流れが途切れて見える月を作らないため
MONTH_MINIMUM_DAYS = 4
_WRITTEN_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 入力の行の行き先
_GO_POST = "投稿"
_GO_SAY = "発言"
_GO_COMMENT = "コメント"
_GO_OBSERVATION = "今の様子"
_GO_CORRECTION = "訂正"


@dataclass(frozen=True)
class _Plan:
    """入力の1行と、その行き先。"""

    row: dict
    go: str
    room: str | None = None
    letter: str | None = None
    # コメントの写真は部屋へ行き先が変わっても流さないので、行き先の部屋とは別に持つ
    flow_photos: bool = True


@dataclass
class Conversion:
    """変換の結果。lines が script.jsonl の中身になる。"""

    lines: list[dict]
    # 写真のファイルがなく、文だけで流した写真の番号
    missing_photos: list[str] = field(default_factory=list)
    # 行は流すが写真は流さないもの（番号, 場所）
    unflowed_photos: list[tuple[str, str]] = field(default_factory=list)
    selected_days: list[str] | None = None
    self_rows: int = 0
    # 親の投稿の日が選ばれたコメントの数。行き先を分ける前の数で、選び方の確かめに使う
    comments_on_selected: int | None = None
    notes: list[str] = field(default_factory=list)


def _day(at: str) -> str:
    return at[:10]


def _name(raw: str) -> str:
    if raw not in NAMES:
        raise ValueError(f"知らない名前です：{raw}")
    return NAMES[raw]


def _opens_twin_room(row: dict) -> bool:
    """ツインとの部屋に入る本人の行か。代筆のつぶやきは本人の言葉ではないので入れない。"""
    return row["place"] in TWIN_PLACES and row["by"] == SELF_RAW


def _proxy_room(at: str) -> str:
    return PROXY_ROOM_BEFORE if _day(at) < PROXY_SPLIT_DAY else PROXY_ROOM_AFTER


def letter_ref(at: str) -> str:
    return f"便り-{at}"


def read_chat(path: Path) -> list[dict]:
    """1行1発言のファイルを読む。"""
    rows: list[dict] = []
    with path.open(encoding="utf-8") as file:
        for raw in file:
            if raw.strip():
                rows.append(json.loads(raw))
    return rows


def find_parents(rows: list[dict]) -> dict[int, int]:
    """コメントの親（時刻で直前の投稿）の位置を返す。

    必ず選ぶ前の全件で求める。選んだあとの行で求めると、親が流れないコメントが、より前の投稿へ付け替わるため。
    関連するテスト：tests/test_convert_chat.py の test_選ぶ版で親が流れないコメントは流さず前の投稿にも付け替えない
    """
    parents: dict[int, int] = {}
    latest: int | None = None
    for index, row in enumerate(rows):
        if row["place"] in POST_PLACES:
            latest = index
        elif row["place"] == COMMENT_PLACE:
            if latest is None:
                raise ValueError(f"{row['at']} のコメントには親の投稿がありません")
            parents[index] = latest
    return parents


def select_days(rows: list[dict]) -> set[str]:
    """選ぶ版で流す日を決める。ツインの部屋が開く日だけを選ぶ。

    物語の芯になる日（決まった言葉、会議で触れる日、出来事、あとで思い出される日、ツインへの言葉）を先に取り、
    残りは月ごとに同じ間隔で足す。乱数を使わないので、同じ入力からは同じ日が選ばれる。
    """
    open_days = {_day(row["at"]) for row in rows if _opens_twin_room(row)}
    wanted: set[str] = set()
    for row in rows:
        marks = row["marks"]
        if "決まった" in marks or "出来事" in marks or row["place"] == SAY_TO_TWIN_PLACE:
            wanted.add(_day(row["at"]))
        if "思い出" in marks:
            wanted.update(_ISO_DATE.findall(str(marks["思い出"])))
        if row["by"] == PROFESSIONAL_RAW and row["place"] in (TEAM_ROOM, MEETING_ROOM):
            wanted.update(f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _WRITTEN_DATE.findall(row["text"]))
    chosen = wanted & open_days
    for month in sorted({day[:7] for day in open_days}):
        lacking = MONTH_MINIMUM_DAYS - sum(1 for day in chosen if day[:7] == month)
        if lacking <= 0:
            continue
        rest = sorted(day for day in open_days - chosen if day[:7] == month)
        if rest:
            step = max(len(rest) // lacking, 1)
            chosen.update(rest[::step][:lacking])
    return chosen


def _flows_when_selected(row: dict, parent: dict | None, chosen: set[str]) -> bool:
    """選ぶ版で流すかを決める。落とすのは本人の行と、親が落ちたコメントだけ。"""
    if _opens_twin_room(row):
        return _day(row["at"]) in chosen
    if parent is not None and _opens_twin_room(parent):
        return _day(parent["at"]) in chosen
    return True


def _destination(row: dict, parent: dict | None) -> _Plan:
    """入力の1行の行き先を決める。"""
    place, by = row["place"], row["by"]
    if place in POST_PLACES:
        if by == SELF_RAW:
            return _Plan(row, _GO_POST)
        return _Plan(row, _GO_SAY, room=_proxy_room(row["at"]))
    if place in (SAY_TO_TWIN_PLACE, PRIVATE_PLACE):
        if by != SELF_RAW:
            raise ValueError(f"{row['at']} の「{place}」の話し手が本人ではありません")
        return _Plan(row, _GO_SAY, room=TWIN_ROOM)
    if place == COMMENT_PLACE:
        # 専門職は便りを見られない。代筆の投稿は便りにならない。どちらも便りのコメントにはできないので部屋で話す
        if by == PROFESSIONAL_RAW or parent["by"] != SELF_RAW:
            return _Plan(row, _GO_SAY, room=_proxy_room(row["at"]), flow_photos=False)
        return _Plan(row, _GO_COMMENT, letter=letter_ref(parent["at"]), flow_photos=False)
    if place == OBSERVATION_PLACE:
        return _Plan(row, _GO_OBSERVATION, flow_photos=False)
    if place == CORRECTION_PLACE:
        return _Plan(row, _GO_CORRECTION, flow_photos=False)
    return _Plan(row, _GO_SAY, room=place)


def _plan_rows(rows: list[dict], parents: dict[int, int], chosen: set[str] | None) -> list[_Plan]:
    plans: list[_Plan] = []
    for index, row in enumerate(rows):
        parent = rows[parents[index]] if index in parents else None
        if chosen is None or _flows_when_selected(row, parent, chosen):
            plans.append(_destination(row, parent))
    return plans


def _proxy_members(plans: list[_Plan]) -> dict[str, list[str]]:
    """代筆の部屋の人を、話す順に返す。set の順番はプロセスごとに変わるので、同じ出力のために話す順で持つ。"""
    members: dict[str, list[str]] = {room: [] for room in PROXY_ROOMS}
    for plan in plans:
        if plan.room in members:
            name = _name(plan.row["by"])
            if name != SELF_NAME and name not in members[plan.room]:
                members[plan.room].append(name)
    return members


def _person_room(place: str, by: str) -> tuple[str, list[str]]:
    """人だけの部屋の種類と、作る人以外の人を返す。場所の名前から人が決まらないものは、推測せず止める。"""
    if place == TEAM_ROOM:
        return "グループ", [name for name in TEAM_MEMBERS if name != by]
    if PAIR_MARK in place:
        people = [_name(raw) for raw in place.split(PAIR_MARK)]
        if by not in people:
            raise ValueError(f"{by}は部屋「{place}」の人ではありません")
        return "1対1", [name for name in people if name != by]
    raise ValueError(f"部屋「{place}」の人が決まりません")


class _Builder:
    """行き先の決まった行を、順に script の行へ書く。人を足す行と節目の行は、ここでは入れない。"""

    def __init__(self, plans: list[_Plan], *, photo_dir: Path, correction_match: str, script_guess: bool) -> None:
        self.plans = plans
        self.photo_dir = photo_dir
        self.correction_match = correction_match
        self.script_guess = script_guess
        self.lines: list[dict] = []
        self.missing: list[str] = []
        self.unflowed: list[tuple[str, str]] = []
        self.rooms_made: set[str] = {TWIN_ROOM}
        self.refs: set[str] = set()
        self.members = _proxy_members(plans)
        self.handed_over = False
        self.guessed = False
        # その日のツインとの部屋の最後の行の位置。閉じる行はここに入れる。行が残らなかった日には閉じる行を出さない
        self.last_twin_row: dict[str, int] = {}
        self.private_texts: dict[str, list[str]] = {}
        for position, plan in enumerate(plans):
            if _opens_twin_room(plan.row):
                day = _day(plan.row["at"])
                self.last_twin_row[day] = position
                if plan.row["place"] == PRIVATE_PLACE:
                    self.private_texts.setdefault(day, []).append(plan.row["text"])

    def build(self) -> list[dict]:
        for position, plan in enumerate(self.plans):
            row = plan.row
            if not plan.flow_photos:
                self.unflowed += [(photo["id"], row["place"]) for photo in row["photos"]]
            if plan.go == _GO_POST:
                self._post(row)
            elif plan.go == _GO_SAY:
                self._enter_room(row["at"], plan.room, _name(row["by"]))
                self._say(row["at"], plan.room, _name(row["by"]), row["text"], row["photos"] if plan.flow_photos else [])
            elif plan.go == _GO_COMMENT:
                self.lines.append({"at": row["at"], "kind": "コメント", "by": _name(row["by"]), "letter": plan.letter, "text": row["text"]})
            elif plan.go == _GO_OBSERVATION:
                self.lines.append({"at": row["at"], "kind": "今の様子", "by": _name(row["by"]), "text": row["text"]})
            elif plan.go == _GO_CORRECTION:
                self._correct(row)
            # 便りは投稿の続きとして書くので、閉じる行は便りのあとになる。その日の便りを必ず同じ形（閉じる前）にそろえるため
            if self.last_twin_row.get(_day(row["at"])) == position:
                self._close_twin_room(row["at"])
        return self.lines

    def notes(self) -> list[str]:
        """頼まれたのに入れられなかった行を知らせる。"""
        notes: list[str] = []
        if not self.handed_over:
            notes.append(f"{HANDOVER_DAY} の訂正がないので、託す行を入れていません")
        if self.script_guess and not self.guessed:
            notes.append(f"{GUESS_DAY} にツインとの部屋の行がないので、推測の行を入れていません")
        return notes

    def _photo_names(self, photos: list[dict]) -> list[str | None]:
        """写真のファイル名を順に返す。ファイルがない写真は None にして、番号を覚えておく。"""
        names: list[str | None] = []
        for photo in photos:
            # 入力の path は Windows の形なので、どの環境でも同じ名前が取れるように Windows の形として読む
            name = PureWindowsPath(photo["path"]).name
            if (self.photo_dir / name).exists():
                names.append(name)
            else:
                self.missing.append(photo["id"])
                names.append(None)
        return names

    def _say(self, at: str, room: str, by: str, text: str, photos: list[dict]) -> str | None:
        """発言を書く。1枚目は文と同じ行に、2枚目以降は写真だけの行にする。1枚目のファイル名を返す。"""
        names = self._photo_names(photos)
        first = names[0] if names else None
        line = {"at": at, "kind": "発言", "room": room, "by": by, "text": text}
        if first:
            line["photo"] = first
        self.lines.append(line)
        for name in names[1:]:
            if name:
                self.lines.append({"at": at, "kind": "発言", "room": room, "by": by, "photo": name})
        return first

    def _post(self, row: dict) -> None:
        """本人の投稿。会話の発言と、同じ本文の便りの2行にする。"""
        at = row["at"]
        first = self._say(at, TWIN_ROOM, SELF_NAME, row["text"], row["photos"])
        ref = letter_ref(at)
        if ref in self.refs:
            raise ValueError(f"便りの ref が重なります：{ref}")
        self.refs.add(ref)
        line = {"at": at, "kind": "便り", "by": SELF_NAME, "ref": ref, "body": row["text"], "share_with": POST_PLACES[row["place"]]}
        if first:
            line["photo"] = first
        self.lines.append(line)

    def _correct(self, row: dict) -> None:
        # 相手は AI の推測だけから選ぶ。庭の話の推測がなければ流す道具が止まる（fiction/correction_scene.py）
        self.lines.append({"at": row["at"], "kind": "訂正", "by": SELF_NAME, "match": self.correction_match, "guess": True, "text": row["text"]})
        if _day(row["at"]) == HANDOVER_DAY and not self.handed_over:
            self.handed_over = True
            self.lines.append(
                {"at": row["at"], "kind": "託す", "by": SELF_NAME, "delegates": ["美穂さん"], "themes": list(HANDOVER_THEMES)}
            )

    def _close_twin_room(self, at: str) -> None:
        """閉じる行と、閉じたあとでないと指せない行を書く。記録は閉じたあとに作られるため。"""
        self.lines.append({"at": at, "kind": "閉じる", "room": TWIN_ROOM, "by": SELF_NAME})
        day = _day(at)
        for text in self.private_texts.get(day, []):
            self.lines.append({"at": at, "kind": "本人だけにする", "by": SELF_NAME, "text": text})
        if self.script_guess and day == GUESS_DAY:
            self.guessed = True
            self.lines.append({"at": at, "kind": "推測", "by": SELF_NAME, "text": GUESS_TEXT, "match": GUESS_MATCH})

    def _enter_room(self, at: str, room: str, by: str) -> None:
        """その部屋に最初に話す人が来たときに、部屋を作る行を書く。"""
        if room in self.rooms_made:
            return
        self.rooms_made.add(room)
        if room in PROXY_ROOMS:
            self._make_room(at, SELF_NAME, room, "グループ", self.members[room], twin_by=SELF_NAME)
        elif room == MEETING_ROOM:
            self._make_room(at, "田中さん", room, "グループ", ["誠一さん", "美穂さん"], twin_by="美穂さん")
        else:
            # 人だけの部屋にはツインを入れない。ツインは招かれた部屋だけを読む（原則15）
            kind, members = _person_room(room, by)
            self._make_room(at, by, room, kind, members)

    def _make_room(self, at: str, by: str, room: str, room_kind: str, members: list[str], twin_by: str | None = None) -> None:
        self.lines.append({"at": at, "kind": "部屋", "by": by, "room": room, "room_kind": room_kind, "members": members})
        if twin_by:
            self.lines.append({"at": at, "kind": "ツインを入れる", "by": twin_by, "room": room, "present": True})


def _with_later_people(lines: list[dict]) -> list[dict]:
    """あとから現れる人を、最初に現れる行の直前に足す。話す行だけでなく、部屋に入る行も現れる行に数える。"""
    pending = dict(LATER_PEOPLE)
    result: list[dict] = []
    for line in lines:
        for name in (line["by"], *line.get("members", [])):
            if name in pending:
                result.append({"at": line["at"], **pending.pop(name)})
        result.append(line)
    return result


def _with_milestones(lines: list[dict]) -> list[dict]:
    """節目の行を時刻の順の位置へ入れる。同じ時刻の行があれば、そのあとに入れる（その日の最後の行より前にしないため）。"""
    result = list(lines)
    for milestone in MILESTONES:
        result.insert(bisect_right([line["at"] for line in result], milestone["at"]), dict(milestone))
    return result


def _check_order(lines: list[dict]) -> None:
    """時刻が減っていないことを確かめる。流す道具は行の順に日付を動かすので、減ると記録の日付が狂うため。"""
    for previous, line in zip(lines, lines[1:]):
        if line["at"] < previous["at"]:
            raise ValueError(f"時刻が減っています：{previous['at']} のあとに {line['at']}")


def convert(
    rows: list[dict],
    *,
    photo_dir: Path,
    select: bool = False,
    correction_match: str = DEFAULT_CORRECTION_MATCH,
    script_guess: bool = False,
) -> Conversion:
    """入力の行を script の行へ変える。本文には手を加えない。"""
    # コメントの親は入力の並びから決めるので、並びが崩れていると別の投稿に付く。選ぶ版では崩れた行が流れず出力で気づけないこともある
    _check_order(rows)
    parents = find_parents(rows)
    chosen = select_days(rows) if select else None
    plans = _plan_rows(rows, parents, chosen)
    builder = _Builder(plans, photo_dir=photo_dir, correction_match=correction_match, script_guess=script_guess)
    first = [{"at": FIRST_AT, "kind": "人", "by": SELF_NAME, **person} for person in FIRST_PEOPLE]
    lines = _with_milestones(_with_later_people([*first, *builder.build()]))
    _check_order(lines)
    return Conversion(
        lines=lines,
        missing_photos=builder.missing,
        unflowed_photos=builder.unflowed,
        selected_days=sorted(chosen) if chosen is not None else None,
        self_rows=sum(1 for plan in plans if _opens_twin_room(plan.row)),
        comments_on_selected=(
            None if chosen is None else sum(1 for parent in parents.values() if _day(rows[parent]["at"]) in chosen)
        ),
        notes=builder.notes(),
    )


def write_script(lines: list[dict], path: Path) -> None:
    """script.jsonl を書く。環境によって改行が変わらないよう、\\n に固定する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for line in lines:
            file.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_missing(result: Conversion, path: Path) -> None:
    """足りない写真の番号を1行に1件書く。流さない写真は、作っても使われないので印を付けて分ける。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [*result.missing_photos, *(f"流さない写真：{number}（{place}）" for number, place in result.unflowed_photos)]
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.writelines(f"{item}\n" for item in body)


def summary(result: Conversion) -> list[str]:
    """標準出力に出す、行数と部屋の一覧。"""
    lines: list[str] = []
    if result.selected_days is None:
        lines.append("全件の版")
    else:
        lines.append(f"選ぶ版：{len(result.selected_days)}日")
        lines.append(f"親の日が選ばれたコメント：{result.comments_on_selected}件")
    lines.append(f"本人の行：{result.self_rows}行")
    lines.append(f"全部で {len(result.lines)}行")
    lines.append("行の種類：")
    for kind, count in Counter(line["kind"] for line in result.lines).most_common():
        lines.append(f"  {kind}：{count}")
    lines.append("部屋（発言の行数）：")
    for room, count in Counter(line["room"] for line in result.lines if line["kind"] == "発言").items():
        lines.append(f"  {room}：{count}")
    lines.append(f"足りない写真：{len(result.missing_photos)}枚")
    lines.append(f"流さない写真：{len(result.unflowed_photos)}枚")
    lines += [f"注意：{note}" for note in result.notes]
    return lines


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="チャットの全件を、fiction/run_script.py に流す script.jsonl へ変えます。")
    parser.add_argument("--chat", type=Path, default=DEFAULT_CHAT, help="入力のチャットのファイル")
    parser.add_argument("--out", type=Path, required=True, help="書き出す script.jsonl")
    parser.add_argument("--select", action="store_true", help="日を選んだ版を作ります")
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS, help="写真のフォルダ")
    parser.add_argument("--correction-match", default=DEFAULT_CORRECTION_MATCH, help="訂正の行で、直す記録を指す言葉")
    parser.add_argument("--script-guess", action="store_true", help="AI の推測の行を、ファイルに書いて入れます")
    parser.add_argument("--missing", type=Path, help="足りない写真の番号を書き出すファイル")
    parser.add_argument("--answers", action="store_true", help="ツインが問う日と、本人の短い受け答えの行を入れます（fiction/answer_plan.py）")
    parser.add_argument("--days", help="この日だけを流す形にします（読み比べの資料用。カンマで区切る）")
    parser.add_argument("--confirm", action="store_true", help="本人の確かめの行を入れます（fiction/confirm_plan.py）")
    parser.add_argument("--fixed", type=Path, default=DEFAULT_FIXED, help="--confirm で読む、決まった言葉のファイル")
    args = parser.parse_args(argv)

    if not args.chat.exists():
        print(f"入力のファイルがありません：{args.chat}")
        return 2
    rows = read_chat(args.chat)
    every_row = rows
    if args.days:
        wanted = set(args.days.split(","))
        rows = [row for row in rows if _day(row["at"]) in wanted]
    try:
        result = convert(
            rows,
            photo_dir=args.photos,
            select=args.select,
            correction_match=args.correction_match,
            script_guess=args.script_guess,
        )
    except ValueError as error:
        # 入力が決まりから外れているときは、途中までのファイルを残さずに止める
        print(f"止めました：{error}")
        return 2
    if args.days:
        result.lines = [line for line in result.lines if line["kind"] != "整理"]
    if args.answers:
        # 道具全体を読み込むので、案の形を作るときだけ読み込む
        from fiction import answer_plan
        from fiction.run_script import forbidden_in

        result.lines = answer_plan.apply_answers(
            result.lines, every_row, future_log=answer_plan.read_future_log(), forbidden=forbidden_in
        )
    if args.confirm:
        from fiction import confirm_plan

        fixed = [json.loads(raw) for raw in args.fixed.read_text(encoding="utf-8").splitlines() if raw.strip()]
        result.lines, left = confirm_plan.add_confirmations(result.lines, every_row, fixed)
        added = sum(1 for line in result.lines if line["kind"] == "確かめ")
        print(f"確かめの行：{added}行。置けなかった文：{len(left)}")
    write_script(result.lines, args.out)
    if args.missing:
        write_missing(result, args.missing)
    for line in summary(result):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
