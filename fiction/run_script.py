"""script.jsonl を、今ある API へ順に流す。

役割：決まり（docs/script-format.md）に沿ったファイルを、その人としてログインして時刻の順に流し、
      別のデータベースに10年分の記録を作る。--check では API も AI も呼ばずにファイルだけを点検する。
4つの極：1. 書く・話す、2. 分かち合う、3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、8（人物と記録はすべて架空）、9（must の言葉は一字も変えない）。

サーバーに専用の受け口を作らない。画面が使うのと同じ道だけを、同じプロセスの中のアプリへ送る
（fastapi.testclient）。ネットワークを使わないので、8000番にも8001番にも接続できない作りになる。
FastAPI の裏の処理（言明への分割など）は、応答が返る前に終わる。そのため、応答を待つだけで次の行へ進める。

保存先は --folder の下だけで、data/narrative_twin.db と data/story/ には触れない。
日付は動かせるが時刻は動かせない（POST /api/dev/date）。発言の時刻は流した時刻になる。

関連するテスト：tests/test_run_script.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal, get_args

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app import ai, config, demo, jobs
from app.main import create_app
from app.twin import _BODY_WORDS, _DISTRESS_WORDS, _LIFE_WORDS
from fiction.load import LogComment, LogConversation
from scripts.usage_report import summarize

DEFAULT_FOLDER = config.DATA_DIR / "trial" / "script_run"
PHOTO_DIR = config.DATA_DIR / "demo_photos"
SELF_NAME = "誠一さん"
TWIN_ROOM = "ツイン"
# 物語の中でログインしない人。話し手にできない
NEVER_SPEAK = {"健太くん"}
SEEDED_ROLES = {"誠一さん": "本人", "和子さん": "家族", "美穂さん": "家族", "正雄さん": "縁のある人", "節子さん": "縁のある人"}
SEEDED_DELEGATES = {"美穂さん"}
DEFAULT_BUDGET_USD = 5.0
# AI の一時の失敗を待って送り直す秒数。同じ client_key で送るので発言は増えない
RETRY_SECONDS = (2, 5, 15)
_END_OF_SENTENCE = ("。", "！", "？")

# 病名と診断。発言と記録に入れない（docs/script-format.md）
_MEDICAL_WORDS = (
    "脳出血",
    "脳梗塞",
    "脳卒中",
    "麻痺",
    "高次脳機能障害",
    "誤嚥",
    "肺炎",
    "認知症",
    "アルツハイマー",
    "パーキンソン",
    "糖尿病",
    "高血圧",
    "癌",
    "腫瘍",
    "骨折",
    "診断",
    "病名",
)
# 漢字の語が症、炎、病で終わるものも病名として見る。看病はふだんの言葉なので除く
_MEDICAL_ENDING = re.compile(r"[一-鿿]+(?:症|炎|病)(?![一-鿿])")
_NOT_MEDICAL = {"看病"}
# 泊まりの話は入れない。ケアマネジャーが聞いたときに「まだ聞けていません」と答える場面に使うため
_STAY_WORDS = ("泊まり", "泊まる", "泊まっ", "ショートステイ", "ショート", "短期入所", "宿泊")
_CARE_WORDS = (*_LIFE_WORDS, *_BODY_WORDS, *_DISTRESS_WORDS)
# 今のログの形に書けるコメントの話し手。それ以外の人のコメントは別のファイルに書く
_LOG_COMMENTERS = get_args(LogComment.model_fields["speaker"].annotation)

Kind = Literal["発言", "閉じる", "便り", "コメント", "今の様子", "確かめ", "訂正", "人", "託す", "招く", "部屋", "ツインを入れる", "整理"]

_REQUIRED: dict[str, tuple[str, ...]] = {
    "発言": ("room",),
    "閉じる": ("room",),
    "便り": ("ref", "body", "share_with"),
    "コメント": ("letter", "text"),
    "今の様子": ("text",),
    "確かめ": ("match",),
    "訂正": ("match", "text"),
    "人": ("name",),
    "託す": ("delegates", "themes"),
    "招く": ("name",),
    "部屋": ("room", "room_kind", "members"),
    "ツインを入れる": ("room", "present"),
    "整理": (),
}
_PERSON_FIELDS = ("called_name", "role", "relation", "residence", "birth_year", "gender", "is_deceased", "memo")


class Answer(BaseModel):
    """本人がツインに答える番。ツインの文はファイルに書かないので、筋と必ず言う言葉だけを書く。"""

    model_config = ConfigDict(extra="forbid")

    hint: str | None = None
    must: str | list[str] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> Answer:
        if not self.hint and not self.musts:
            raise ValueError("answer には hint か must を書いてください")
        return self

    @property
    def musts(self) -> list[str]:
        if self.must is None:
            return []
        return [self.must] if isinstance(self.must, str) else self.must


class Line(BaseModel):
    """1行分。項目の意味は docs/script-format.md にある。"""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    kind: Kind
    by: str
    room: str | None = None
    text: str | None = None
    answer: Answer | None = None
    photo: str | None = None
    topic: str | None = None
    ref: str | None = None
    body: str | None = None
    share_with: Literal["家族", "縁のある人まで"] | None = None
    letter: str | None = None
    theme: str | None = None
    at_meeting: bool | None = None
    match: str | None = None
    reply: Literal["そうです", "今は話したくない"] | None = None
    name: str | None = None
    called_name: str | None = None
    role: str | None = None
    relation: str | None = None
    residence: str | None = None
    birth_year: int | None = None
    gender: str | None = None
    is_deceased: bool | None = None
    memo: str | None = None
    delegates: list[str] | None = None
    themes: list[str] | None = None
    room_kind: Literal["1対1", "グループ"] | None = None
    members: list[str] | None = None
    present: bool | None = None

    @model_validator(mode="after")
    def _has_required(self) -> Line:
        missing = [name for name in _REQUIRED[self.kind] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.kind} の行に {'、'.join(missing)} がありません")
        if self.kind == "発言" and (self.text is None) == (self.answer is None):
            raise ValueError("発言の行には text か answer のどちらか1つを書いてください")
        return self

    @property
    def day(self) -> date:
        return self.at.date()


class FixedWord(BaseModel):
    """渡された決まった言葉。一字も違わず、この日の行に入っていなければならない。"""

    model_config = ConfigDict(extra="forbid")

    date: date
    text: str
    by: str | None = None
    room: str | None = None


@dataclass
class Finding:
    """点検で見つかったこと。problem は流せない問題、それ以外は知らせるだけ。"""

    number: int
    message: str
    problem: bool = True

    def __str__(self) -> str:
        head = "問題" if self.problem else "知らせ"
        place = f"{self.number}行目" if self.number else "全体"
        return f"{head}｜{place}｜{self.message}"


class StopRun(RuntimeError):
    """流すのを止めるとき。ここまでの進み具合は残っている。"""


# ---- 点検（API も AI も呼ばない） ----


def read_script(path: Path) -> tuple[list[Line], list[Finding]]:
    """ファイルを読む。読めない行は問題として返し、残りの点検を続ける。"""
    lines: list[Line] = []
    findings: list[Finding] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            lines.append(Line.model_validate_json(raw))
        except ValidationError as error:
            detail = "；".join(item["msg"] for item in error.errors()[:2])
            findings.append(Finding(number, f"読めません：{detail}"))
    return lines, findings


def read_fixed(path: Path) -> list[FixedWord]:
    return [FixedWord.model_validate_json(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def said_texts(line: Line) -> list[str]:
    """声や記録になる文。決まった言葉を探す先。hint は入れない（言うとは限らないため）。"""
    texts = [value for value in (line.text, line.body) if value]
    if line.answer:
        texts += line.answer.musts
    return texts


def _all_texts(line: Line) -> list[str]:
    texts = said_texts(line)
    for value in (line.topic, line.memo, line.answer.hint if line.answer else None):
        if value:
            texts.append(value)
    return texts


def forbidden_in(text: str) -> list[str]:
    """病名、診断、泊まりの話に当たる言葉を返す。"""
    found = [word for word in (*_MEDICAL_WORDS, *_STAY_WORDS) if word in text]
    for word in _MEDICAL_ENDING.findall(text):
        if not word.endswith(tuple(_NOT_MEDICAL)) and not any(word.endswith(item) for item in found):
            found.append(word)
    return found


def check_lines(lines: list[Line], fixed: list[FixedWord] | None = None) -> list[Finding]:
    """部屋と話し手、時刻の順、言葉の決まりを点検する。"""
    findings: list[Finding] = []
    roles = dict(SEEDED_ROLES)
    deceased: set[str] = set()
    delegates = set(SEEDED_DELEGATES)
    rooms: dict[str, set[str]] = {TWIN_ROOM: {SELF_NAME}}
    twin_in: set[str] = {TWIN_ROOM}
    letters: dict[str, str] = {}
    open_day: date | None = None
    opened_days: set[date] = set()
    closed_day: date | None = None
    spoken_by_self: list[str] = []
    previous: datetime | None = None
    open_lines = 0

    def problem(number: int, message: str) -> None:
        findings.append(Finding(number, message))

    def can_act(number: int, line: Line) -> bool:
        if line.by in NEVER_SPEAK or line.by in deceased:
            problem(number, f"{line.by}はログインしないので、話し手にできません")
            return False
        if line.by not in roles:
            problem(number, f"{line.by}はまだいない人です")
            return False
        if roles[line.by] == "専門職" and line.day < config.PROFESSIONAL_FROM:
            problem(number, f"{line.by}は{config.PROFESSIONAL_FROM}より前には話し手にできません")
            return False
        return True

    for number, line in enumerate(lines, start=1):
        if previous is not None and line.at < previous:
            problem(number, f"時刻が前の行より前です（{line.at} < {previous}）")
        previous = line.at
        if not can_act(number, line):
            continue
        for text in _all_texts(line):
            for word in forbidden_in(text):
                problem(number, f"書かない言葉「{word}」があります")
        for text in said_texts(line):
            for word in _CARE_WORDS:
                if word in text:
                    findings.append(Finding(number, f"「{word}」があると、ツインは決まった文とつなぎ先を返します", problem=False))
        if open_day is not None and line.day != open_day:
            problem(number, f"{open_day} の会話を閉じる前に日付が変わっています")
            open_day = None
        if line.photo and not (PHOTO_DIR / line.photo).exists():
            problem(number, f"写真 {line.photo} が {PHOTO_DIR} にありません")
        if line.kind == "発言":
            if line.room not in rooms:
                problem(number, f"部屋「{line.room}」は、まだ作られていません")
                continue
            if line.by not in rooms[line.room]:
                problem(number, f"{line.by}は部屋「{line.room}」にいません")
            if line.answer:
                if line.room not in twin_in:
                    problem(number, f"部屋「{line.room}」にはツインがいないので、answer は書けません")
                for must in line.answer.musts:
                    if not must.endswith(_END_OF_SENTENCE):
                        problem(number, f"must は文の切れ目で終わらせてください：{must}")
            if line.room == TWIN_ROOM:
                if open_day is None:
                    if line.answer:
                        problem(number, "会話の最初の行は answer にできません。ツインがまだ話していません")
                    if line.day in opened_days:
                        problem(number, f"ツインとの部屋の会話は1日に1回までです（{line.day}）")
                    open_day = line.day
                    opened_days.add(line.day)
                    open_lines = 0
                open_lines += 1
            if line.by == SELF_NAME:
                spoken_by_self += said_texts(line)
        elif line.kind == "閉じる":
            if line.room != TWIN_ROOM:
                problem(number, "閉じるのはツインとの部屋だけです")
            elif line.by != SELF_NAME:
                problem(number, "閉じるのは本人だけです")
            elif open_day is None:
                problem(number, "閉じる会話がありません")
            else:
                closed_day = open_day
                open_day = None
        elif line.kind == "便り":
            if line.by != SELF_NAME:
                problem(number, "便りを出せるのは本人だけです")
            if closed_day != line.day:
                problem(number, "便りは、同じ日に閉じた会話から作ります")
            if line.ref in letters:
                problem(number, f"便りの ref「{line.ref}」が重なっています")
            letters[line.ref] = line.share_with
        elif line.kind == "コメント":
            scope = letters.get(line.letter)
            if scope is None:
                problem(number, f"便り「{line.letter}」は、まだ出ていません")
            elif roles[line.by] == "専門職" or (scope == "家族" and roles[line.by] == "縁のある人"):
                problem(number, f"{line.by}はこの便りを見られません")
        elif line.kind == "今の様子":
            if line.by not in delegates and roles[line.by] != "専門職":
                problem(number, f"{line.by}は今の様子を入れられません（託された家族か専門職だけです）")
        elif line.kind in ("確かめ", "訂正"):
            if line.by != SELF_NAME:
                problem(number, f"{line.kind}は本人だけです")
            if not any(line.match in text for text in spoken_by_self):
                findings.append(
                    Finding(number, "match が本人の文に見つかりません。AI の推測を指すのでなければ、流すときに止まります", problem=False)
                )
        elif line.kind == "人":
            if line.is_deceased:
                deceased.add(line.name)
            if line.name not in roles:
                if not line.role:
                    problem(number, f"新しい人 {line.name} には role を書いてください")
                roles[line.name] = line.role or "家族"
            elif line.role:
                roles[line.name] = line.role
            if line.name in NEVER_SPEAK:
                problem(number, f"{line.name}を登録するとログインできる人になります。言葉の中に出してください")
        elif line.kind == "託す":
            if line.by != SELF_NAME:
                problem(number, "託す設定は本人だけです")
            delegates = set(line.delegates)
        elif line.kind == "招く":
            if line.day < config.PROFESSIONAL_FROM:
                problem(number, f"招けるのは {config.PROFESSIONAL_FROM} 以降です")
            if line.by != SELF_NAME and line.by not in delegates:
                problem(number, f"{line.by}は招けません")
            roles[line.name] = "専門職"
        elif line.kind == "部屋":
            if line.room == TWIN_ROOM or line.room in rooms:
                problem(number, f"部屋「{line.room}」の名前が重なっています")
            members = {*line.members, line.by}
            for member in members:
                if member not in roles or member in deceased or member in NEVER_SPEAK:
                    problem(number, f"{member}は部屋に入れません")
            rooms[line.room] = members
        elif line.kind == "ツインを入れる":
            if line.room not in rooms:
                problem(number, f"部屋「{line.room}」は、まだ作られていません")
            elif SELF_NAME not in rooms[line.room]:
                problem(number, "本人がいない部屋にはツインを入れられません")
            elif line.present:
                twin_in.add(line.room)
            else:
                twin_in.discard(line.room)
        elif line.kind == "整理" and line.by != SELF_NAME:
            problem(number, "整理は本人だけです")
    if open_day is not None:
        problem(len(lines), f"{open_day} の会話が閉じられていません")
    findings += _check_fixed(lines, fixed or [])
    return findings


def _check_fixed(lines: list[Line], fixed: list[FixedWord]) -> list[Finding]:
    """決まった言葉が、決まった日に一字も違わず入っているかを見る。"""
    findings: list[Finding] = []
    for word in fixed:
        same_day = [
            line
            for line in lines
            if line.day == word.date and (word.by is None or line.by == word.by) and (word.room is None or line.room == word.room)
        ]
        if any(word.text in text for line in same_day for text in said_texts(line)):
            continue
        other = [line.day.isoformat() for line in lines if any(word.text in text for text in said_texts(line))]
        where = f"（{'、'.join(other)} にはあります）" if other else ""
        findings.append(Finding(0, f"{word.date} の決まった言葉が入っていません：{word.text}{where}"))
    return findings


# ---- 流す ----


class AnswerDraft(BaseModel):
    """本人の答えの案。AI は must の前と後の文だけを書く。

    must はプログラムが間に入れる。AI に書き写させないので、must の言葉は一字も変わらない。
    """

    before: str = Field(default="", max_length=300)
    after: str = Field(default="", max_length=300)

    def joined(self, musts: list[str]) -> str:
        return f"{self.before.strip()}{''.join(musts)}{self.after.strip()}"


ANSWER_SYSTEM = """あなたは、架空の人物「誠一さん」の言葉を書く係です。記録を作るための架空の会話です。

## 誠一さん
1952年生まれ。大牟田市の出身で、市役所を定年まで勤めました。縁側と庭のある家に一人で暮らしています。頑固で、自由が好きです。
妻の和子さんは2024年に亡くなりました。娘の美穂さんは福岡市に住み、年に数回帰ってきます。孫の健太くんがいます。
将棋仲間の正雄さんが訪ねてきます。ご近所の節子さんが声をかけてくれます。

## 書き方
- ツインの直前の言葉に、誠一さんとして答えます。1〜2文の話し言葉で、短く書きます。
- 渡された「筋」の話だけを書きます。筋にない人や出来事を作りません。
- 「必ず言う言葉」は、プログラムが before と after の間に入れます。あなたは書き写しません。
- before は、必ず言う言葉の前に置く文です。「。」で終わらせます。なければ空にします。
- after は、必ず言う言葉の後に続く文です。なければ空にします。
- 「必ず言う言葉」がないときは、答えを before に書きます。
- 病名、診断、薬、泊まりの話、ショートステイの話は書きません。
- ツインの言葉を繰り返しません。"""


def answer_prompt(line: Line, recent: list[str], feedback: str | None = None) -> str:
    parts = [f"今日：{line.day.isoformat()}", "## 直前のやりとり", *recent]
    if line.answer.hint:
        parts += ["## 筋", line.answer.hint]
    if line.answer.musts:
        parts += ["## 必ず言う言葉（一字も変えない）", *line.answer.musts]
    if feedback:
        parts += ["## 前の案の直すところ", feedback]
    return "\n".join(parts)


def stands_alone(must: str, text: str) -> bool:
    """must が、前の文とつながらずに1つの文として入っているかを返す。

    記録は文の切れ目で分かれる。「庭なので、毎朝見るのが楽しみです。」のようにつながると、
    前の言葉が付いたまま本人の言葉になり、決まった言葉が決まった形で届かないため。
    """
    start = text.find(must)
    while start != -1:
        before = text[:start].rstrip()
        if not before or before.endswith(_END_OF_SENTENCE):
            return True
        start = text.find(must, start + 1)
    return False


def _answer_problem(text: str, line: Line) -> str | None:
    if not text:
        return "答えが空です"
    joined = [must for must in line.answer.musts if not stands_alone(must, text)]
    if joined:
        return "before は「。」で終わらせてください。必ず言う言葉の前の文とつなげません"
    words = forbidden_in(text)
    if words:
        return f"書かない言葉があります：{'、'.join(words)}"
    return None


def _usd(result) -> float:
    return config.cost_usd(result.model, result.input_tokens, result.output_tokens) + config.cache_cost_usd(
        result.model, result.cache_read_tokens, result.cache_write_tokens
    )


@dataclass
class Progress:
    """進み具合。1行終わるごとに書くので、止まっても続きから流せる。"""

    path: Path
    digests: list[str] = field(default_factory=list)
    rooms: dict[str, int] = field(default_factory=dict)
    room_seq: dict[str, int] = field(default_factory=dict)
    letters: dict[str, dict] = field(default_factory=dict)
    photos: dict[str, int] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    closed: dict | None = None
    sending: int | None = None
    answer_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    story: dict[str, dict] = field(default_factory=dict)
    extra: dict[str, list] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Progress:
        if not path.exists():
            return cls(path)
        return cls(path, **json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        data = {key: value for key, value in self.__dict__.items() if key != "path"}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)


def line_digest(line: Line) -> str:
    return hashlib.sha256(line.model_dump_json().encode("utf-8")).hexdigest()[:16]


class Runner:
    """1行ずつ API へ送る。client は画面と同じ道だけを呼ぶ。"""

    def __init__(self, client, progress: Progress, *, keep_going: bool = False) -> None:
        self.client = client
        self.progress = progress
        self.keep_going = keep_going
        self.logged_in: int | None = None
        self.ids: dict[str, int] = {}

    # ---- 共通 ----

    def _call(self, method: str, path: str, *, user: str | None = None, json_body: dict | None = None, content: bytes | None = None):
        headers = {"X-NT-User": str(self._user_id(user))} if user else {}
        if content is not None:
            return self.client.request(method, path, headers=headers, content=content)
        return self.client.request(method, path, headers=headers, json=json_body)

    def _ok(self, response, what: str) -> dict:
        if response.status_code >= 400:
            raise StopRun(f"{what} が失敗しました（{response.status_code}）：{response.text[:200]}")
        return response.json() if response.content else {}

    def _send(self, path: str, *, user: str, json_body: dict | None, what: str, method: str = "POST") -> dict:
        """送る。AI の一時の失敗（503）は、少し待って送り直す。"""
        for wait in (*RETRY_SECONDS, None):
            response = self._call(method, path, user=user, json_body=json_body)
            if response.status_code != 503 or wait is None:
                return self._ok(response, what)
            time.sleep(wait)
        raise StopRun(f"{what} が失敗しました")

    def check_mode(self) -> None:
        """架空用の起動でなければ、何も送らずに止まる。"""
        mode = self._ok(self.client.get("/api/mode"), "起動の種類の確かめ")
        if mode.get("mine") is not False or mode.get("label") != config.LABEL_FICTIONAL:
            raise StopRun("架空用の起動ではありません。何も送らずに止めます。")

    def _people(self) -> dict[str, dict]:
        """亡くなった方も含めた人の一覧。本人だけが読める。"""
        response = self.client.get("/api/people", headers={"X-NT-User": str(self._user_id(SELF_NAME))})
        return {item["name"]: item for item in self._ok(response, "人の一覧")["items"]}

    def _user_id(self, name: str) -> int:
        if name not in self.ids:
            # 招いた人や足した人が増えるので、知らない名前のときだけ読み直す
            items = self._ok(self.client.get("/api/users"), "名前の一覧")["items"]
            self.ids = {item["name"]: item["id"] for item in items}
        if name not in self.ids:
            raise StopRun(f"{name}はログインできません")
        return self.ids[name]

    def _login(self, name: str) -> None:
        """その人としてログインする。話す人が替わるときだけ呼ぶ。"""
        user_id = self._user_id(name)
        if self.logged_in != user_id:
            self._ok(self.client.post("/api/login", json={"user_id": user_id}), f"{name}のログイン")
            self.logged_in = user_id

    def _set_day(self, day: date) -> None:
        """日付を動かす。流している間に本当の日付が変わっても合うよう、行ごとに送る。"""
        result = self._ok(self.client.post("/api/dev/date", json={"date": day.isoformat()}), "日付の変更")
        if result.get("today") != day.isoformat():
            raise StopRun(f"日付を {day} にできませんでした（{result.get('today')}）")

    def _room_id(self, name: str, user: str) -> int:
        if name in self.progress.rooms:
            return self.progress.rooms[name]
        items = self._ok(self._call("GET", "/api/rooms", user=user), "部屋の一覧")["items"]
        for item in items:
            if (name == TWIN_ROOM and item["kind"] == "ツイン") or (name != TWIN_ROOM and item["name"] == name):
                self.progress.rooms[name] = item["id"]
                return item["id"]
        raise StopRun(f"部屋「{name}」が見つかりません")

    def _upload(self, name: str, kind: str, user: str) -> int:
        """写真を送る。同じファイルは1回だけ送る。"""
        key = f"{kind}:{name}"
        if key not in self.progress.photos:
            response = self._call("POST", f"/api/photos?kind={kind}", user=user, content=(PHOTO_DIR / name).read_bytes())
            self.progress.photos[key] = self._ok(response, f"写真 {name}")["id"]
        return self.progress.photos[key]

    def _new_messages(self, room: str, room_id: int, user: str) -> list[dict]:
        """まだ読んでいない発言を読む。ツインが書き終えるまで待つ。"""
        after = self.progress.room_seq.get(room, 0)
        while True:
            view = self._ok(self._call("GET", f"/api/rooms/{room_id}/messages?after={after}", user=user), "部屋の発言")
            if not view["thinking"]:
                break
            time.sleep(1)
        self.progress.room_seq[room] = view["last_seq"]
        return view["items"]

    def _extra(self, line: Line, entry: dict) -> None:
        self.progress.extra.setdefault(str(line.day.year), []).append({"at": line.at.isoformat(), "by": line.by, **entry})

    # ---- 本人の答え ----

    def _answer_text(self, number: int, line: Line, room_id: int) -> str:
        """ツインの実際の言葉を見て、本人の答えを作る。止まっても同じ文を送れるよう、送る前に覚える。"""
        key = str(number)
        if key in self.progress.answers:
            return self.progress.answers[key]
        after = max(self.progress.room_seq.get(line.room, 0) - 8, 0)
        view = self._ok(self._call("GET", f"/api/rooms/{room_id}/messages?after={after}", user=line.by), "部屋の発言")
        names = {person["id"]: person["name"] for person in view["people"]}
        recent = [
            f"{'ツイン' if item['is_twin'] else names.get(item['sender_user_id'], line.by)}：{item['text']}"
            for item in view["items"]
            if item.get("text")
        ]
        feedback = None
        text = None
        for _ in range(2):
            result = ai.call_structured(
                "架空ログの作成", system=ANSWER_SYSTEM, content=answer_prompt(line, recent, feedback), schema=AnswerDraft
            )
            self.progress.answer_usd += _usd(result)
            draft = result.value.joined(line.answer.musts)
            feedback = _answer_problem(draft, line)
            if feedback is None:
                text = draft
                break
        if text is None:
            if not line.answer.musts:
                raise StopRun(f"{number}行目：筋に合う答えを作れませんでした（{feedback}）")
            # 言葉を変えるより、必ず言う言葉だけを送る
            text = "".join(line.answer.musts)
            self.progress.warnings.append(f"{number}行目：答えを作れず、must の文だけを送りました")
        self.progress.answers[key] = text
        self.progress.save()
        return text

    # ---- 行ごとの処理 ----

    def run_line(self, number: int, line: Line, script_key: str) -> None:
        self._set_day(line.day)
        self._login(line.by)
        handler = getattr(self, f"_do_{_HANDLERS[line.kind]}")
        handler(number, line, script_key)

    def _conversation(self, day: date, line: Line | None = None) -> dict:
        entry = self.progress.story.setdefault(
            day.isoformat(), {"trigger": "", "turns": [], "confirmation": "合っています", "letter": None, "comments": []}
        )
        if line is not None and line.topic and not entry["trigger"]:
            entry["trigger"] = line.topic
        return entry

    def _do_say(self, number: int, line: Line, script_key: str) -> None:
        room_id = self._room_id(line.room, line.by)
        text = self._answer_text(number, line, room_id) if line.answer else line.text
        body = {"text": text, "client_key": f"script-{script_key}-{number}"}
        if line.photo:
            kind = "会話" if line.room == TWIN_ROOM else "部屋"
            body["photo_id"] = self._upload(line.photo, kind, line.by)
        self._send(f"/api/rooms/{room_id}/messages", user=line.by, json_body=body, what=f"{number}行目の発言")
        self._record_messages(line, room_id)

    def _record_messages(self, line: Line, room_id: int) -> None:
        for item in self._new_messages(line.room, room_id, line.by):
            if not item.get("text") or item.get("kind") == "お知らせ":
                continue
            if line.room == TWIN_ROOM:
                entry = self._conversation(line.day, line)
                if not entry["trigger"]:
                    entry["trigger"] = item["text"][:20]
                entry["turns"].append({"speaker": "ツイン" if item["is_twin"] else SELF_NAME, "text": item["text"]})
            else:
                speaker = "ツイン" if item["is_twin"] else line.by
                self._extra(line, {"kind": "部屋の発言", "room": line.room, "speaker": speaker, "text": item["text"]})

    def _do_close(self, number: int, line: Line, _key: str) -> None:
        room_id = self._room_id(line.room, line.by)
        # 応答が返るまでに、言明への分割と要約も終わっている
        result = self._send(f"/api/rooms/{room_id}/close", user=line.by, json_body=None, what=f"{number}行目の閉じる")
        if not result.get("closed"):
            raise StopRun(f"{number}行目：閉じる会話がありませんでした")
        self.progress.closed = {"day": line.day.isoformat(), "id": result["conversation_id"]}
        self._record_messages(line, room_id)

    def _do_letter(self, number: int, line: Line, _key: str) -> None:
        body = {
            "conversation_id": self.progress.closed["id"],
            "body": line.body,
            "share_with": line.share_with,
        }
        if line.photo:
            body["photo_id"] = self._upload(line.photo, "便り", line.by)
        # 便りには送り直しの印がないので、止まった行は同じ本文がもうあるかを先に見る
        letter_id = self._already_sent_letter(number, line)
        if letter_id is None:
            letter_id = self._send("/api/letters", user=line.by, json_body=body, what=f"{number}行目の便り")["id"]
        self.progress.letters[line.ref] = {"id": letter_id, "day": line.day.isoformat()}
        self._conversation(line.day)["letter"] = line.body

    def _already_sent_letter(self, number: int, line: Line) -> int | None:
        if self.progress.sending != number:
            return None
        items = self._ok(self._call("GET", "/api/letters", user=line.by), "便りの一覧")["items"]
        return next((item["id"] for item in items if item["body"] == line.body), None)

    def _do_comment(self, number: int, line: Line, script_key: str) -> None:
        letter = self.progress.letters[line.letter]
        body = {"text": line.text, "client_key": f"script-{script_key}-{number}"}
        self._send(f"/api/letters/{letter['id']}/comments", user=line.by, json_body=body, what=f"{number}行目のコメント")
        if line.by in _LOG_COMMENTERS:
            self._conversation(date.fromisoformat(letter["day"]))["comments"].append({"speaker": line.by, "text": line.text})
        else:
            self._extra(line, {"kind": "コメント", "letter": line.letter, "text": line.text})

    def _do_observation(self, number: int, line: Line, _key: str) -> None:
        if self.progress.sending == number and self._find_statements(line.text, SELF_NAME):
            return
        body = {"text": line.text}
        if line.theme:
            body["theme"] = line.theme
        if line.at_meeting:
            body["at_meeting"] = True
        self._send("/api/observations", user=line.by, json_body=body, what=f"{number}行目の今の様子")
        self._extra(line, {"kind": "今の様子", "text": line.text})

    def _find_statements(self, match: str, user: str) -> list[dict]:
        """本文に match を含む、いま有効な記録を新しい順に返す。"""
        items = self._ok(self._call("GET", "/api/statements", user=user), "記録の一覧")["items"]
        found = [item for item in items if match in item["body"]]
        return sorted(found, key=lambda item: (item["effective_date"], item["created_at"]), reverse=True)

    def _target(self, number: int, line: Line) -> dict | None:
        found = self._find_statements(line.match, line.by)
        if found:
            # まだ確かめていない記録を先にする。同じ言葉を別の日に話したときに、確かめ済みの行を選ばないため
            return next((item for item in found if item["confirm_status"] == "未確認"), found[0])
        message = f"{number}行目：「{line.match}」を含む記録が見つかりません"
        if not self.keep_going:
            raise StopRun(message)
        self.progress.warnings.append(message)
        return None

    def _do_confirm(self, number: int, line: Line, _key: str) -> None:
        target = self._target(number, line)
        if target is None:
            return
        if self.progress.sending == number and target["confirm_status"] != "未確認":
            return
        action = "rest" if line.reply == "今は話したくない" else "confirm"
        self._send(f"/api/statements/{target['id']}/{action}", user=line.by, json_body=None, what=f"{number}行目の確かめ")
        self._extra(line, {"kind": "確かめ", "match": line.match, "reply": line.reply or "そうです"})

    def _do_correct(self, number: int, line: Line, _key: str) -> None:
        # 止まった行で、もう直してあれば送らない
        if self.progress.sending == number and self._find_statements(line.text, line.by):
            return
        target = self._target(number, line)
        if target is None:
            return
        self._send(f"/api/statements/{target['id']}/correct", user=line.by, json_body={"text": line.text}, what=f"{number}行目の訂正")
        self._conversation(line.day)["confirmation"] = "直した"
        self._extra(line, {"kind": "訂正", "match": line.match, "text": line.text})

    def _do_person(self, number: int, line: Line, _key: str) -> None:
        # 書いた項目だけを送る。null と書いた項目は空にする（亡くなった方の「同居」を消すときなど）
        values = {name: getattr(line, name) for name in _PERSON_FIELDS if name in line.model_fields_set}
        people = self._people()
        if line.name in people:
            self._send(f"/api/people/{people[line.name]['id']}", user=line.by, json_body=values, what=f"{number}行目の人", method="PUT")
        else:
            self._send("/api/people", user=line.by, json_body={"name": line.name, **values}, what=f"{number}行目の人")

    def _do_handover(self, number: int, line: Line, _key: str) -> None:
        people = self._people()
        body = {"delegate_ids": [people[name]["id"] for name in line.delegates], "themes": line.themes}
        self._send("/api/handover", user=line.by, json_body=body, what=f"{number}行目の託す", method="PUT")

    def _do_invite(self, number: int, line: Line, _key: str) -> None:
        names = {item["name"] for item in self._ok(self.client.get("/api/users"), "名前の一覧")["items"]}
        if line.name in names:
            return
        body = {"name": line.name, "called_name": line.called_name or line.name, "relation": line.relation or "ケアマネジャー"}
        self._send("/api/people/professional", user=line.by, json_body=body, what=f"{number}行目の招く")

    def _do_room(self, number: int, line: Line, _key: str) -> None:
        items = self._ok(self._call("GET", "/api/rooms", user=line.by), "部屋の一覧")["items"]
        existing = next((item for item in items if item["name"] == line.room), None)
        if existing:
            self.progress.rooms[line.room] = existing["id"]
            return
        users = {item["name"]: item["id"] for item in self._ok(self.client.get("/api/users"), "名前の一覧")["items"]}
        body = {"kind": line.room_kind, "name": line.room, "member_ids": [users[name] for name in line.members if name != line.by]}
        self.progress.rooms[line.room] = self._send("/api/rooms", user=line.by, json_body=body, what=f"{number}行目の部屋")["id"]

    def _do_twin(self, number: int, line: Line, _key: str) -> None:
        room_id = self._room_id(line.room, line.by)
        self._send(f"/api/rooms/{room_id}/twin", user=line.by, json_body={"present": line.present}, what=f"{number}行目のツイン")

    def _do_consolidate(self, number: int, line: Line, _key: str) -> None:
        self._send("/api/model/consolidate", user=line.by, json_body=None, what=f"{number}行目の整理")


_HANDLERS = {
    "発言": "say",
    "閉じる": "close",
    "便り": "letter",
    "コメント": "comment",
    "今の様子": "observation",
    "確かめ": "confirm",
    "訂正": "correct",
    "人": "person",
    "託す": "handover",
    "招く": "invite",
    "部屋": "room",
    "ツインを入れる": "twin",
    "整理": "consolidate",
}


def spent_usd(db_path: Path, progress: Progress) -> float:
    """このデータベースで使った費用。本人の答えを作った分は、記録に残らないので足す。"""
    return sum(item["cost_usd"] for item in summarize(db_path)) + progress.answer_usd


def write_story(progress: Progress, out_dir: Path) -> list[Path]:
    """今のログの形で書き出す。data/story/ には書かない。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    years: dict[str, dict] = {}
    for day, entry in progress.story.items():
        if entry["turns"]:
            log = LogConversation.model_validate(entry)
            years.setdefault(day[:4], {})[day] = log.model_dump()
    for year, values in years.items():
        path = out_dir / f"log_{year}.json"
        path.write_text(json.dumps(dict(sorted(values.items())), ensure_ascii=False, indent=0), encoding="utf-8")
        written.append(path)
    for year, values in progress.extra.items():
        path = out_dir / f"extra_{year}.json"
        path.write_text(json.dumps(values, ensure_ascii=False, indent=0), encoding="utf-8")
        written.append(path)
    return written


def run_lines(
    client,
    lines: list[Line],
    progress: Progress,
    *,
    db_path: Path,
    budget_usd: float = DEFAULT_BUDGET_USD,
    until: date | None = None,
    stop_after: int | None = None,
    keep_going: bool = False,
) -> int:
    """残りの行を流す。流した行の数を返す。"""
    runner = Runner(client, progress, keep_going=keep_going)
    runner.check_mode()
    digests = [line_digest(line) for line in lines]
    done = len(progress.digests)
    if digests[:done] != progress.digests:
        raise StopRun("すでに流した行が変わっています。別の --folder で流し直してください。")
    count = 0
    for number, line in enumerate(lines[done:], start=done + 1):
        if until and line.day > until:
            break
        if stop_after is not None and count >= stop_after:
            break
        if spent_usd(db_path, progress) > budget_usd:
            raise StopRun(f"費用の枠 ${budget_usd} を超えそうなので、{number}行目の前で止めました。")
        progress.sending = number
        progress.save()
        # 発言の印は行の中身から作る。止まって送り直しても同じ印になり、発言は増えない
        runner.run_line(number, line, digests[number - 1])
        progress.digests.append(digests[number - 1])
        progress.sending = None
        progress.save()
        count += 1
    return count


def safe_folder(folder: Path) -> Path:
    """保存先を確かめる。自分用の記録の置き場、今の架空用の保存先、data/story/ には流さない。"""
    resolved = folder.resolve()
    if "mine" in {part.lower() for part in resolved.parts}:
        raise StopRun("自分用の記録の置き場には流しません。")
    data_dir = config.DATA_DIR.resolve()
    if resolved == data_dir:
        raise StopRun("今の架空用の保存先には流しません。別の --folder を指定してください。")
    if resolved == data_dir / "story" or (data_dir / "story") in resolved.parents:
        raise StopRun("data/story/ には書きません。別の --folder を指定してください。")
    return resolved


def open_client(folder: Path) -> tuple[TestClient, Path]:
    """別のデータベースでアプリを開く。前の物語は読み込まない（demo.ensure_prologue を呼ばない）。

    デモの顔は入れる。写真のフォルダがなければ何も入れない。
    """
    deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
    folder.mkdir(parents=True, exist_ok=True)
    app = create_app(deployment=deployment)
    demo.ensure_demo_faces(deployment)
    return TestClient(app), deployment.db_path


def catch_up_for_resume(client: TestClient, lines: list[Line], progress: Progress) -> None:
    """前回止まったときに残った裏の処理を流し直す。サーバーの起動と同じ処理。

    先に日付を続きの行に合わせる。catch_up は今日と違う日の開いた会話を閉じるので、
    合わせないと、途中で止めた会話がここで閉じてしまい、続きの発言が別の会話になるため。
    関連するテスト：tests/test_run_script.py の test_会話の途中で止めて開き直しても_会話は閉じない
    """
    done = len(progress.digests)
    if done == 0 or not lines:
        return
    Runner(client, progress).check_mode()
    day = lines[min(done, len(lines) - 1)].day
    response = client.post("/api/dev/date", json={"date": day.isoformat()})
    if response.status_code >= 400:
        raise StopRun(f"日付の変更が失敗しました（{response.status_code}）")
    jobs.catch_up(client.app)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="script.jsonl を点検し、今ある API へ順に流します。")
    parser.add_argument("script", type=Path)
    parser.add_argument("--check", action="store_true", help="API も AI も呼ばず、ファイルだけを点検します")
    parser.add_argument("--fixed", type=Path, help="決まった言葉のファイル")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER, help="流し込む先のフォルダ")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="使ってよい費用（ドル）")
    parser.add_argument("--until", type=date.fromisoformat, help="この日までで止める")
    parser.add_argument("--stop-after", type=int, help="この行数を流して止める（確かめ用）")
    parser.add_argument("--keep-going", action="store_true", help="記録が見つからない行を書き残して続ける")
    args = parser.parse_args(argv)

    lines, findings = read_script(args.script)
    # 読めない行があると行番号がずれるので、先に読める形に直してもらう
    if not findings:
        findings = check_lines(lines, read_fixed(args.fixed) if args.fixed else None)
    for item in findings:
        print(item)
    problems = [item for item in findings if item.problem]
    print(f"{len(lines)}行。問題 {len(problems)} 件、知らせ {len(findings) - len(problems)} 件。")
    if args.check or problems:
        return 1 if problems else 0

    try:
        folder = safe_folder(args.folder)
        client, db_path = open_client(folder)
        progress = Progress.load(folder / "progress.json")
        catch_up_for_resume(client, lines, progress)
        count = run_lines(
            client,
            lines,
            progress,
            db_path=db_path,
            budget_usd=args.budget,
            until=args.until,
            stop_after=args.stop_after,
            keep_going=args.keep_going,
        )
    except StopRun as error:
        print(f"止めました：{error}")
        return 2
    written = write_story(progress, folder / "story")
    print(f"{count}行を流しました。全部で {len(progress.digests)}/{len(lines)} 行。費用 約 {spent_usd(db_path, progress):.3f} ドル。")
    for path in written:
        print(f"書き出し：{path}")
    for warning in progress.warnings:
        print(f"注意：{warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
