"""架空の記録で、ツインの問いに本人の短い受け答えを足す。

役割：チャットは本物のツインの返事を見ずに書いたので、ツインが問うても次の行は答えにならない。
      そこで AI が本人の短い受け答えを書き、プログラムと別の AI で確かめる。
4つの極：1. 書く・話す（架空の記録を作る道具）。
守る原則：1（判断を足さない）、3（出どころを分ける）、8（すべて架空）、9（本人の言葉は記録の本文だけ）。

足した文は本人が話した言葉ではないので、記録にしない（fiction/run_script.py の _do_added）。
画面の会話には出るので、記録に入らなくても次を確かめる。
- プログラム：字数、病名、泊まりの語、関西の言い方、一人称、丁寧語、渡した事実にない数字。
- 別の AI：新しい人、出来事、数字がないか、筑後弁か。

確かめに通らなければ理由を添えて書き直させ、それでもだめなら決まった短い言葉にする。
新しい事実を入れるより、答えを濁すほうがこの本人らしいため。

モデルは Sonnet 5.5 にする（app/config.py）。回数は全体の中でわずかで、方言の加減と、
事実を足さない判断の差が画面に出るため。

関連するテスト：tests/test_added_words.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

ROLE_WRITE = "架空の受け答え"
ROLE_CHECK = "架空の受け答えの確かめ"
MAX_TRIES = 3

# 字数の上限。チャットの本人の受け答えの字数の中央値（11字、5字）に合わせ、時期が進むほど短くする
LIMIT_FIRST = 25
LIMIT_SHORT = 12
LIMIT_BROKEN = 8

_KANSAI_WORDS = ("あかん", "ほんま", "めっちゃ", "おおきに", "やねん", "せやな", "そやな", "しゃあない", "なんぼ", "ちゃう", "ええ")
_KANSAI_ENDING = re.compile(r"(?:へん|はる|やで|やん)(?:[。、！？…]|$)")
_OTHER_SELF = ("私", "わたし", "わし", "僕", "ぼく", "おれ", "自分は")
# チャットの本人が使っていない言い方。ほかの地方の九州の言葉が混ざらないようにする
_NOT_HIS_WORDS = ("そげん", "どげん", "こげん", "あげん", "ごわす", "とよ")
# 渡した指示の言葉。本人の口から出ると、AI が書いた文だと分かる（実際に「事実にはなかけん」と書いた）
_PROMPT_WORDS = ("事実", "字数", "上限", "話し方", "指示", "渡され", "ヒント", "チャット", "プロンプト", "筑後弁", "AI", "ＡＩ", "ツイン")
# 同じ言い回しが続くと機械のように読めるので、最近の文と共通の並びがこの字数あれば止める
_SAME_RUN = 5
# 物忘れが物語に出てくるのは2032年10月からなので、それより前に本人に言わせない
_FORGET_FROM = "2032-10-01"
_POLITE = re.compile(r"(?:です|ます|ました|でした|ません)(?:[。、！？…]|$)")
_DIGITS = re.compile(r"[0-9０-９]+")
# 「一」は「一人」「一回」のようにふつうの言葉に多いので見ない
_KANJI_NUMBER = re.compile(r"[二三四五六七八九十百千万]+")


class AddedLine(BaseModel):
    """本人の短い受け答え。"""

    text: str = Field(max_length=80)


class AddedCheck(BaseModel):
    """足した文の確かめの結果。空なら通す。"""

    new_people: list[str] = Field(default_factory=list)
    new_events: list[str] = Field(default_factory=list)
    new_numbers: list[str] = Field(default_factory=list)
    not_chikugo: str | None = None


WRITE_SYSTEM = """あなたは、架空の人物「誠一さん」の短い受け答えを書く係です。人物と記録はすべて架空です。

## 誠一さん
1952年生まれ。大牟田市の出身で、市役所を定年まで勤めました。縁側と庭のある家に一人で暮らしています。頑固で、口数が少ない人です。

## 書き方
- ツインの問いに、誠一さんとして短く答えます。1文か2文。字数の上限を必ず守ります。
- 言葉は筑後弁です（〜たい、〜ばい、〜けん、〜じゃけん、〜ばってん、〜ば、〜ごたる、〜しとる、よか、なか）。「話し方の例」にそろえます。丁寧語を使いません。関西の言い方を使いません。「そげん」「どげん」は使いません。
- 一人称は「俺」です。一人称を書かなくてもかまいません。
- 渡された「事実」と「話し方の例」だけを使います。**新しい人、出来事、数字を足しません。**
- 事実から答えられない問いには、新しい話を作らず、短く濁します（例：「知らん。」「また今度たい。」「よか。」）。
- 事実に書いていない具体（切り方、味付け、量、場所、誰がしたか）を答えません。「最近の受け答え」と同じ言い回しを使いません。
- 「事実」「話し方」「字数」など、ここに書いてある言葉を受け答えに書きません。誠一さんは、この決まりを知りません。
- 「## 物忘れ」に書いてある時期は、「忘れた」と言いません。
- 病名、診断、薬の名前、泊まりの話は書きません。気持ちを説明しません。
- ツインの言葉をくり返しません。"""

CHECK_SYSTEM = """あなたは、架空の人物の短い受け答えを確かめる係です。人物と記録はすべて架空です。

渡された「事実」と「ツインの問い」と「受け答え」を読み、受け答えに、事実にない次のものがあれば書き出します。
- new_people：事実に出てこない人
- new_events：事実に出てこない出来事や具体。切り方、味付け、量、場所、誰がしたか、これからすることも入れます。感想と、答えを濁す言葉は入れません
- new_numbers：事実に出てこない数字
- not_chikugo：筑後弁でない言い方（関西の言い方、標準語の丁寧語など）があれば、その言い方と理由。なければ null。たい、ばい、けん、じゃけん、ばってん、よか、なか、とる、ごたる、やった、やけん、〜ば は、この人の言葉として通します

なければ空の配列にします。"""

# 確かめに通らなかったときの言葉。AI の文ではなく、新しい事実が入らない
_FALLBACK = {
    LIMIT_FIRST: ("また今度たい。", "知らん。", "よか。", "まあ、な。"),
    LIMIT_SHORT: ("よか。", "知らん。", "まあ、な。"),
    LIMIT_BROKEN: ("……うん。", "……よか。", "……な。"),
}


def limit_for(day: str) -> int | None:
    """その日の本人の話し方に合わせた字数の上限を返す。足さない時期は None。

    2035-10 からは本人が自分で打てない物語なので、受け答えを足さず、ツインも問わない。
    """
    if day >= "2035-10-01":
        return None
    if day >= "2034-10-01":
        return LIMIT_BROKEN
    if day >= "2032-10-01":
        return LIMIT_SHORT
    return LIMIT_FIRST


def fallback_for(limit: int, recent: tuple[str, ...] = ()) -> str:
    """決まった短い言葉を返す。最近使っていないものを先に選ぶ。"""
    choices = _FALLBACK.get(limit, _FALLBACK[LIMIT_BROKEN])
    return next((word for word in choices if word not in recent), choices[0])


def _repeats(words: str, recent: tuple[str, ...]) -> bool:
    """最近の文と同じ言い回しがあるかを返す。"""
    if words in recent:
        return True
    runs = {words[start : start + _SAME_RUN] for start in range(len(words) - _SAME_RUN + 1)}
    return any(run in item for run in runs for item in recent)


def added_problem(text: str, *, hint: str, limit: int, forbidden, day: str = _FORGET_FROM, recent: tuple[str, ...] = ()) -> str | None:
    """足した文の問題を返す。なければ None。forbidden は病名と泊まりの語を探す関数（fiction/run_script.py）。

    recent は最近足した文。同じ言葉が続くと機械のように読めるので止める。
    """
    words = text.strip()
    if not words:
        return "受け答えが空です"
    if len(words) > limit:
        return f"{limit}字以内にしてください（今は{len(words)}字）"
    found = forbidden(words)
    if found:
        return f"書かない言葉があります：{'、'.join(found)}"
    kansai = [word for word in _KANSAI_WORDS if word in words]
    if kansai or _KANSAI_ENDING.search(words):
        return f"関西の言い方があります：{'、'.join(kansai) or '語尾'}。筑後弁にしてください"
    not_his = [word for word in _NOT_HIS_WORDS if word in words]
    if not_his:
        return f"この人の言葉にない言い方です：{'、'.join(not_his)}。話し方の例にそろえてください"
    if day < _FORGET_FROM and "忘れ" in words:
        return "この時期の本人は「忘れた」と言いません。答えられないなら、別の言葉で短く濁してください"
    if _repeats(words, recent):
        return "最近と同じ言い回しです。別の言葉にしてください"
    leaked = [word for word in _PROMPT_WORDS if word in words]
    if leaked:
        return f"決まりの言葉を受け答えに書かないでください：{'、'.join(leaked)}"
    other = [word for word in _OTHER_SELF if word in words]
    if other:
        return f"一人称は「俺」にしてください：{'、'.join(other)}"
    if _POLITE.search(words):
        return "丁寧語を使わず、筑後弁にしてください"
    numbers = [number for number in (*_DIGITS.findall(words), *_KANJI_NUMBER.findall(words)) if number not in hint]
    if numbers:
        return f"事実にない数字があります：{'、'.join(numbers)}"
    return None


def write_prompt(
    *, day: str, hint: str, recent: list[str], question: str, limit: int, feedback: str | None = None, recent_added: tuple[str, ...] = ()
) -> str:
    parts = [f"今日：{day}", f"字数の上限：{limit}字"]
    if day < _FORGET_FROM:
        parts += ["## 物忘れ", "この時期の本人は、まだ物忘れがありません。"]
    parts += ["## 事実と話し方の例", hint, "## 直前のやりとり", *recent, "## ツインの問い", question]
    if recent_added:
        parts += ["## 最近の受け答え（同じ言い回しを使わない）", *recent_added]
    if feedback:
        parts += ["## 前の案の直すところ", feedback]
    return "\n".join(parts)


def check_prompt(*, hint: str, question: str, text: str) -> str:
    return "\n".join(["## 事実", hint, "## ツインの問い", question, "## 受け答え", text])


def check_problem(result: AddedCheck) -> str | None:
    """別の AI の確かめの結果を、書き直しの理由にする。なければ None。"""
    reasons = []
    if result.new_people:
        reasons.append(f"事実にない人：{'、'.join(result.new_people)}")
    if result.new_events:
        reasons.append(f"事実にない出来事：{'、'.join(result.new_events)}")
    if result.new_numbers:
        reasons.append(f"事実にない数字：{'、'.join(result.new_numbers)}")
    if result.not_chikugo:
        reasons.append(f"筑後弁でない言い方：{result.not_chikugo}")
    return "。".join(reasons) or None
