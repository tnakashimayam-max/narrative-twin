"""script.jsonl を、今ある API へ順に流す。

役割：決まり（docs/script-format.md）に沿ったファイルを、その人としてログインして時刻の順に流し、
      別のデータベースに10年分の記録を作る。--check では API も AI も呼ばずにファイルだけを点検する。
4つの極：1. 書く・話す、2. 分かち合う、3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、6（話さない自由）、8（人物と記録はすべて架空）、9（must の言葉は一字も変えない）。

サーバーに専用の受け口を作らない。画面が使うのと同じ道を、同じプロセスの中のアプリへ送る（fastapi.testclient）。
ネットワークを使わないので、8000番にも8001番にも接続できない作りになる。
画面に道がない「段階」と「推測」だけは、道具の中から ledger の決まりを通して入れる。台本の中の探し物は
データベースを読むだけで行う。GET /api/statements は全部の行を読むので、台帳が増えると1回で数秒かかるため。

保存先は --folder の下だけで、data/narrative_twin.db には触れない。
流す時刻は at のとおりにする（POST /api/dev/date と同じ計算を、HTTP を通さずに行う）。同じ時刻の行は1秒ずつずらし、順番が時刻に残るようにする。

裏の処理を待たずに進める（--workers、既定は2）。
- 本線で待つもの：ツインの返事、「今日はここまで」、会話の要約、グループのツイン。次の発言と次の日の会話が読むため。
- 裏に回すもの：会話を閉じたあとの言明への分割と人の候補、コメントのあとのスレッドの問いと言明への分割。
- 全部終わるまで待つ行：_WAIT_FOR_ALL。記録を探す行と、裏の処理が読むものを変える行。
- 問いが出るかもしれないコメントのあとだけ、同じ便りの処理を待つ。ツインの問いが次のコメントの後ろに入らないようにするため。
- 待ちには上限がある。ツインの記憶の遅れを数日分以内にするため。
- 同時の数は2本から始めて、問題がなければ少しずつ --workers まで上げる。429 か 529 で半分にする。
  急に増やすと API が断ることがあるため。材料の数字を --folder の jobs_stats.json に残す。本文は書かない。
- 一時の失敗は retry-after に従って待つ。待つ間は全体で新しく AI を呼ばない。何度やってもだめなら止める。
  止めても発言は残り、同じ命令の続きで全部入る。

推測の行：出どころは「AIの推測」だが、この行は台本の文で AI の出力ではない。AI を呼ばない。
訂正の行の guess：相手を AI の推測だけから選び、match の話でなければ止まる。流し直しは fiction/correction_scene.py。

止まっても、落ちても、同じ命令で続きから流せる。発言、コメント、閉じるは送り直しても増えないので、
進み具合の全部の保存はまとめて行い、ほかの種類の行は1行ごとに保存する。送り始めた行の番号だけは毎行残す。
全部を毎行書くと、10年分で進み具合のファイルが数MBになり、それだけで遅くなるため。

年の変わり目と整理の行の直後に、--folder の checkpoints の下へデータベースと進み具合の控えを取る。問題が見つかったら
--restore-checkpoint でその年の前へ戻し、同じ命令で流し直す。戻す前の分は消さず、replaced-時刻 へ移す。
100行ごとの速さ、累計の費用、データベースとメモリの大きさを --folder の speed.jsonl に残す。本文は書かない。

関連するテスト：tests/test_run_script.py、tests/test_run_script_jobs.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Literal, get_args

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app import ai, clock, config, db, demo, devlog, jobs, ledger, memory, room_store, splitter, twin
from app.main import create_app
from app.schemas import (
    CommentDraft,
    ConfirmStatus,
    ConversationDraft,
    LifeStage,
    Period,
    QuestionResult,
    ShareScope,
    Source,
    SplitTargetKind,
    StatementDraft,
    Theme,
    Utterance,
)
from app.splitter import _is_hedged
from app.twin import _BODY_WORDS, _DISTRESS_WORDS, _LIFE_WORDS
from fiction import added_words
from fiction.log_models import LogComment, LogConversation
from scripts.usage_report import summarize

DEFAULT_FOLDER = config.DATA_DIR / "trial" / "script_run"
PHOTO_DIR = config.DATA_DIR / "demo_photos"
SELF_NAME = "誠一さん"
TWIN_ROOM = "ツイン"
# 物語の中でログインしない人。今はいない。健太くんも「人」の行で登録すれば話し手にできる
NEVER_SPEAK: set[str] = set()
SEEDED_ROLES = {"誠一さん": "本人", "和子さん": "家族", "美穂さん": "家族", "正雄さん": "縁のある人", "節子さん": "縁のある人"}
SEEDED_DELEGATES = {"美穂さん"}
DEFAULT_BUDGET_USD = 5.0
# 本線の AI の一時の失敗を待って送り直す秒数。同じ client_key で送るので発言は増えない
RETRY_SECONDS = (2, 5, 15, 30, 60)
_END_OF_SENTENCE = ("。", "！", "？")

# ---- 裏の処理の決まり ----
DEFAULT_WORKERS = 2
# 少なく始める本数。--workers はこの上の上限
FIRST_WORKERS = 2
# 問題がなければ、この秒数ごとに1本ずつ上げる。急に増やすと 429 が返ることがあるため
RAMP_SECONDS = 300
# 待ちの上限はその時点の本数の8倍。ツインの記憶の遅れを数日分以内にするため
QUEUE_PER_WORKER = 8
# 裏の処理の一時の失敗を待つ秒数。この回数を超えたら止める。retry-after があれば、そちらに従う
JOB_RETRY_SECONDS = (5, 10, 20, 40, 60, 60, 60, 60)
# retry-after がこれより長いなら待たずに止める。夜の間ずっと止まったままにしないため。同じ命令で続きから流せる
MAX_RETRY_AFTER_SECONDS = 900
# データベースのロックの失敗は全体を止めず、その処理だけ少し待つ
LOCK_RETRY_SECONDS = 1
# 429 と 529 は本数を半分にする。ほかの一時の失敗は本数を変えない
_RATE_LIMITED = {429, 529}
# 待っても直らない番号。すぐに止める
_NEVER_RETRY = {400, 401, 403, 404, 413, 422}
# 書き込みの鍵をこの秒数より長く待ったら、待ちとして数える
SLOW_WRITE_SECONDS = 0.05
STATS_EVERY_SECONDS = 30
STATS_FILE = "jobs_stats.json"
_JOIN_SECONDS = 120

# ---- 費用の確かめと保存の頻度 ----
# 費用の確かめは表を集計するので、毎行行うと全件で遅くなる。この行数ごとにする。超え方は数行分の AI の費用までに収まる
BUDGET_EVERY_LINES = 20
# 進み具合の全部の保存の間隔。_SAFE_TO_RESEND の行だけが続くときに使う
SAVE_EVERY_LINES = 200
# 送り直しても増えない行。client_key か、閉じたかどうかの確かめで分かる
_SAFE_TO_RESEND = {"発言", "コメント", "閉じる"}
# 裏の処理が全部終わるまで待つ行。記録を探す行に加えて、託す、人、招くも待つ。
# 託すは今ある言葉に範囲を当てるので、まだ分けていない言葉だけ範囲が変わらず、--workers で結果が変わるため。
# 人と招くは、裏の処理が読む人の一覧を変えるため
_WAIT_FOR_ALL = {"確かめ", "訂正", "本人だけにする", "推測", "整理", "段階", "託す", "人", "招く"}

# ---- 速さの記録と控え ----
# データが増えると遅くなるかを見るため、この行数ごとに速さと費用を書く。本文は入れない
SPEED_EVERY_LINES = 100
SPEED_FILE = "speed.jsonl"
# 年の変わり目に、データベースと進み具合の控えを取る。問題が見つかったら、その年の前から流し直すため
CHECKPOINT_FOLDER = "checkpoints"
CHECKPOINT_INFO = "checkpoint.json"
# 訂正の場面を書き残すファイル。読む人が、推測と訂正の文がかみ合うかを確かめるため
SCENE_FILE = "correction_scene.json"
# 訂正の相手の推測が決まった話でないときの終了の番号。ほかの止まり方と分けて、流し直しの道具が見分ける
GUESS_EXIT = 3
# 同じ字を含んでも別の話になる語。「庭」の話を探すときに、家庭の話を数えないため
_OTHER_WORDS = ("家庭", "校庭")
# 控えに写す進み具合のファイル。写真は増えるだけで、戻しても番号が重ならないので写さない
_PROGRESS_FILES = ("progress.json", "progress.sending")

# ---- 流す時刻 ----
# 同じ時刻の行を離す幅。応答の処理の揺れより十分に長くして、順番が時刻に残るようにする
LINE_SPACING = timedelta(seconds=1)
# 1日の最後に使う時刻。少し手前にして、処理の間に次の日へ入らないようにする
_DAY_END = day_time(23, 59, 58)

STAGE_TRIGGER = "人生の段階"

# 病名と診断。発言と記録に入れない（docs/script-format.md）。物語を作った時点の決まりで、11/6 の原則17のあともデータは作り直していない
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
# ツインのいない部屋の発言でだけ通す語。人だけの部屋の本文は room_messages にだけ入り、AI にも記録にも渡らないため
_PEOPLE_ROOM_WORDS = ("認知症", "アルツハイマー", "診断")
# 漢字の語が症、炎、病で終わるものも病名として見る。看病はふだんの言葉なので除く
_MEDICAL_ENDING = re.compile(r"[一-鿿]+(?:症|炎|病)(?![一-鿿])")
_NOT_MEDICAL = {"看病"}
# 泊まりの話は入れない。ケアマネジャーが聞いたときに「まだ聞けていません」と答える場面に使うため
_STAY_WORDS = ("泊まり", "泊まる", "泊まっ", "ショートステイ", "ショート", "短期入所", "宿泊")
_CARE_WORDS = (*_LIFE_WORDS, *_BODY_WORDS, *_DISTRESS_WORDS)
# 今のログの形に書けるコメントの話し手。それ以外の人のコメントは別のファイルに書く
_LOG_COMMENTERS = get_args(LogComment.model_fields["speaker"].annotation)

Kind = Literal[
    "発言",
    "閉じる",
    "便り",
    "コメント",
    "今の様子",
    "確かめ",
    "訂正",
    "人",
    "託す",
    "招く",
    "部屋",
    "ツインを入れる",
    "整理",
    "段階",
    "本人だけにする",
    "推測",
    "受け答え",
]

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
    "段階": ("stage",),
    "本人だけにする": ("text",),
    "推測": ("text", "match"),
    "受け答え": ("hint",),
}
_PERSON_FIELDS = ("called_name", "role", "relation", "residence", "birth_year", "gender", "is_deceased", "memo")
# text があっても、本人が話した文ではない行。決まった言葉の確かめとツインの決まった文の知らせに数えない
_NOT_SAID = {"本人だけにする", "推測"}


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
    stage: LifeStage | None = None
    # 訂正の行：true なら、相手は AI の推測だけから選ぶ。match の話の推測がなければ止まる
    guess: bool | None = None
    # false なら、この行ではツインが問わない（fiction/answer_plan.py）
    ask: bool | None = None
    # 受け答えの行：渡す事実、字数の上限、受け答えのあとにツインが返すか
    hint: str | None = None
    limit: int | None = None
    twin_replies: bool | None = None

    @model_validator(mode="after")
    def _has_required(self) -> Line:
        missing = [name for name in _REQUIRED[self.kind] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.kind} の行に {'、'.join(missing)} がありません")
        if self.kind == "発言":
            if self.text is not None and self.answer is not None:
                raise ValueError("発言の行には text か answer のどちらか1つを書いてください")
            if self.text is None and self.answer is None and self.photo is None:
                raise ValueError("発言の行には text か answer のどちらか1つを書いてください（写真だけなら photo）")
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


class GuessNotAbout(StopRun):
    """訂正の相手の推測が、決まった話ではない。流し直すか、台本の文にする（fiction/correction_scene.py）。"""


METHOD_AI = "AI の推測"
METHOD_SCRIPT = "台本の文"


def speaks_of(body: str, word: str) -> bool:
    """本文がその話をしているかを返す。別の語の中の同じ字は数えない。"""
    for other in _OTHER_WORDS:
        body = body.replace(other, "")
    return word in body


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
    texts = [value for value in (line.text, line.body) if value and line.kind not in _NOT_SAID]
    if line.answer:
        texts += line.answer.musts
    return texts


def _all_texts(line: Line) -> list[str]:
    texts = said_texts(line)
    not_said = line.text if line.kind in _NOT_SAID else None
    for value in (line.topic, line.memo, line.answer.hint if line.answer else None, not_said, line.hint):
        if value:
            texts.append(value)
    return texts


def forbidden_in(text: str, *, people_room: bool = False) -> list[str]:
    """病名、診断、泊まりの話に当たる言葉を返す。

    people_room は、ツインのいない部屋の発言のとき。_PEOPLE_ROOM_WORDS だけを通す。
    「アルツハイマー型認知症」の「型認知症」のように、_MEDICAL_ENDING が拾う語も、これらで終わるなら通す。
    それ以外の病名と泊まりの語は、ここでも止める。
    """
    allowed = _PEOPLE_ROOM_WORDS if people_room else ()
    found = [word for word in (*_MEDICAL_WORDS, *_STAY_WORDS) if word in text and word not in allowed]
    for word in _MEDICAL_ENDING.findall(text):
        if word.endswith(allowed) or word.endswith(tuple(_NOT_MEDICAL)):
            continue
        if not any(word.endswith(item) for item in found):
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
    spoken_by_self: list[str] = []
    previous: datetime | None = None

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
        people_room = line.kind == "発言" and line.room in rooms and line.room not in twin_in
        for text in _all_texts(line):
            for word in forbidden_in(text, people_room=people_room):
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
                open_day = None
        elif line.kind == "便り":
            if line.by != SELF_NAME:
                problem(number, "便りを出せるのは本人だけです")
            if line.day not in opened_days:
                problem(number, "便りは、同じ日にツインとの部屋の会話がある日に出します")
            if line.ref in letters:
                problem(number, f"便りの ref「{line.ref}」が重なっています")
            letters[line.ref] = line.share_with
        elif line.kind == "コメント":
            scope = letters.get(line.letter)
            if scope is None:
                problem(number, f"便り「{line.letter}」は、まだ出ていません")
            elif roles[line.by] == "専門職" or (scope == "家族" and roles[line.by] == "縁のある人"):
                problem(number, f"{line.by}はこの便りを見られません")
            if line.by == SELF_NAME:
                spoken_by_self += said_texts(line)
        elif line.kind == "今の様子":
            if line.by not in delegates and roles[line.by] != "専門職":
                problem(number, f"{line.by}は今の様子を入れられません（託された家族か専門職だけです）")
        elif line.kind in ("確かめ", "訂正"):
            if line.by != SELF_NAME:
                problem(number, f"{line.kind}は本人だけです")
            if not line.guess and not any(line.match in text for text in spoken_by_self):
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
        elif line.kind == "段階" and line.by != SELF_NAME:
            problem(number, "段階の行は by を本人にします")
        elif line.kind == "本人だけにする":
            if line.by != SELF_NAME:
                problem(number, "本人だけにするのは本人だけです")
            if open_day == line.day:
                problem(number, "本人だけにするは、閉じるのあとに書きます。記録は閉じたあとにできるため")
            if not any(line.text in text for text in spoken_by_self):
                problem(number, "text が本人の発言に見つかりません。text か must に書いた文にします")
        elif line.kind == "受け答え":
            if line.by != SELF_NAME:
                problem(number, "受け答えの行は by を本人にします")
            if line.letter:
                if line.letter not in letters:
                    problem(number, f"便り「{line.letter}」は、まだ出ていません")
            elif line.room != TWIN_ROOM:
                problem(number, "受け答えは、ツインとの部屋か便りにだけ足せます")
            elif open_day != line.day:
                problem(number, "受け答えは、会話が開いている間に足します")
        elif line.kind == "推測":
            if line.by != SELF_NAME:
                problem(number, "推測の行は by を本人にします")
            if not _is_hedged(line.text):
                problem(number, "推測の文は、すべての文を言い切らない形で終わらせます")
            if line.theme and line.theme not in set(Theme):
                problem(number, f"テーマ「{line.theme}」は決まりにありません")
            if not any(line.match in text for text in spoken_by_self):
                findings.append(Finding(number, "match が本人の文に見つかりません。流すときに止まることがあります", problem=False))
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


def spaced_times(lines: list[Line]) -> list[datetime]:
    """行ごとに流す時刻を決める。at のとおりにし、同じ時刻の行は1秒ずつずらす。

    発言の時刻は要求が届いた瞬間で決まる。同じ at を毎行送ると、処理の揺れで前後が入れ替わることがある。
    台帳と発言は時刻の順に読むので、順番を時刻に残す。行の中身だけで決まるので、続きから流しても同じ時刻になる。
    関連するテスト：tests/test_run_script.py の test_同じ時刻の行は1秒ずつずらして_順番を時刻に残す
    """
    stamps: list[datetime] = []
    previous: datetime | None = None
    for line in lines:
        wanted = line.at if previous is None else max(line.at, previous + LINE_SPACING)
        stamp = min(wanted, datetime.combine(line.day, _DAY_END, tzinfo=line.at.tzinfo))
        stamps.append(stamp)
        previous = stamp
    return stamps


@dataclass
class Progress:
    """進み具合。止まっても落ちても続きから流せるよう、ファイルに残す。

    全部を書く save はまとめて行う。送り始めた行の番号だけは note_sending で毎行、別のファイルに足す。
    読み直すと、番号が sent_upto に入り、そこまでの行は「すでに送ったかもしれない行」として流し直す。
    """

    path: Path
    digests: list[str] = field(default_factory=list)
    rooms: dict[str, int] = field(default_factory=dict)
    room_seq: dict[str, int] = field(default_factory=dict)
    letters: dict[str, dict] = field(default_factory=dict)
    photos: dict[str, int] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    closed: dict | None = None
    sending: int | None = None
    sent_upto: int = 0
    answer_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    story: dict[str, dict] = field(default_factory=dict)
    extra: dict[str, list] = field(default_factory=dict)
    # ツインを入れたことのある部屋。それ以外の部屋の発言は、読み直さなくても中身が分かる
    twin_rooms: list[str] = field(default_factory=list)
    # 合う記録が見つからずに飛ばした確かめの行の数。--keep-going のときだけ増える
    skipped_confirms: int = 0

    @classmethod
    def load(cls, path: Path) -> Progress:
        progress = cls(path, **json.loads(path.read_text(encoding="utf-8"))) if path.exists() else cls(path)
        # sending も見る。番号のファイルがない前の版で止まった進み具合も、同じように続きから流せるようにするため
        progress.sent_upto = max(progress.sent_upto, progress.sending or 0, _last_number(progress._sending_file()))
        return progress

    def _sending_file(self) -> Path:
        return self.path.with_suffix(".sending")

    def note_sending(self, number: int) -> None:
        """送り始める行の番号を残す。追記なので、進み具合が大きくなっても遅くならない。"""
        self.sending = number
        self.sent_upto = max(self.sent_upto, number)
        with self._sending_file().open("a", encoding="utf-8") as file:
            file.write(f"{number}\n")

    def save(self) -> None:
        data = {key: value for key, value in self.__dict__.items() if key != "path"}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)


def _last_number(path: Path) -> int:
    """番号のファイルの最後の番号を返す。途中で落ちて最後の行が欠けていても読めるようにする。"""
    if not path.exists():
        return 0
    numbers = [int(raw) for raw in path.read_text(encoding="utf-8").split() if raw.isdigit()]
    return max(numbers, default=0)


# あとから足した項目。値がないときは印に入れない
_NEW_FIELDS = ("stage", "ask", "hint", "limit", "twin_replies", "guess")


def line_digest(line: Line) -> str:
    """行の印。項目を足しても、これまでの行の印が変わらないようにする。すでに流した分を続きから流すため。"""
    unset = {name for name in _NEW_FIELDS if getattr(line, name) is None}
    return hashlib.sha256(line.model_dump_json(exclude=unset).encode("utf-8")).hexdigest()[:16]


# ---- 裏の処理の受け手 ----


class _Timer:
    """時刻と待ちの道具。テストでは差し替えて、本当には待たずに確かめる。"""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, stop: threading.Event, seconds: float) -> None:
        stop.wait(seconds)


@dataclass
class JobStats:
    """同時の数を決める材料。jobs_stats.json に書く。本文は入れない。続きから流しても足していく。"""

    workers_limit: int = 0
    workers_now: int = 0
    workers_lowered: int = 0
    queue_max: int = 0
    queue_wait_seconds: float = 0.0
    barrier_wait_seconds: float = 0.0
    letter_wait_seconds: float = 0.0
    rate_limited: int = 0
    other_transient: int = 0
    waited_seconds: float = 0.0
    write_waits: int = 0
    write_wait_seconds: float = 0.0
    lock_failures: int = 0
    job_seconds: float = 0.0
    jobs_done: int = 0

    @classmethod
    def load(cls, path: Path | None) -> JobStats:
        if path is None or not path.exists():
            return cls()
        try:
            known = cls.__dataclass_fields__
            return cls(**{key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items() if key in known})
        except (OSError, ValueError, TypeError):
            return cls()


@dataclass
class _Job:
    key: str
    at: datetime
    work: Callable[[], None]


def _is_lock_failure(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(word in str(error).lower() for word in ("locked", "busy"))


def read_api_failure(error: BaseException) -> tuple[int | None, float | None]:
    """AI の呼び出しの失敗から、番号と retry-after の秒数を読む。

    AIUnavailableError は元の例外を __cause__ に持つ。SDK を読み込まず getattr で読むので、
    応答のない失敗（接続の失敗など）でも動く。読めないときは None を返す。
    retry-after が日時の形のときは読まず、決まった待ち方にする。
    """
    cause = getattr(error, "__cause__", None)
    response = getattr(cause, "response", None)
    status = getattr(cause, "status_code", None)
    if status is None:
        status = getattr(response, "status_code", None)
    reader = getattr(getattr(response, "headers", None), "get", None)
    raw = reader("retry-after") if callable(reader) else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return status, None
    return status, seconds if math.isfinite(seconds) and seconds >= 0 else None


class JobRunner:
    """裏の処理を少ない本数で流す。app.state.job_runner に入れている間だけ、app/jobs.py から渡される。

    流す間だけ動く道具なので、サーバーには置かない。app/jobs.py はここの決まりを知らない。
    関連するテスト：tests/test_run_script_jobs.py
    """

    def __init__(self, workers: int, *, stats_path: Path | None = None, timer: _Timer | None = None) -> None:
        self.limit = workers
        self.current = min(FIRST_WORKERS, workers)
        self.timer = timer or _Timer()
        self.stop_reason: str | None = None
        self.stats = JobStats.load(stats_path)
        self.stats.workers_limit = workers
        self.stats.workers_now = self.current
        self._stats_path = stats_path
        self._save_lock = threading.Lock()
        self._cond = threading.Condition()
        self._queue: deque[_Job] = deque()
        self._keys: Counter[str] = Counter()
        self._running = 0
        self._ended = threading.Event()
        started = self.timer.now()
        self._paused_until = started
        self._changed_at = started
        self._saved_at = started
        ledger.write_wait_watcher = self._note_write_wait
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"裏の処理{number}") for number in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    # ---- 本線から呼ぶ ----

    def submit(self, key: str, at: datetime, work: Callable[[], None]) -> bool:
        """処理を渡す。待ちがいっぱいなら本線が待つ。止める途中なら受け取らない（続きのときに印を見て拾う）。"""
        started = self.timer.now()
        with self._cond:
            self._raise_if_due()
            while not self._ended.is_set() and len(self._queue) + self._running >= self.current * QUEUE_PER_WORKER:
                self._cond.wait(1.0)
            self.stats.queue_wait_seconds += self.timer.now() - started
            if self._ended.is_set():
                return False
            self._queue.append(_Job(key, at, work))
            self._keys[key] += 1
            self.stats.queue_max = max(self.stats.queue_max, len(self._queue))
            self._cond.notify_all()
        return True

    def wait_for(self, key: str) -> None:
        """同じ名札の処理が終わるのを待つ。"""
        started = self.timer.now()
        with self._cond:
            while self._keys[key] > 0 and not self._ended.is_set():
                self._cond.wait(1.0)
            self.stats.letter_wait_seconds += self.timer.now() - started

    def drain(self) -> None:
        """待ちと動いている処理が全部終わるのを待つ。"""
        started = self.timer.now()
        with self._cond:
            while (self._queue or self._running) and not self._ended.is_set():
                self._cond.wait(1.0)
            self.stats.barrier_wait_seconds += self.timer.now() - started

    def speed_fields(self) -> dict:
        """速さの記録に書く項目を返す。"""
        with self._cond:
            return {
                "workers": self.current,
                "waiting": len(self._queue) + self._running,
                "letter_wait_seconds": round(self.stats.letter_wait_seconds, 1),
                "barrier_wait_seconds": round(self.stats.barrier_wait_seconds, 1),
                "rate_limited": self.stats.rate_limited,
            }

    def check(self) -> None:
        """裏の処理が止めると決めていれば、本線を止める。"""
        if self.stop_reason:
            raise StopRun(self.stop_reason)

    def close(self) -> None:
        """受け付けを終える。残っている待ちは捨てる。印が進まないので、続きのときに拾う。"""
        with self._cond:
            self._queue.clear()
            self._ended.set()
            self._cond.notify_all()
        for thread in self._threads:
            thread.join(timeout=_JOIN_SECONDS)
        ledger.write_wait_watcher = None
        self._save_stats()

    # ---- app/jobs.py から呼ぶ ----

    def wait_for_api(self) -> bool:
        """全体で待っている間はここで待つ。流してよいなら True、止める途中なら False を返す。"""
        while True:
            with self._cond:
                if self._ended.is_set():
                    return False
                left = self._paused_until - self.timer.now()
            if left <= 0:
                return True
            self.timer.sleep(self._ended, left)

    def retry_after_failure(self, job: str, target: str, error: BaseException, tries: int) -> bool:
        """失敗した処理をやり直すかを決める。

        やり直さず記録して続ける失敗（AI が断った、決めた型に合わないなど）と、やり直す一時の失敗と、
        待っても直らないので本線ごと止める失敗に分ける。
        """
        if _is_lock_failure(error):
            self.note_lock_failure()
            if tries > len(JOB_RETRY_SECONDS):
                self.stop(f"{job}（{target}）で、データベースのロックの失敗が続きました。同じ命令で続きから流せます。")
                return False
            self.timer.sleep(self._ended, LOCK_RETRY_SECONDS)
            return not self._ended.is_set()
        if isinstance(error, config.MissingApiKeyError):
            self.stop("API キーがありません。.env を確かめてください。同じ命令で続きから流せます。")
            return False
        if not isinstance(error, ai.AIUnavailableError):
            return False
        status, retry_after = read_api_failure(error)
        if status in _NEVER_RETRY:
            self.stop(f"{job}（{target}）で Claude API が {status} を返しました。待っても直らないので止めます。")
            return False
        if tries > len(JOB_RETRY_SECONDS):
            self.stop(f"{job}（{target}）の一時の失敗が続きました（{status or '接続'}）。同じ命令で続きから流せます。")
            return False
        if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
            self.stop(f"{job}（{target}）で {retry_after:.0f} 秒待つように言われました。同じ命令で、あとから続きを流せます。")
            return False
        delay = retry_after if retry_after is not None else JOB_RETRY_SECONDS[tries - 1]
        self._pause(delay, lowered=status in _RATE_LIMITED)
        return not self._ended.is_set()

    def stop(self, reason: str) -> None:
        """これ以上流さない。本線は次の check で止まる。理由に本文は入れない。"""
        with self._cond:
            if self.stop_reason is None:
                self.stop_reason = reason
            self._queue.clear()
            self._ended.set()
            self._cond.notify_all()
        self._save_stats()

    def note_lock_failure(self) -> None:
        with self._cond:
            self.stats.lock_failures += 1

    def pause_for_main(self, seconds: float) -> None:
        """本線の AI の一時の失敗で、裏の処理も待たせる。同じ API の上限に当たっている見込みが高いため。"""
        self._pause(seconds, lowered=False)

    # ---- 中の部品 ----

    def _pause(self, seconds: float, *, lowered: bool) -> None:
        """全体で待つ時刻を延ばす。本数の半分への引き下げもここで行う。"""
        with self._cond:
            now = self.timer.now()
            if lowered:
                self.stats.rate_limited += 1
                # 同時に返った分で何度も下げないよう、待っている間の2回目からは下げない
                if now >= self._paused_until and self.current > 1:
                    self.current = max(1, self.current // 2)
                    self.stats.workers_lowered += 1
            else:
                self.stats.other_transient += 1
            until = now + seconds
            if until > self._paused_until:
                self.stats.waited_seconds += until - max(now, self._paused_until)
                self._paused_until = until
            self._changed_at = self._paused_until
        self._save_stats()

    def _raise_if_due(self) -> None:
        """問題がなければ1本上げる。_cond を持って呼ぶ。"""
        now = self.timer.now()
        if self.current < self.limit and now - self._changed_at >= RAMP_SECONDS:
            self.current += 1
            self._changed_at = now
            self._cond.notify_all()

    def _note_write_wait(self, seconds: float) -> None:
        with self._cond:
            self.stats.write_wait_seconds += seconds
            if seconds >= SLOW_WRITE_SECONDS:
                self.stats.write_waits += 1

    def _worker(self) -> None:
        while True:
            with self._cond:
                while True:
                    if self._ended.is_set():
                        return
                    self._raise_if_due()
                    if self._queue and self._running < self.current:
                        break
                    self._cond.wait(1.0)
                job = self._queue.popleft()
                self._running += 1
            self._run(job)

    def _run(self, job: _Job) -> None:
        started = self.timer.now()
        try:
            # 日付が進んだあとに動いても、渡されたときの時刻で記録する
            with clock.frozen(job.at):
                job.work()
        except Exception as error:  # noqa: BLE001  失敗は _guarded が受けるので、ここへ来るのは組み立ての誤りだけ
            self.stop(f"裏の処理で思わぬ失敗がありました（{devlog.error_name(error)}）。")
        finally:
            with self._cond:
                self._running -= 1
                self._keys[job.key] -= 1
                if self._keys[job.key] <= 0:
                    del self._keys[job.key]
                now = self.timer.now()
                self.stats.job_seconds += now - started
                self.stats.jobs_done += 1
                due = now - self._saved_at >= STATS_EVERY_SECONDS
                self._cond.notify_all()
            if due:
                self._save_stats()

    def _save_stats(self) -> None:
        """数字を書く。書けなくても流すのは止めない。読む人のために小数は丸める。"""
        with self._cond:
            self.stats.workers_now = self.current
            self._saved_at = self.timer.now()
            values = {key: round(value, 3) if isinstance(value, float) else value for key, value in asdict(self.stats).items()}
        if self._stats_path is None:
            return
        temporary = self._stats_path.with_suffix(".tmp")
        with self._save_lock:
            try:
                temporary.write_text(json.dumps(values, ensure_ascii=False, indent=0), encoding="utf-8")
                temporary.replace(self._stats_path)
            except OSError:
                pass


@contextmanager
def running_jobs(app, workers: int, folder: Path, timer: _Timer | None = None) -> Iterator[JobRunner | None]:
    """流している間だけ受け手を入れる。workers が 0 なら入れず、今までの順番で流す。"""
    if workers <= 0:
        yield None
        return
    runner = JobRunner(workers, stats_path=folder / STATS_FILE, timer=timer)
    app.state.job_runner = runner
    try:
        yield runner
    finally:
        app.state.job_runner = None
        runner.close()


class Runner:
    """1行ずつ API へ送る。client は画面と同じ道だけを呼ぶ。"""

    def __init__(self, client, progress: Progress, *, keep_going: bool = False, job_runner: JobRunner | None = None) -> None:
        self.client = client
        self.progress = progress
        self.keep_going = keep_going
        self.job_runner = job_runner
        self.ids: dict[str, int] = {}
        # 問いが出るかもしれないコメントのあとで、まだ処理を待っていない便り
        self.asking_letters: set[str] = set()
        # 今の行が、前回すでに送られているかもしれないとき True
        self.resending = False
        self.stamp: datetime | None = None

    # ---- 共通 ----

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """データベースを直接開く。探し物と、画面に道がない行にだけ使う。"""
        conn = db.connect(self.client.app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

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
        """送る。AI の一時の失敗（503）とロックの失敗は、少し待って送り直す。

        503 の番号の中身（429 かどうか）は応答から分からないので、本数は下げず、全体で待つだけにする。
        """
        for wait in (*RETRY_SECONDS, None):
            try:
                response = self._call(method, path, user=user, json_body=json_body)
            except sqlite3.OperationalError as error:
                if not _is_lock_failure(error):
                    raise
                if self.job_runner is not None:
                    self.job_runner.note_lock_failure()
                if wait is None:
                    raise StopRun(f"{what} が、データベースのロックで失敗しました。同じ命令で続きから流せます。") from error
                time.sleep(wait)
            else:
                if response.status_code != 503 or wait is None:
                    return self._ok(response, what)
                if self.job_runner is None:
                    time.sleep(wait)
                else:
                    self.job_runner.pause_for_main(wait)
                    self.job_runner.wait_for_api()
            # 待った分だけ時計が進むので、次の行より後の時刻にならないよう合わせ直す
            if self.stamp is not None:
                self._set_day(self.stamp)
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

    def _set_day(self, at: datetime) -> None:
        """日付と時刻を動かす。発言の時刻が at のとおりになるよう、行ごとに動かす。

        全部の行で動かすので、HTTP を通さず /api/dev/date と同じ計算をする。架空用の起動かどうかは check_mode で
        確かめてあり、自分用の起動には流さない。
        """
        app = self.client.app
        target = at if at.tzinfo else at.astimezone()
        app.state.day_offset = target - datetime.now().astimezone()
        today = clock.now(app).date()
        if today != at.date():
            raise StopRun(f"日付を {at.date()} にできませんでした（{today}）")

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

    def _not_found(self, message: str) -> None:
        """記録が見つからないとき。--keep-going なら書き残して続ける。"""
        if not self.keep_going:
            raise StopRun(message)
        self.progress.warnings.append(message)

    # ---- 本人の答え ----

    def _ask_for_answer(self, number: int, content: str):
        """本人の答えの案を AI に作らせる。一時の失敗は、裏の処理と同じ決まりで待ってやり直す。"""
        tries = 0
        while True:
            try:
                return ai.call_structured("架空ログの作成", system=ANSWER_SYSTEM, content=content, schema=AnswerDraft)
            except config.MissingApiKeyError as error:
                raise StopRun("API キーがありません。.env を確かめてください。同じ命令で続きから流せます。") from error
            except ai.AIUnavailableError as error:
                tries += 1
                if self.job_runner is not None:
                    if self.job_runner.retry_after_failure("本人の答え", f"{number}行目", error, tries):
                        self.job_runner.wait_for_api()
                        continue
                elif tries <= len(RETRY_SECONDS):
                    time.sleep(RETRY_SECONDS[tries - 1])
                    continue
                raise StopRun(f"{number}行目：本人の答えを作れませんでした。同じ命令で続きから流せます。") from error

    def _answer_text(self, number: int, line: Line, room_id: int, client_key: str) -> str:
        """ツインの実際の言葉を見て、本人の答えを作る。送り直すときは同じ文になるよう覚えておく。"""
        key = str(number)
        if key in self.progress.answers:
            return self.progress.answers[key]
        after = max(self.progress.room_seq.get(line.room, 0) - 8, 0)
        view = self._ok(self._call("GET", f"/api/rooms/{room_id}/messages?after={after}", user=line.by), "部屋の発言")
        if self.resending:
            # 送ったあとで落ちた行は、部屋に入っている文をそのまま使う。作り直すと費用だけがかかるため
            sent = next((item["text"] for item in view["items"] if item.get("client_key") == client_key and item["text"]), None)
            if sent is not None:
                return sent
        names = {person["id"]: person["name"] for person in view["people"]}
        recent = [
            f"{'ツイン' if item['is_twin'] else names.get(item['sender_user_id'], line.by)}：{item['text']}"
            for item in view["items"]
            if item.get("text")
        ]
        feedback = None
        text = None
        for _ in range(2):
            try:
                result = self._ask_for_answer(number, answer_prompt(line, recent, feedback))
            except (ai.AIResultError, ai.AIRefusedError) as error:
                # 答えは得られなくても費用はかかる。この呼び出しは表に残らないので、ここで足す
                self.progress.answer_usd += config.cost_usd(error.model, error.input_tokens, error.output_tokens)
                feedback = str(error)
                continue
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
        return text

    # ---- 行ごとの処理 ----

    def run_line(self, number: int, line: Line, script_key: str, at: datetime | None = None) -> None:
        # 時計を合わせる前に待つ。合わせたあとで待つと、その分だけ発言の時刻が進み、次の行より後になるため
        self._wait_for_jobs(line)
        self.stamp = at or line.at
        self._set_day(self.stamp)
        # 話し手の名前を確かめる。/api/login は確かめるだけで何も変えず、各要求は話し手の番号を付けて送るので呼ばない
        self._user_id(line.by)
        self.client.app.state.twin_may_ask = line.ask is not False
        handler = getattr(self, f"_do_{_HANDLERS[line.kind]}")
        handler(number, line, script_key)

    def _wait_for_jobs(self, line: Line) -> None:
        if self.job_runner is None:
            return
        self.job_runner.wait_for_api()
        if line.kind in _WAIT_FOR_ALL:
            self.job_runner.drain()
            self.asking_letters.clear()
        elif line.letter in self.progress.letters and self._must_wait_for_letter(line):
            self.job_runner.wait_for(jobs.thread_key(self.progress.letters[line.letter]["id"]))
            self.asking_letters.discard(line.letter)
        self.job_runner.check()

    def _must_wait_for_letter(self, line: Line) -> bool:
        """この行の前に、同じ便りの裏の処理を待つかを返す。

        問わないコメントのあとは待たない。待ちの中身は言明への分割で、1回10秒ほどかかるため。
        分割は便りごとの鍵で順に動き、印のあとを読むので、次のコメントを先に入れても欠けず、重ならない。
        言明の日付と記録の時刻は発言の時刻から決まるので、まとめて分けられてもずれない。
        - 問いが出るかもしれないコメントのあとは待つ。ツインの問いが次のコメントの後ろに入らないようにするため。
        - 受け答えの前は待つ。足した文は分割の印を進めるので、前の分割が終わっていないと、人のコメントを飛ばすため。
        関連するテスト：tests/test_run_script_jobs.py の test_問わないコメントのあとは_同じ便りの処理を待たない
        """
        return line.kind == "受け答え" or line.letter in self.asking_letters

    def _conversation(self, day: date, line: Line | None = None) -> dict:
        entry = self.progress.story.setdefault(
            day.isoformat(), {"trigger": "", "turns": [], "confirmation": "合っています", "letter": None, "comments": []}
        )
        if line is not None and line.topic and not entry["trigger"]:
            entry["trigger"] = line.topic
        return entry

    def _do_say(self, number: int, line: Line, script_key: str) -> None:
        room_id = self._room_id(line.room, line.by)
        client_key = f"script-{script_key}-{number}"
        text = self._answer_text(number, line, room_id, client_key) if line.answer else line.text
        body = {"text": text, "client_key": client_key}
        if line.photo:
            kind = "会話" if line.room == TWIN_ROOM else "部屋"
            body["photo_id"] = self._upload(line.photo, kind, line.by)
        if line.answer:
            # 答えを作る間に時計が進むので、次の行より後の時刻にならないよう合わせ直す
            self._set_day(self.stamp)
        sent = self._send(f"/api/rooms/{room_id}/messages", user=line.by, json_body=body, what=f"{number}行目の発言")
        self.progress.answers.pop(str(number), None)
        if line.room == TWIN_ROOM or line.room in self.progress.twin_rooms or self.resending:
            self._record_messages(line, room_id)
        else:
            self._record_people_message(line, sent)

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

    def _record_people_message(self, line: Line, sent: dict) -> None:
        """人だけの部屋の発言を記録する。ツインも裏の処理も発言を足さないので、応答の中身だけで足りる。"""
        message = sent["message"]
        self.progress.room_seq[line.room] = message["seq"]
        if message.get("text"):
            self._extra(line, {"kind": "部屋の発言", "room": line.room, "speaker": line.by, "text": message["text"]})

    def _closed_conversation(self, room_id: int, day: date) -> int | None:
        """その日の会話がもう閉じてあれば番号を返す。送り直しで、次の日の会話を閉じないようにするため。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT id, closed_at FROM conversations WHERE room_id = ? AND conversation_date = ? ORDER BY rowid DESC LIMIT 1",
                (room_id, day.isoformat()),
            ).fetchone()
        return row["id"] if row and row["closed_at"] else None

    def _do_close(self, number: int, line: Line, _key: str) -> None:
        room_id = self._room_id(line.room, line.by)
        closed_id = self._closed_conversation(room_id, line.day) if self.resending else None
        if closed_id is None:
            # 応答が返るまでに、要約は終わっている。言明への分割は受け手があれば裏で流れる
            result = self._send(f"/api/rooms/{room_id}/close", user=line.by, json_body=None, what=f"{number}行目の閉じる")
            if not result.get("closed"):
                raise StopRun(f"{number}行目：閉じる会話がありませんでした")
            closed_id = result["conversation_id"]
        self.progress.closed = {"day": line.day.isoformat(), "id": closed_id}
        self._record_messages(line, room_id)

    def _do_letter(self, number: int, line: Line, _key: str) -> None:
        body = {"body": line.body, "share_with": line.share_with}
        # 閉じる前に出す便りは、まだ閉じた会話がないので指さない（API は省略を受け付ける）
        closed = self.progress.closed
        if closed and closed["day"] == line.day.isoformat():
            body["conversation_id"] = closed["id"]
        if line.photo:
            body["photo_id"] = self._upload(line.photo, "便り", line.by)
        # 便りには送り直しの印がないので、止まった行は同じ本文がもうあるかを先に見る
        letter_id = self._already_sent_letter(line)
        if letter_id is None:
            letter_id = self._send("/api/letters", user=line.by, json_body=body, what=f"{number}行目の便り")["id"]
        self.progress.letters[line.ref] = {"id": letter_id, "day": line.day.isoformat()}
        self._conversation(line.day)["letter"] = line.body

    def _already_sent_letter(self, line: Line) -> int | None:
        if not self.resending:
            return None
        items = self._ok(self._call("GET", "/api/letters", user=line.by), "便りの一覧")["items"]
        day = line.day.isoformat()
        return next((item["id"] for item in items if item["body"] == line.body and str(item["shared_at"]).startswith(day)), None)

    def _do_comment(self, number: int, line: Line, script_key: str) -> None:
        letter = self.progress.letters[line.letter]
        body = {"text": line.text, "client_key": f"script-{script_key}-{number}"}
        self._send(f"/api/letters/{letter['id']}/comments", user=line.by, json_body=body, what=f"{number}行目のコメント")
        if line.ask is not False:
            self.asking_letters.add(line.letter)
        if line.by in _LOG_COMMENTERS:
            self._conversation(date.fromisoformat(letter["day"]))["comments"].append({"speaker": line.by, "text": line.text})
        else:
            self._extra(line, {"kind": "コメント", "letter": line.letter, "text": line.text})

    # ---- 本人の短い受け答え ----

    def _ask_added(self, number: int, role: str, system: str, content: str, schema):
        """受け答えの AI を呼ぶ。一時の失敗は、本人の答えと同じ決まりで待ってやり直す。"""
        tries = 0
        while True:
            try:
                result = ai.call_structured(role, system=system, content=content, schema=schema)
            except config.MissingApiKeyError as error:
                raise StopRun("API キーがありません。.env を確かめてください。同じ命令で続きから流せます。") from error
            except ai.AIUnavailableError as error:
                tries += 1
                if self.job_runner is not None:
                    if self.job_runner.retry_after_failure("受け答え", f"{number}行目", error, tries):
                        self.job_runner.wait_for_api()
                        continue
                elif tries <= len(RETRY_SECONDS):
                    time.sleep(RETRY_SECONDS[tries - 1])
                    continue
                raise StopRun(f"{number}行目：受け答えを作れませんでした。同じ命令で続きから流せます。") from error
            self.progress.answer_usd += _usd(result)
            return result.value

    def _added_text(self, number: int, line: Line, question: str, recent: list[str]) -> dict:
        """ツインの問いへの短い受け答えを作り、確かめる。通らなければ決まった短い言葉にする。"""
        key = str(number)
        if key in self.progress.answers:
            return json.loads(self.progress.answers[key])
        limit = line.limit or added_words.LIMIT_FIRST
        feedback = None
        recent_added = self._recent_added()
        record = {"text": added_words.fallback_for(limit, recent_added), "fallback": True, "tries": 0, "reasons": []}
        for tries in range(1, added_words.MAX_TRIES + 1):
            record["tries"] = tries
            try:
                draft = self._ask_added(
                    number,
                    added_words.ROLE_WRITE,
                    added_words.WRITE_SYSTEM,
                    added_words.write_prompt(
                        day=line.day.isoformat(), hint=line.hint, recent=recent, question=question, limit=limit, feedback=feedback, recent_added=recent_added
                    ),
                    added_words.AddedLine,
                )
                text = draft.text.strip()
                feedback = added_words.added_problem(
                    text, hint=line.hint, limit=limit, forbidden=forbidden_in, day=line.day.isoformat(), recent=recent_added
                )
                if feedback is None:
                    check = self._ask_added(
                        number,
                        added_words.ROLE_CHECK,
                        added_words.CHECK_SYSTEM,
                        added_words.check_prompt(hint=line.hint, question=question, text=text),
                        added_words.AddedCheck,
                    )
                    feedback = added_words.check_problem(check)
            except (ai.AIResultError, ai.AIRefusedError) as error:
                self.progress.answer_usd += config.cost_usd(error.model, error.input_tokens, error.output_tokens)
                feedback = str(error)
            if feedback is None:
                record.update(text=text, fallback=False)
                break
            record["reasons"].append(feedback)
        self.progress.answers[key] = json.dumps(record, ensure_ascii=False)
        return record

    def _recent_added(self) -> tuple[str, ...]:
        """最近足した文を返す。同じ言葉が続かないようにするため。"""
        items = [item for year in self.progress.extra.values() for item in year if item.get("kind") == "足した文"]
        return tuple(item["text"] for item in items[-8:])

    def _do_added(self, number: int, line: Line, script_key: str) -> None:
        """ツインが問うていたら、本人の短い受け答えを足す。問うていなければ何もしない。

        足した文は本人が話した言葉ではないので、記録にしない。ツインとの部屋では部屋の行にだけ入れる。
        発言の表に入らないので、言明にならず、要約にも渡らない。ツインの次の返事にだけ渡す（memory.conversation_lines）。
        関連するテスト：tests/test_added_words.py
        """
        if line.letter:
            self._added_in_thread(number, line, script_key)
            return
        room_id = self._room_id(line.room, line.by)
        client_key = f"script-{script_key}-{number}"
        with self._db() as conn:
            self_user = db.self_user(conn)
            conversation = room_store.open_conversation(conn, room_id)
            message = room_store.find_by_client_key(conn, room_id, self_user.id, client_key) if self.resending else None
            question = None if message else self._pending_in_room(conn, conversation.id)
            if message is None and question is None:
                return
            recent = memory.conversation_lines(conn, conversation.id, self_user, max_lines=8, with_added=True)
        if message is None:
            record = self._added_text(number, line, question, recent)
            self._set_day(self.stamp)
            app = self.client.app
            with self._db() as conn:
                message = room_store.insert_message(
                    conn,
                    room_id=room_id,
                    sent_at=clock.now(app).isoformat(),
                    sender_user_id=self_user.id,
                    text=record["text"],
                    conversation_id=conversation.id,
                    client_key=client_key,
                )
                with conn:
                    twin._answer_pending(conn, conversation.id, QuestionResult.ANSWERED)
            self._extra(line, {"kind": "足した文", "where": "ツインとの部屋", "question": question, **record})
            self.progress.answers.pop(str(number), None)
        # 足した文は会話のログに書かない。読み直すと本人の発言になるため
        self._new_messages(line.room, room_id, line.by)
        if line.twin_replies:
            self._twin_reply_to_added(number, line, room_id, message)

    def _pending_in_room(self, conn: sqlite3.Connection, conversation_id: int) -> str | None:
        """ツインの最後の行が答えを待っている問いなら、その文を返す。"""
        row = conn.execute(
            """
            SELECT q.question, q.result FROM room_messages m
            LEFT JOIN question_logs q ON q.id = m.question_log_id
            WHERE m.conversation_id = ? ORDER BY m.rowid DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        return row["question"] if row and row["question"] and row["result"] is None else None

    def _twin_reply_to_added(self, number: int, line: Line, room_id: int, message) -> None:
        """足した文にツインが受け止めて返す。問いは出さない。答えのない問いを残さないため。"""
        app = self.client.app
        for wait in (*RETRY_SECONDS, None):
            try:
                with self._db() as conn:
                    twin.respond_in_twin_room(
                        conn,
                        room=room_store.get_room(conn, room_id),
                        self_user=db.self_user(conn),
                        message=message,
                        now=clock.now(app),
                        photos_dir=app.state.deployment.photos_dir,
                        ask=False,
                    )
                break
            except twin._ALL_AI_ERRORS as error:
                if wait is None:
                    self.progress.warnings.append(f"{number}行目：足した文へのツインの返事を作れませんでした（{type(error).__name__}）")
                    return
                time.sleep(wait)
        self._record_messages(line, room_id)

    def _added_in_thread(self, number: int, line: Line, script_key: str) -> None:
        """便りでツインが問うていたら、本人の短い受け答えをコメントに足す。

        コメントは発言の表に入る作りなので、分割の印を足した文の後ろへ進めて、言明にならないようにする。
        """
        letter_id = self.progress.letters[line.letter]["id"]
        with self._db() as conn:
            row = conn.execute(
                "SELECT question FROM question_logs WHERE letter_id = ? AND result IS NULL AND asked_on IS NOT NULL ORDER BY id DESC LIMIT 1",
                (letter_id,),
            ).fetchone()
            if row is None:
                return
            question = row["question"]
            body, lines = memory.thread_lines(conn, letter_id)
        record = self._added_text(number, line, question, [f"便り：{body}", *lines[-6:]])
        self._set_day(self.stamp)
        app = self.client.app
        with self._db() as conn:
            self_user = db.self_user(conn)
            comment = ledger.add_comment(
                conn,
                CommentDraft(
                    letter_id=letter_id,
                    author_user_id=self_user.id,
                    body=record["text"],
                    commented_at=clock.now(app),
                    client_key=f"script-{script_key}-{number}",
                ),
            )
            twin.thread_turn(conn, letter_id=letter_id, author_user_id=self_user.id, now=clock.now(app))
            rowid = conn.execute("SELECT rowid FROM utterances WHERE id = ?", (comment.utterance_id,)).fetchone()[0]
            with conn:
                splitter.skip_up_to(conn, SplitTargetKind.LETTER, letter_id, rowid)
        self._extra(line, {"kind": "足した文", "where": "便り", "letter": line.letter, "question": question, **record})
        self.progress.answers.pop(str(number), None)

    def _do_observation(self, number: int, line: Line, _key: str) -> None:
        day = line.day.isoformat()
        sent = self.resending and any(item["effective_date"] == day for item in self._find_statements(line.text))
        if not sent:
            body = {"text": line.text}
            if line.theme:
                body["theme"] = line.theme
            if line.at_meeting:
                body["at_meeting"] = True
            self._send("/api/observations", user=line.by, json_body=body, what=f"{number}行目の今の様子")
        self._extra(line, {"kind": "今の様子", "text": line.text})

    def _find_statements(self, match: str) -> list[dict]:
        """本文に match を含む、いま有効な記録を新しい順に返す。本人にはすべての範囲が見えるので、本人の見え方と同じになる。"""
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT id, body, confirm_status, effective_date, created_at FROM statements
                WHERE instr(body, ?) > 0
                  AND id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
                """,
                (match,),
            ).fetchall()
        found = [dict(row) for row in rows]
        return sorted(found, key=lambda item: (item["effective_date"], item["created_at"]), reverse=True)

    def _target(self, number: int, line: Line) -> dict | None:
        found = self._find_statements(line.match)
        if found:
            # まだ確かめていない記録を先にする。同じ言葉を別の日に話したときに、確かめ済みの行を選ばないため
            return next((item for item in found if item["confirm_status"] == "未確認"), found[0])
        self._not_found(f"{number}行目：「{line.match}」を含む記録が見つかりません")
        return None

    def _do_confirm(self, number: int, line: Line, _key: str) -> None:
        target = self._target(number, line)
        if target is None:
            self.progress.skipped_confirms += 1
            return
        rest = line.reply == "今は話したくない"
        status = target["confirm_status"]
        # 答えてある記録には、同じ答えの行を足さない。「今は話したくない」は確かめたあとの記録にも答えられる
        answered = status == ConfirmStatus.RESTING.value if rest else status != ConfirmStatus.UNCONFIRMED.value
        if not answered:
            action = "rest" if rest else "confirm"
            self._send(f"/api/statements/{target['id']}/{action}", user=line.by, json_body=None, what=f"{number}行目の確かめ")
        self._extra(line, {"kind": "確かめ", "match": line.match, "reply": line.reply or "そうです"})

    def _do_correct(self, number: int, line: Line, _key: str) -> None:
        # 止まった行で、もう直してあれば送らない
        if not (self.resending and self._find_statements(line.text)):
            target = self._guess_target(number, line) if line.guess else self._target(number, line)
            if target is None:
                return
            self._send(f"/api/statements/{target['id']}/correct", user=line.by, json_body={"text": line.text}, what=f"{number}行目の訂正")
        self._conversation(line.day)["confirmation"] = "直した"
        self._extra(line, {"kind": "訂正", "match": line.match, "text": line.text})
        if line.guess:
            self._write_scene(number, line)

    def _guess_target(self, number: int, line: Line) -> dict:
        """訂正の相手の推測を選ぶ。match の話の推測がなければ、--keep-going でも止める。

        AI の推測は流すたびに変わる。本文の言葉だけで探すと、訂正の文とかみ合わない推測に当たり、場面が成り立たないため
        （実際に「本当は誰かに気にかけてほしい思いがあるかもしれません」に「庭が好きなのではなく」と訂正する形になった）。
        本人の言葉に同じ語があっても、推測でなければ相手にしない。
        関連するテスト：tests/test_correction_scene.py の test_推測が庭の話でなければ_訂正の前で止まる
        """
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT id, body, confirm_status, effective_date, created_at FROM statements
                WHERE source = ? AND id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
                """,
                (Source.AI_GUESS.value,),
            ).fetchall()
        guesses = sorted((dict(row) for row in rows), key=lambda item: (item["effective_date"], item["created_at"]), reverse=True)
        found = [item for item in guesses if speaks_of(item["body"], line.match)]
        if not found:
            heard = "、".join(f"「{item['body']}」" for item in guesses) or "なし"
            raise GuessNotAbout(f"{number}行目：「{line.match}」の話の AI の推測がありません。今の推測：{heard}")
        return next((item for item in found if item["confirm_status"] == ConfirmStatus.UNCONFIRMED.value), found[0])

    def _write_scene(self, number: int, line: Line) -> None:
        """訂正の場面を書き残す。推測の本文と、AI の推測か台本の文かは、訂正の行が指す元の行から読む。"""
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT guess.body, guess.effective_date FROM statements fixed JOIN statements guess ON guess.id = fixed.replaces_id
                WHERE fixed.source = ? AND fixed.body = ? ORDER BY fixed.rowid LIMIT 1
                """,
                (Source.SELF_CORRECTION.value, line.text),
            ).fetchone()
        written = {
            item["text"] for items in self.progress.extra.values() for item in items if item.get("kind") == "推測"
        }
        scene = {
            "line": number,
            "day": line.day.isoformat(),
            "guess_day": row["effective_date"],
            "guess": row["body"],
            "correction": line.text,
            "method": METHOD_SCRIPT if row["body"] in written else METHOD_AI,
        }
        (self.progress.path.parent / SCENE_FILE).write_text(json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8")

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
        if line.room not in self.progress.twin_rooms:
            self.progress.twin_rooms.append(line.room)

    def _do_consolidate(self, number: int, line: Line, _key: str) -> None:
        self._send("/api/model/consolidate", user=line.by, json_body=None, what=f"{number}行目の整理")

    def _do_stage(self, number: int, line: Line, _key: str) -> None:
        """人生の段階を切り替える。

        ledger.life_stage_on は、その日までの最後の会話から段階を読む。だから部屋のない会話を1行足す
        （app/handover.py の _confirm_conversation と同じ形）。部屋がないので裏の分割の対象にならず、
        発言がないので、これまでの記録の一覧（/api/archive）にも空の日は出ない。
        同じ日の同じ段階の行がすでにあれば足さない。続きから流しても重ならないようにするため。
        関連するテスト：tests/test_run_script.py の test_段階の行で人生の段階を切り替える
        """
        with self._db() as conn:
            found = conn.execute(
                "SELECT 1 FROM conversations WHERE room_id IS NULL AND trigger = ? AND conversation_date = ? AND life_stage = ?",
                (STAGE_TRIGGER, line.day.isoformat(), line.stage.value),
            ).fetchone()
            if found is None:
                ledger.add_conversation(
                    conn,
                    ConversationDraft(
                        conversation_date=line.day,
                        trigger=STAGE_TRIGGER,
                        life_stage=line.stage,
                        closed_at=clock.now(self.client.app),
                    ),
                )
        self._extra(line, {"kind": "段階", "stage": line.stage.value})

    def _private_targets(self, conn: sqlite3.Connection, number: int, line: Line) -> list[int]:
        """本人だけにする記録の番号を返す。同じ日のほかの発言の記録は入れない。

        まだ言明に分けていない発言なら止める。あとから分かれた記録が休まないまま残り、話さない自由を守れないため。
        """
        self_user = db.self_user(conn)
        last_day = conn.execute(
            """
            SELECT substr(spoken_at, 1, 10) AS day FROM utterances
            WHERE speaker_user_id = ? AND instr(text, ?) > 0 AND substr(spoken_at, 1, 10) <= ?
            ORDER BY spoken_at DESC, rowid DESC LIMIT 1
            """,
            (self_user.id, line.text, line.day.isoformat()),
        ).fetchone()
        if last_day is None:
            self._not_found(f"{number}行目：本人だけにする発言が見つかりません")
            return []
        rows = conn.execute(
            """
            SELECT u.rowid AS seen, u.id, u.text, u.conversation_id, u.letter_id, c.room_id FROM utterances u
            LEFT JOIN conversations c ON c.id = u.conversation_id
            WHERE u.speaker_user_id = ? AND instr(u.text, ?) > 0 AND substr(u.spoken_at, 1, 10) = ?
            """,
            (self_user.id, line.text, last_day["day"]),
        ).fetchall()
        # 発言全体が同じものがあれば、それだけにする。短い語が別の発言に含まれても、そちらは休ませないため
        exact = [row for row in rows if row["text"] == line.text]
        chosen = exact or rows
        for row in chosen:
            if row["letter_id"]:
                kind, target = SplitTargetKind.LETTER, row["letter_id"]
            elif row["room_id"]:
                kind, target = SplitTargetKind.CONVERSATION, row["conversation_id"]
            else:
                continue
            if splitter.read_mark(conn, kind, target) < row["seen"]:
                self._not_found(f"{number}行目：本人だけにする発言が、まだ言明に分かれていません。同じ命令で続きから流せます")
                return []
        marks = ", ".join("?" for _ in chosen)
        statements = conn.execute(
            f"""
            SELECT DISTINCT s.id FROM statements s, json_each(s.source_utterance_ids) j
            WHERE j.value IN ({marks}) AND s.source IN (?, ?) AND s.confirm_status <> ?
              AND s.id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
            """,
            (*(row["id"] for row in chosen), Source.SELF_WORDS.value, Source.AI_GUESS.value, ConfirmStatus.RESTING.value),
        ).fetchall()
        return [row["id"] for row in statements]

    def _do_private(self, number: int, line: Line, _key: str) -> None:
        """その発言からできた、本人が確かめられる記録を全部「今は話したくない」にする。

        休ませた行は探す先から外れるので、続きから流しても同じ記録に行を足さない。
        関連するテスト：tests/test_run_script.py の test_本人だけにするは_その発言の記録だけを休ませる
        """
        with self._db() as conn:
            targets = self._private_targets(conn, number, line)
        for statement_id in targets:
            self._send(f"/api/statements/{statement_id}/rest", user=line.by, json_body=None, what=f"{number}行目の本人だけにする")
        self._extra(line, {"kind": "本人だけにする", "text": line.text})

    def _do_guess(self, number: int, line: Line, _key: str) -> None:
        """本人の発言を根拠に、出どころ「AIの推測」の言明を足す。台本の文なので AI を呼ばない。

        入れ方は eval/previous_story/load.py の _add_demo_guess と同じで、ledger の決まりを通す。範囲は本人だけ、状態は未確認で入る。
        同じ発言を根拠にした同じ文があれば足さない。続きから流しても重ならないようにするため。
        関連するテスト：tests/test_run_script.py の test_推測の行は_根拠の発言を指すAIの推測として入り_重ならない
        """
        with self._db() as conn:
            self_user = db.self_user(conn)
            row = conn.execute(
                """
                SELECT * FROM utterances
                WHERE speaker_user_id = ? AND instr(text, ?) > 0 AND substr(spoken_at, 1, 10) <= ?
                ORDER BY spoken_at DESC, rowid DESC LIMIT 1
                """,
                (self_user.id, line.match, line.day.isoformat()),
            ).fetchone()
            if row is None:
                self._not_found(f"{number}行目：「{line.match}」を含む本人の発言が見つかりません")
                return
            spoken = db.row_to_model(row, Utterance)
            exists = conn.execute(
                """
                SELECT 1 FROM statements s, json_each(s.source_utterance_ids) j
                WHERE s.source = ? AND s.body = ? AND j.value = ? LIMIT 1
                """,
                (Source.AI_GUESS.value, line.text, spoken.id),
            ).fetchone()
            if exists is None:
                ledger.append_statement(
                    conn,
                    StatementDraft(
                        source_utterance_ids=[spoken.id],
                        subject_user_id=self_user.id,
                        speaker_is_twin=True,
                        source=Source.AI_GUESS,
                        body=line.text,
                        period=Period.NOW,
                        theme=self._guess_theme(conn, line, spoken.id),
                        share_scope=ShareScope.SELF_ONLY,
                        effective_date=line.day,
                        created_at=clock.now(self.client.app),
                    ),
                )
        self._extra(line, {"kind": "推測", "match": line.match, "text": line.text})

    def _guess_theme(self, conn: sqlite3.Connection, line: Line, utterance_id: int) -> Theme:
        """推測のテーマ。書いてなければ、同じ発言の記録に合わせる。託すテーマで範囲が別々に動かないようにするため。"""
        if line.theme:
            return Theme(line.theme)
        row = conn.execute(
            "SELECT s.theme FROM statements s, json_each(s.source_utterance_ids) j WHERE j.value = ? ORDER BY s.rowid LIMIT 1",
            (utterance_id,),
        ).fetchone()
        return Theme(row["theme"]) if row else Theme.VALUES


_HANDLERS = {
    "発言": "say",
    "受け答え": "added",
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
    "段階": "stage",
    "本人だけにする": "private",
    "推測": "guess",
}


def spent_usd(db_path: Path, progress: Progress) -> float:
    """このデータベースで使った費用。本人の答えを作った分は、記録に残らないので足す。"""
    return sum(item["cost_usd"] for item in summarize(db_path)) + progress.answer_usd


def write_story(progress: Progress, out_dir: Path) -> list[Path]:
    """前の物語のログと同じ形で書き出す。"""
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


def _memory_mb() -> float | None:
    """このプロセスの使っているメモリを返す。読めない環境では None。道具を足さないため ctypes で読む。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        current_process = ctypes.WinDLL("kernel32").GetCurrentProcess
        current_process.restype = wintypes.HANDLE
        read = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not read(current_process(), ctypes.byref(counters), counters.cb):
            return None
        return round(counters.WorkingSetSize / 1_000_000, 1)
    except (OSError, AttributeError):
        return None


class SpeedLog:
    """決まった行数ごとに速さと費用を書く。データが増えると遅くなるかを、流している間に見るため。本文は入れない。

    書けなくても流すのは止めない。関連するテスト：tests/test_run_script_jobs.py の test_100行ごとに速さと費用を残す
    """

    def __init__(self, folder: Path, db_path: Path, progress: Progress, job_runner: JobRunner | None, every: int = SPEED_EVERY_LINES) -> None:
        self.path = folder / SPEED_FILE
        self.db_path = db_path
        self.progress = progress
        self.job_runner = job_runner
        self.every = every
        self.count = 0
        self.started = time.monotonic()

    def line_done(self, number: int, line: Line) -> None:
        self.count += 1
        if self.count < self.every:
            return
        now = time.monotonic()
        files = (path for path in self.db_path.parent.glob(f"{self.db_path.name}*") if path.is_file())
        row = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "line": number,
            "day": line.day.isoformat(),
            "ms_per_line": round((now - self.started) / self.count * 1000),
            "usd": round(spent_usd(self.db_path, self.progress), 3),
            "db_mb": round(sum(path.stat().st_size for path in files) / 1_000_000, 1),
            "memory_mb": _memory_mb(),
            "skipped_confirms": self.progress.skipped_confirms,
        }
        if self.job_runner is not None:
            row.update(self.job_runner.speed_fields())
        try:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self.count = 0
        self.started = now


def checkpoint_due(previous: date | None, day: date, marks: tuple[date, ...] = ()) -> str | None:
    """この行の前に取る控えの名前を返す。いらなければ None。

    年の変わり目に取る。marks はそれ以外に取りたい日で、その日かそれより後の最初の行の前に取る。
    """
    if previous is None:
        return None
    for mark in marks:
        if previous < mark <= day:
            return f"before-{mark.isoformat()}"
    return f"before-{day.year}" if day.year != previous.year else None


def save_checkpoint(name: str, progress: Progress, db_path: Path, job_runner: JobRunner | None, day: date) -> Path | None:
    """データベースと進み具合の控えを取る。

    裏の処理を全部待ってから取る。待たないと、進み具合より記録が遅れた控えになり、戻したときに言明が欠けるため。
    データベースは SQLite の backup で写す。動いているファイルをそのまま写すと、書きかけの分が欠けることがあるため。
    途中で落ちても半端な控えが残らないよう、写し終えてから名前を付ける。前の控えは上書きしない。
    関連するテスト：tests/test_run_script_jobs.py の test_年の変わり目に控えを取る
    """
    if job_runner is not None:
        job_runner.drain()
        job_runner.check()
    progress.save()
    base = progress.path.parent / CHECKPOINT_FOLDER
    lines = len(progress.digests)
    target = base / name
    number = 1
    while target.exists():
        if json.loads((target / CHECKPOINT_INFO).read_text(encoding="utf-8"))["lines"] == lines:
            return None
        number += 1
        target = base / f"{name}-{number}"
    partial = base / f"{target.name}.partial"
    partial.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(partial / db_path.name)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    for file_name in _PROGRESS_FILES:
        if (progress.path.parent / file_name).exists():
            shutil.copy2(progress.path.parent / file_name, partial / file_name)
    info = {"name": target.name, "lines": lines, "before_day": day.isoformat(), "taken_at": datetime.now().isoformat(timespec="seconds")}
    (partial / CHECKPOINT_INFO).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    partial.replace(target)
    return target


def _read_checkpoints(base: Path) -> dict[str, dict]:
    if not base.exists():
        return {}
    found = {}
    for item in sorted(base.iterdir()):
        if (item / CHECKPOINT_INFO).exists() and not item.name.endswith(".partial"):
            found[item.name] = json.loads((item / CHECKPOINT_INFO).read_text(encoding="utf-8"))
    return found


def restore_checkpoint(folder: Path, name: str) -> dict:
    """控えへ戻す。今のデータベースと進み具合は消さず、replaced-時刻 のフォルダへ移す。

    戻す控えより後の控えも一緒に移す。流し直した結果と混ざらないようにするため。
    動いているデータベースは移せないので、流している途中ならここで止まり、何も変わらない。
    関連するテスト：tests/test_run_script_jobs.py の test_控えへ戻すと_その年の前から流し直せて_戻す前の分は残る
    """
    db_path = config.deployment(data_dir=folder).db_path
    base = folder / CHECKPOINT_FOLDER
    known = _read_checkpoints(base)
    if name not in known:
        raise StopRun(f"控え {name} がありません。あるのは：{'、'.join(known) or 'なし'}")
    info = known[name]
    aside = folder / f"replaced-{datetime.now():%Y%m%d-%H%M%S}"
    extra = 1
    while aside.exists():
        extra += 1
        aside = folder / f"replaced-{datetime.now():%Y%m%d-%H%M%S}-{extra}"
    aside.mkdir()
    try:
        db_path.replace(aside / db_path.name)
    except OSError as error:
        aside.rmdir()
        raise StopRun("データベースを移せませんでした。流している途中なら、止めてから戻してください。") from error
    for file_name in (f"{db_path.name}-wal", f"{db_path.name}-shm", *_PROGRESS_FILES):
        if (folder / file_name).exists():
            (folder / file_name).replace(aside / file_name)
    for other, other_info in known.items():
        if other_info["lines"] > info["lines"]:
            (aside / CHECKPOINT_FOLDER).mkdir(exist_ok=True)
            (base / other).replace(aside / CHECKPOINT_FOLDER / other)
    shutil.copy2(base / name / db_path.name, db_path)
    for file_name in _PROGRESS_FILES:
        if (base / name / file_name).exists():
            shutil.copy2(base / name / file_name, folder / file_name)
    return {**info, "replaced": aside.name}


def read_scene(folder: Path) -> dict | None:
    """書き残した訂正の場面を読む。まだなければ None。"""
    path = folder / SCENE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _keep_awake() -> None:
    """流す間、パソコンがスリープに入らないようにする。全件は一晩かかるため。プロセスが終われば元に戻る。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED。画面の消灯は止めない
        ctypes.WinDLL("kernel32").SetThreadExecutionState(0x80000000 | 0x00000001)
    except (OSError, AttributeError):
        pass


@contextmanager
def shared_api_client() -> Iterator[None]:
    """流す間だけ、AI の接続を1つ使い回す。

    ふだんの起動は呼ぶたびに作る。全件では本線だけで約1万回呼ぶので、接続を作り直す分（測って1回約0.1秒）を省く。
    SDK の接続は、同時に別のスレッドから使える。流し終えたら元に戻す。
    関連するテスト：tests/test_run_script_jobs.py の test_流す間だけ_AIの接続を1つ使い回す
    """
    original = ai._make_client
    guard = threading.Lock()
    shared: list = []

    def make():
        with guard:
            if not shared:
                shared.append(original())
            return shared[0]

    ai._make_client = make
    try:
        yield
    finally:
        ai._make_client = original
        for client in shared:
            client.close()


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
    workers: int = 0,
    timer: _Timer | None = None,
    checkpoint_days: tuple[date, ...] = (),
    speed_every: int = SPEED_EVERY_LINES,
) -> int:
    """残りの行を流す。流した行の数を返す。workers が 0 なら、裏の処理も順に待つ。"""
    runner = Runner(client, progress, keep_going=keep_going)
    runner.check_mode()
    digests = [line_digest(line) for line in lines]
    stamps = spaced_times(lines)
    done = len(progress.digests)
    if digests[:done] != progress.digests:
        raise StopRun("すでに流した行が変わっています。別の --folder で流し直してください。")
    count = 0
    with running_jobs(client.app, workers, progress.path.parent, timer) as job_runner:
        runner.job_runner = job_runner
        speed = SpeedLog(progress.path.parent, db_path, progress, job_runner, speed_every)
        previous_day = lines[done - 1].day if done else None
        try:
            for number, line in enumerate(lines[done:], start=done + 1):
                if until and line.day > until:
                    break
                if stop_after is not None and count >= stop_after:
                    break
                if job_runner is not None:
                    job_runner.check()
                if (count % BUDGET_EVERY_LINES == 0 or line.kind == "整理") and spent_usd(db_path, progress) > budget_usd:
                    raise StopRun(f"費用の枠 ${budget_usd} を超えそうなので、{number}行目の前で止めました。")
                checkpoint = checkpoint_due(previous_day, line.day, checkpoint_days)
                if checkpoint:
                    save_checkpoint(checkpoint, progress, db_path, job_runner, line.day)
                previous_day = line.day
                runner.resending = number <= progress.sent_upto
                progress.note_sending(number)
                # 発言の印は行の中身から作る。止まって送り直しても同じ印になり、発言は増えない
                runner.run_line(number, line, digests[number - 1], stamps[number - 1])
                progress.digests.append(digests[number - 1])
                progress.sending = None
                count += 1
                if line.kind not in _SAFE_TO_RESEND or count % SAVE_EVERY_LINES == 0:
                    progress.save()
                if line.kind == "整理":
                    # 固めた直後の状態を、あとから写して試せるように残す
                    save_checkpoint(f"consolidated-{line.day.isoformat()}", progress, db_path, job_runner, line.day)
                speed.line_done(number, line)
            # 止めるときも、流し終わるときも、裏の処理が終わってから戻る
            if job_runner is not None:
                job_runner.drain()
                job_runner.check()
        finally:
            progress.save()
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
    """別のデータベースでアプリを開く。リポジトリの物語は読み込まない（demo.ensure_story を呼ばない）。

    デモの顔は入れる。写真のフォルダがなければ何も入れない。
    """
    deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
    folder.mkdir(parents=True, exist_ok=True)
    app = create_app(deployment=deployment)
    demo.ensure_demo_faces(deployment)
    return TestClient(app), deployment.db_path


def _silent_letters(lines: list[Line], progress: Progress | None) -> set[int]:
    """最後に送ったコメントが問わない行である便りの番号を返す。"""
    if progress is None:
        return set()
    last_ask: dict[str, bool | None] = {}
    for line in lines[: max(len(progress.digests), progress.sent_upto)]:
        if line.kind == "コメント":
            last_ask[line.letter] = line.ask
    known = progress.letters
    return {known[ref]["id"] for ref, ask in last_ask.items() if ask is False and ref in known}


def _restart_lost_threads(app, lines: list[Line] = (), progress: Progress | None = None) -> None:
    """待ちの中で失われたスレッドの処理を流し直す。

    問うコメントのあとは次の行が処理を待つので、問いが失われるのは便りごとに最後のコメントの分だけ。その処理の前に
    落ちていれば、最後のコメントは人のコメントのままで、印もそこまで進んでいない。分割は印のあとを全部読むので、
    問わないコメントが何件失われていても、便りごとに1回流せば足りる。
    コメントの時刻で流すので、問いの日付はずれない。最後のコメントが問わない行なら、流し直しでも問わない。
    関連するテスト：tests/test_run_script_jobs.py の test_待ちの中の処理を失って流し直しても_重ならず欠けない
    """
    conn = db.connect(app.state.db_path)
    try:
        lost = []
        for letter_id in jobs.unsplit_letters(conn):
            row = conn.execute(
                "SELECT author_user_id, is_twin_question, commented_at FROM comments WHERE letter_id = ? ORDER BY rowid DESC LIMIT 1",
                (letter_id,),
            ).fetchone()
            if row is not None and not row["is_twin_question"]:
                lost.append((letter_id, row["author_user_id"], datetime.fromisoformat(row["commented_at"])))
    finally:
        conn.close()
    silent = _silent_letters(lines, progress)
    for letter_id, author, commented_at in lost:
        app.state.twin_may_ask = letter_id not in silent
        with clock.frozen(commented_at):
            jobs.after_comment(app, letter_id, author)
    app.state.twin_may_ask = True


def catch_up_for_resume(
    client: TestClient, lines: list[Line], progress: Progress, *, workers: int = 0, timer: _Timer | None = None
) -> None:
    """前回止まったときに残った裏の処理を流し直す。

    先に日付を、送り始めていた行に合わせる。catch_up は今日と違う日の開いた会話を閉じるので、
    合わせないと、途中で止めた会話がここで閉じてしまい、続きの発言が別の会話になるため。
    進み具合の保存はまとめて行うので、「終わった行の次」ではなく「送り始めた最後の行」で合わせる。
    関連するテスト：tests/test_run_script.py の test_会話の途中で止めて開き直しても_会話は閉じない
    """
    done = len(progress.digests)
    if not lines or (done == 0 and progress.sent_upto == 0):
        return
    Runner(client, progress).check_mode()
    at = spaced_times(lines)[min(max(done, progress.sent_upto - 1), len(lines) - 1)]
    response = client.post("/api/dev/date", json={"at": at.isoformat()})
    if response.status_code >= 400:
        raise StopRun(f"日付の変更が失敗しました（{response.status_code}）")
    with running_jobs(client.app, workers, progress.path.parent, timer) as job_runner:
        _restart_lost_threads(client.app, lines, progress)
        if job_runner is not None:
            job_runner.drain()
        jobs.catch_up(client.app)
        if job_runner is not None:
            job_runner.drain()
            job_runner.check()


def _job_notes(db_path: Path) -> list[str]:
    """流したあとの裏の処理の様子を、本文を入れずに返す。"""
    conn = db.connect(db_path)
    try:
        failures = conn.execute("SELECT COUNT(*) FROM job_failures").fetchone()[0]
        unsplit = len(jobs.unsplit_conversations(conn)) + len(jobs.unsplit_letters(conn))
    finally:
        conn.close()
    notes = []
    if failures:
        notes.append(f"裏の処理の記録（job_failures）が {failures} 件あります。")
    if unsplit:
        notes.append(f"まだ言明に分けていない会話か便りが {unsplit} 件あります。同じ命令で流し直せます。")
    return notes


def _count_of_workers(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("0 以上にしてください")
    return value


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
    parser.add_argument(
        "--workers",
        type=_count_of_workers,
        default=DEFAULT_WORKERS,
        help="裏の処理の同時の数の上限。2本から始めて少しずつ上げます。0 は裏の処理も順に待ちます",
    )
    parser.add_argument(
        "--checkpoint-days",
        default="",
        help="年の変わり目のほかに控えを取る日。カンマで分けます（例：2026-11-03,2036-05-06）",
    )
    parser.add_argument("--restore-checkpoint", help="この控えへ戻して止まります。今の分は replaced-時刻 へ移します")
    args = parser.parse_args(argv)

    if args.restore_checkpoint:
        try:
            info = restore_checkpoint(safe_folder(args.folder), args.restore_checkpoint)
        except StopRun as error:
            print(f"止めました：{error}")
            return 2
        print(f"{info['name']} へ戻しました（{info['lines']}行まで、{info['before_day']} の前）。今の分は {info['replaced']} へ移しました。同じ命令で続きから流せます。")
        return 0

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
        marks = tuple(date.fromisoformat(raw) for raw in args.checkpoint_days.split(",") if raw)
        _keep_awake()
        with shared_api_client():
            catch_up_for_resume(client, lines, progress, workers=args.workers)
            count = run_lines(
                client,
                lines,
                progress,
                db_path=db_path,
                budget_usd=args.budget,
                until=args.until,
                stop_after=args.stop_after,
                keep_going=args.keep_going,
                workers=args.workers,
                checkpoint_days=marks,
            )
    except GuessNotAbout as error:
        print(f"止めました：{error}")
        return GUESS_EXIT
    except StopRun as error:
        print(f"止めました：{error}")
        return 2
    written = write_story(progress, folder / "story")
    print(f"{count}行を流しました。全部で {len(progress.digests)}/{len(lines)} 行。費用 約 {spent_usd(db_path, progress):.3f} ドル。")
    for path in written:
        print(f"書き出し：{path}")
    for warning in progress.warnings:
        print(f"注意：{warning}")
    for note in _job_notes(db_path):
        print(f"注意：{note}")
    confirms = sum(1 for line in lines[: len(progress.digests)] if line.kind == "確かめ")
    if confirms:
        print(f"確かめの行：{confirms}行。見つからずに飛ばした行：{progress.skipped_confirms}行。")
    scene = read_scene(folder)
    if scene:
        print(f"訂正の場面（{scene['method']}）：推測「{scene['guess']}」→ 訂正「{scene['correction']}」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
