"""ツインの言葉を作り、確かめて保存する。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：本人とツインの部屋、グループ、スレッドで、AI の答えをプログラムの決まりに通して発言にする。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：1（判断しない）、6（お休みの話題とつらい出来事は聞かない）、7（引き留めない）、
          12（本人になりきらない）、14（スレッドのツインは問いを1つ）、15（招かれた部屋だけを読む）、
          17（診断と服薬の指示だけをしない。一般的な健康の相談には答える）。

返事は1回の呼び出しで書く。発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択する（choose_turn、app/twin_model.py）。

プロンプトの決まりは AI へのお願いで、守られるとは限らない。次のことはプログラムで決める。
- 問いは1回に1つまで（型で受け止めと問いを分ける。app/schemas.py）。
- お休みのテーマ、つらかったことのテーマ、同じ問い、本人が終えたそうなときの問いは落とす。
- 問いが2回続いたら、次は受け止めるだけにする。尋問のようにしないため。
- 受け止めの文から、気持ちの決めつけと、本人が今話していない体やつらさの話の文を外す。
- 体の急な不調、命に関わる言葉、強いつらさ、薬や病気かどうかの相談には問いを続けず、つなぎ先の文をプログラムが添える。
- 受け止めの文から、診断や服薬の指示を言い切る文を外す（app/medical.py）。
  語の一覧を AI の判定に重ねるのは、AI が断ったり見落としたりしても、つなぎ先を必ず出すため。
- グループでは、ツインが入ったあとのその部屋の発言だけを渡す。スレッドでは、便りとコメントだけを渡す。
  本人の考えや気持ちを聞かれたら、AI を呼ばずに決まった文を返す。

問いの選び方は、固める処理ができるまでの仮の決まり（docs/decisions.md）。
関連するテスト：tests/test_twin.py、tests/test_rooms.py、tests/test_timeline.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import ai, config, db, devlog, heart, ledger, medical, memory, photos, prompts, room_store, twin_model
from app.schemas import (
    InferenceKind,
    CareKind,
    Conversation,
    GroupTurnKind,
    Place,
    QuestionResult,
    Role,
    Room,
    RoomMessage,
    Theme,
    User,
    UtteranceDraft,
)

# 語の一覧は AI の判定に重ねるもの。一覧だけで見分けようとはしない。
_LIFE_WORDS = (
    "死にたい",
    "死のう",
    "消えたい",
    "自殺",
    "生きていてもしょうがない",
    "生きててもしょうがない",
    "生きていても仕方",
    "生きてても仕方",
    "生きている意味",
    "いなくなりたい",
    "野垂れ死",
)
_BODY_WORDS = (
    "胸が痛",
    "胸の痛",
    "胸が苦し",
    "息が苦し",
    "息苦し",
    # できない形だけを見る。「親も息ができる」のようなふつうの文に119番の案内を付けないため。
    "息ができな",
    "息ができん",
    "息ができません",
    "息ができず",
    "ろれつ",
    "意識が",
    "血を吐",
    "激しい痛",
    "急に動けな",
    "倒れた",
)
_DISTRESS_WORDS = ("もう耐えられない", "もう限界", "つらくてたまらない", "苦しくてたまらない")
# 診断か服薬の指示を求める発言（原則17）。話題の語と、聞く言葉の両方があるときだけ相談先を添える。
# 腰の痛みや眠れないことの「どうしたら」は、一般的な健康の相談なので入れない。ツインが暮らしの工夫で答える。
# 薬の量や飲み続けるか、病気かどうか、診てもらうかどうかを聞かれたときだけ、お医者さんか薬剤師さんにつなぐ。
# 「腰が痛いんです。」のように話しているだけの発言に案内を付けると、話を聞いていないように読めるため。
# 湿布は市販の薬にあたるので、どれを貼るかを聞かれたら薬と同じにつなぐ
_HEALTH_TOPICS = ("薬", "湿布", "病気", "病名", "診断", "病院に行", "受診")
# 「薬」を含むが、飲む薬の話ではない語
_NOT_MEDICINE = ("農薬", "薬局", "薬味", "薬剤師")
# 「どっち」「どちら」は選ぶことを聞く形だけを見る。「どっちも」「どっちか分からん」「どっちになるか」は
# 話しているだけの文で、案内を付けると話を聞いていないように読めるため。
_HEALTH_ASKS = (
    "どうしたら",
    "どうすれば",
    "いいかな",
    "いいのかな",
    "いいですか",
    "いいでしょうか",
    "どっちが",
    "どちらが",
    "どっちを",
    "どちらを",
    "どっちの",
    "どちらの",
    "べきか",
)

# つなぎ先の文。AI の文ではなく、プログラムが必ず添える。番号は厚生労働省の案内で確かめた。
_CARE_LINES = {
    CareKind.BODY: "急な体の不調は、すぐに身近な人に伝えてください。かかりつけの病院か、急ぐときは119番に電話してください。",
    CareKind.LIFE: "おひとりで抱えないでください。身近な人に伝えてください。よりそいホットライン（0120-279-338、24時間）でも話を聞いてもらえます。",
    CareKind.DISTRESS: "身近な人に話してみてください。かかりつけの病院や、よりそいホットライン（0120-279-338、24時間）でも話を聞いてもらえます。",
    CareKind.HEALTH: "病気かどうか、診てもらうかどうか、薬の量や飲み続けるかは、お医者さんか薬剤師さんに聞いてみてください。",
}
# AI が答えられないときの受け止め。つなぎ先の文の前に置く。
_CARE_RECEIVE = {
    CareKind.BODY: "それは心配です。",
    CareKind.LIFE: "お話ししてくださって、ありがとうございます。",
    CareKind.DISTRESS: "お話ししてくださって、ありがとうございます。",
    CareKind.HEALTH: "それはお困りですね。",
}
_CARE_ORDER = [CareKind.NONE, CareKind.HEALTH, CareKind.DISTRESS, CareKind.BODY, CareKind.LIFE]
# 相談先の案内はプログラムが添えるので、AI の文に同じ案内があれば外す。
_REFERRAL_WORDS = ("お医者", "医師", "医療", "薬剤師", "看護師", "かかりつけ", "病院", "受診", "専門", "ご相談")
# 句点で終わる問いの形。この場面では原因を聞かないので、受け止めの文に混ざっていたら決まった文に替える。
_HIDDEN_QUESTION = re.compile(r"(ますか|ませんか|ですか|でしたか|でしょうか|ありますか)[。！!]?")

# 本人が終えたそうな言葉。問いを落とし、引き留めない。誤って落としても、問わないだけで害は小さい。
_GOODBYE_WORDS = (
    "もう帰",
    "帰ります",
    "帰るね",
    "今日はいい",
    "今日はもういい",
    "もういいよ",
    "そろそろ",
    "おしまい",
    "疲れた",
    "もう寝",
    "寝ます",
    "またね",
    "また今度",
    "じゃあね",
    "おやすみ",
    "さようなら",
)

# AI から聞かない話題の語。問いにこの語があれば落とす。
_NOT_TO_ASK = ("病気", "病院", "薬", "痛み", "痛い", "体調", "具合", "亡くな", "つらかった", "辛かった", "お金", "借金")

# 活用で形が変わる語は、本人が話したかを語の頭で見る。「痛くて」と話した人に「痛いのですね」と返せるようにするため。
_TOPIC_STEMS = {
    "痛み": "痛",
    "痛い": "痛",
    "病気": "病",
    "病院": "病",
    "亡くな": "亡",
    "つらかった": "つら",
    "辛かった": "辛",
}

# 体の話の語。本人が今の発言で体の話をしていれば、「具合」「病院」と返しても持ち出しにならない。
_BODY_TOPIC_WORDS = ("病気", "病院", "薬", "痛み", "痛い", "体調", "具合")
# 本人が体の話をしているかを見る語。相談先を添える語（_HEALTH_TOPICS）より広く見る
_BODY_TALK = ("薬", "痛", "湿布", "血圧", "血糖", "病院", "病気", "眠れな", "手術", "検査", "体調", "具合", "腰", "膝")
# 薬の使い方の語。体の話への返事にあれば、その文を外す。服薬の指示をしない決まりを、プログラムでも守るため（原則17）。
# 温める、休む、体操のような一般的な健康の工夫は、してよいので外さない。
_TREATMENT_WORDS = ("痛み止め", "湿布を貼", "湿布が", "湿布は")
# 今日の一言に入れない語。薬と受診の話は服薬の指示や急ぐかの判定に近いので出さない。
# 体を温める、水分をとるのような一般的な健康の工夫は出してよい（原則17）。
_HEALTH_ADVICE_WORDS = ("薬", "湿布", "受診")

# 本人の気持ちを決めつける言い方。プロンプトで頼んでも実測で出たので、この文だけプログラムで外す。
# やり直しにしないのは、返事を待たせず、失敗も増やさないため。
_ASSUMING_WORDS = ("伝わります", "伝わってきます", "伝わる", "思い浮かびます", "大切にされて", "大切に使われて")

# 本人の考えや気持ちを聞く言葉。AI を呼ばずに決まった文を返す。
_ASKS_FOR_SELF = ("本音", "本心", "気持ち", "どう思", "考えて", "思って", "望んで", "したいの", "意思")

_MENTION = re.compile(r"[@＠]\s*ツイン")

# 本人の言葉を伝える言い方。今のところ、本人の言葉を番号で取り出して届ける処理はないので、
# AI がこの形で書いた文は、原文と照らせるものだけを通す。原則9（本人の言葉は記録の本文そのままだけ）。
_REPORTED_PAST = re.compile(r"おっしゃいました|おっしゃっていました|おっしゃっています|言っていました|話していました|言っています|話しています")
_QUOTE_THEN_REPORT = re.compile(r"「([^「」]+)」と(?:おっしゃ|言|話|うかが)")
_HEARD_RECEIPT = "そうでしたか。"
_FAMILY_WORDS = ("お父さん", "お母さん", "父", "母", "本人", "ご本人")
_MAX_QUESTIONS_IN_A_ROW = 2


def detect_care(text: str) -> CareKind:
    """語の一覧から、問いを続けない場面かを見る。"""
    if any(word in text for word in _LIFE_WORDS):
        return CareKind.LIFE
    if any(word in text for word in _BODY_WORDS):
        return CareKind.BODY
    if any(word in text for word in _DISTRESS_WORDS):
        return CareKind.DISTRESS
    topic_text = text
    for word in _NOT_MEDICINE:
        topic_text = topic_text.replace(word, "")
    if any(word in topic_text for word in _HEALTH_TOPICS) and any(word in text for word in _HEALTH_ASKS):
        return CareKind.HEALTH
    return CareKind.NONE


def _stronger(first: CareKind, second: CareKind) -> CareKind:
    return max(first, second, key=_CARE_ORDER.index)


def settled_ai_care(ai_care: CareKind, said: str) -> CareKind:
    """眠れないだけの話を AI が急な不調と読んだら、急な不調として扱わない。

    実測で「眠れない。どうしたらいいかな」を Haiku 5.5 が3回中2回「体の不調」と読み、119番の案内が付いた。
    痛みや苦しさが重なるときは AI の判定を残す。見落とすほうが害が大きいため。
    関連するテスト：tests/test_twin.py の test_眠れないだけの話をAIが急な不調と読んでも_119番の案内を出さない
    """
    if ai_care is CareKind.BODY and "眠れな" in said and not any(word in said for word in ("痛", "苦し")):
        return CareKind.NONE
    return ai_care


def care_lines(ai_care: CareKind, keyword_care: CareKind) -> list[str]:
    """添えるつなぎ先の文を返す。

    AI が胸の痛みを「命に関わる」と読むと、強いほうの判定だけでは119番の案内が消える（実測）。
    語の一覧が急な体の不調と読んだら、AI の判定にかかわらず119番の案内を先に出す。
    関連するテスト：tests/test_twin.py の test_AIが胸の痛みを命の場面と読んでも_119番の案内を消さない
    """
    care = _stronger(ai_care, keyword_care)
    if care is CareKind.NONE:
        return []
    if keyword_care is CareKind.BODY and care is not CareKind.BODY:
        return [_CARE_LINES[CareKind.BODY], _CARE_LINES[care]]
    return [_CARE_LINES[care]]


def says_goodbye(text: str) -> bool:
    return any(word in text for word in _GOODBYE_WORDS)


def question_is_allowed(question: str, theme: Theme | None, *, resting: set[Theme], asked: list[str]) -> bool:
    """AI の問いを、聞いてよいものか確かめる。"""
    if theme is Theme.HARDSHIP or (theme is not None and theme in resting):
        return False
    if any(word in question for word in _NOT_TO_ASK):
        return False
    return not memory.is_repeated(question, asked)


def words_are_real(text: str, sources: list[str]) -> bool:
    """本人の言葉を伝える文が、原文と照らせるかを返す。

    「〜」と言いました、の中身が今の話か前に聞いた本人の言葉にあれば通す。中身を示さずに
    「おっしゃっていました」と伝える文は、照らせないので通さない。
    """
    joined = "\n".join(sources)
    quotes = _QUOTE_THEN_REPORT.findall(text)
    if quotes:
        return all(quote in joined for quote in quotes)
    return _REPORTED_PAST.search(text) is None


def reports_self(text: str, self_user: User) -> bool:
    """グループの答えが、本人の考えや発言を伝える文かを返す。"""
    names = (self_user.name, self_user.display_name, *_FAMILY_WORDS)
    return _REPORTED_PAST.search(text) is not None and any(name in text for name in names)


def trim_reply(reply: str, heard: str) -> str:
    """受け止めの文から、気持ちの決めつけと、本人が今話していない体やつらさの話の文を外す。

    前の話に出た不調を AI が持ち出すと、関係のない話の途中で思い出させることになる（原則6）。
    heard は今の発言と写真の説明。全部の文が外れたら空を返し、呼ぶ側が決まった文に替える。
    「薬剤師」は薬の話ではなく相談先なので、語の一致から外す。外さないと、プロンプトが書くよう求めた
    相談先の文まで消え、返事が言い直しの一言だけになる。
    関連するテスト：tests/test_twin.py の test_本人が今話している体の話への返事は_持ち出しとして消さない
    """
    sentences = re.findall(r"[^。！!\n]+[。！!]*", reply)
    talks_about_body = any(word in heard for word in _BODY_TALK)

    def brings_up(sentence: str) -> bool:
        sentence = sentence.replace("薬剤師", "")
        for word in _NOT_TO_ASK:
            if word not in sentence or (talks_about_body and word in _BODY_TOPIC_WORDS):
                continue
            if _TOPIC_STEMS.get(word, word) not in heard:
                return True
        return False

    def treats(sentence: str) -> bool:
        # 診断や服薬の指示を言い切る文は、体の話でなくても外す（原則17、app/medical.py）
        return (talks_about_body and any(word in sentence for word in _TREATMENT_WORDS)) or bool(medical.orders_in(sentence))

    kept = [
        sentence
        for sentence in sentences
        if not any(word in sentence for word in _ASSUMING_WORDS) and not brings_up(sentence) and not treats(sentence)
    ]
    return "".join(kept).strip()


def advises_on_medicine(tip: str) -> bool:
    """今日の一言が、薬や受診の話、診断や服薬の指示になっているかを返す。

    一般的な健康の工夫（体を温めてから動く、水分をとる）はしてよい（原則17、2026-11-06）。
    関連するテスト：tests/test_twin.py の test_薬の話を書いた今日の一言は出さない
    """
    return any(word in tip for word in _HEALTH_ADVICE_WORDS) or bool(medical.orders_in(tip))


# 今日の一言の決まり。ツインは天気予報を知らないので、予報を伝える形にしない。
# 天気の語と、聞いた話や見込みの言い方が同じ文にあるときだけ止める。「雨のあとの晴れた日は」のような話は止めない。
_WEATHER_WORDS = ("暑", "寒", "涼し", "暖か", "雨", "晴れ", "曇", "雪", "台風", "気温", "冷え込", "天気")
_HEARSAY = re.compile(r"(?:そう(?:なので|です|だ|で)|らしい|でしょう|見込み|なるので|なります)")
_TODAY_WEATHER = re.compile(r"(?:今日|明日|今夜|今晩|午後|夕方)(?:は|も|から)[^。、]{0,6}(?:暑|寒|涼し|暖か|雨|晴れ|曇|雪)")
# 時刻を過ぎた話。(この時より後なら, 語)。明日の話は止めない
_TIME_PASSED = ((12, ("午前中", "午前のうち", "朝のうち", "今朝のうち")), (17, ("昼のうち", "昼間のうち", "日中", "お昼", "昼前", "午後のうち")))
TIP_SENTENCE = re.compile(r"[^。！!\n]+")
# 今日の一言に書かない相談先。案内はプログラムが返事に添える文だけにする。番号を AI に書かせないため
_TIP_REFERRALS = re.compile(r"ホットライン|いのちの電話|相談窓口|相談先|\d{2,4}-\d{2,4}-\d{3,4}")


def tip_problem(tip: str, *, now: datetime, heard: str, gone: tuple[str, ...] = ()) -> str | None:
    """今日の一言の問題を返す。なければ None。

    実際に、夜の10時に「今日は暑くなるそうなので、午前中のうちに洗濯物を…」と書いた。本人の「暑うなるごたる」を
    予報のように言い直し、もう過ぎた時間の案を出したもの。プロンプトで禁じても守られるとは限らないので、ここで止める。
    heard は今日の話。本人が話した天気に触れるのはよい。
    gone は亡くなった方の呼び名。実際に、亡くなった方との夕飯を勧める一言を書いた。一言を作るときは今日の話だけを
    渡し、人の一覧を AI に送らないので、亡くなったことを AI は知らない。名前が入った一言をここで止める。
    相談先も止める。プログラムが返事に添えた案内を、AI がすぐ下の一言にくり返した回があった（実測）。
    関連するテスト：tests/test_twin.py の test_今日の一言
    """
    if _TIP_REFERRALS.search(tip):
        return "一言に相談先を書かないでください。暮らしの案か、季節のことにしてください。"
    if any(name in tip for name in gone):
        return "一言に人の名前を書かないでください。暮らしの案か、季節のことにしてください。"
    if "予報" in tip:
        return "天気予報を知らないので、予報の話を書かないでください。"
    for sentence in TIP_SENTENCE.findall(tip):
        weather = [word for word in _WEATHER_WORDS if word in sentence]
        if weather and _HEARSAY.search(sentence):
            return "予報を伝える形にしないでください。今日の話に出たことか、季節のことにしてください。"
        if _TODAY_WEATHER.search(sentence) and not any(word in heard for word in weather):
            return "話に出ていない今日の天気を書かないでください。"
        if "明日" in sentence or "明朝" in sentence:
            continue
        for hour, words in _TIME_PASSED:
            if now.hour >= hour and any(word in sentence for word in words):
                return f"今は{now.hour}時です。この時刻に合う案にしてください。"
    return None


def _gone_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    """亡くなった方の呼び名を返す。「和子さん」も「和子」も止めるよう、さんを除く。"""
    names = (user.display_name.removesuffix("さん") for user in db.list_users(conn) if user.is_deceased)
    return tuple(name for name in names if name)


def drop_referrals(reply: str) -> str:
    """AI の文から相談先の案内を外す。案内はプログラムが添えるので、2回くり返さないため。"""
    sentences = re.findall(r"[^。！!\n]+[。！!]*", reply)
    return "".join(sentence for sentence in sentences if not any(word in sentence for word in _REFERRAL_WORDS)).strip()


def mentions_twin(text: str) -> bool:
    return _MENTION.search(text) is not None


def asks_for_self(text: str) -> bool:
    return any(word in text for word in _ASKS_FOR_SELF)


# ---- 保存の部品 ----


def _log_result(conn: sqlite3.Connection, role: str, result, target: str | None = None) -> None:
    db.log_ai_call(
        conn,
        role=role,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        target=target,
        seconds=result.seconds,
    )


def _log_spent(conn: sqlite3.Connection, role: str, error: Exception, target: str | None = None) -> None:
    """答えが得られなかった呼び出しも、費用がかかったので記録する。"""
    db.log_ai_call(
        conn,
        role=role,
        model=error.model,
        input_tokens=error.input_tokens,
        output_tokens=error.output_tokens,
        target=target,
    )


_SPENT_ERRORS = (ai.AIResultError, ai.AIRefusedError)
_ALL_AI_ERRORS = (*_SPENT_ERRORS, ai.AIUnavailableError, config.MissingApiKeyError)


def _say(conn, room: Room, conversation_id: int, place: Place, text: str, now: datetime, question_log_id=None):
    return room_store.say_in_ledger(
        conn,
        room_id=room.id,
        conversation_id=conversation_id,
        place=place,
        is_twin=True,
        text=text,
        sent_at=now,
        question_log_id=question_log_id,
    )


def _ask(
    conn,
    room: Room,
    conversation_id: int,
    place: Place,
    question: str,
    theme: Theme | None,
    now: datetime,
) -> None:
    """問いを発言として保存し、質問の記録に入れる。"""
    with ledger.atomic(conn):
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation_id,
                speaker_is_twin=True,
                text=question,
                spoken_at=now,
                place=place,
            ),
        )
        question_id = ledger.add_asked_question(
            conn,
            question=question,
            theme=theme,
            asked_on=now.date(),
            conversation_id=conversation_id,
            room_id=room.id,
            utterance_id=utterance.id,
        )
        room_store.insert_message(
            conn,
            room_id=room.id,
            is_twin=True,
            utterance_id=utterance.id,
            conversation_id=conversation_id,
            question_log_id=question_id,
            sent_at=now.isoformat(),
        )


# ---- モデルを選ぶ ----


@dataclass(frozen=True)
class ChosenTurn:
    """答えと、モデルを Sonnet 5.5 へ選択した理由。Haiku 5.5 の答えを使うときの理由は None。"""

    result: ai.AIResult
    reason: twin_model.DeepReason | None


def _shown_reply(reply: str, heard: str, care: CareKind) -> str:
    """AI の返事を決まりに通した文を返す。全部の文が外れたら空を返し、呼ぶ側が決まった文に替える。"""
    trimmed = trim_reply(reply, heard)
    return drop_referrals(trimmed) if care is CareKind.HEALTH else trimmed


def choose_turn(
    *,
    system: str,
    text: str,
    said: str,
    keyword_care: CareKind,
    record: Callable[..., None],
    client=None,
    cached_system: str | None = None,
    people: tuple[str, ...] = (),
    has_index: bool = False,
) -> ChosenTurn:
    """発言の中身をプログラムが判断してモデルを選び、1回の呼び出しで返事を書かせる。

    判断の決まりと理由は app/twin_model.py にある。record は AI を呼ぶたびに費用を残すために呼ぶ。
    選んだモデルが答えられないときは、もう一方のモデルの答えで返す。本人を待たせて何も返さないより、
    言葉の質が少し落ちても返すほうがよいため。API に届かないときは、もう一方でも届かないので例外のまま上げる。
    関連するテスト：tests/test_twin_model.py
    """

    def ask(*, deep: bool):
        role = config.TWIN_DEEP_ROLE if deep else "ツインの会話"
        try:
            result = ai.twin_turn(system=system, text=text, client=client, deep=deep, cached_system=cached_system)
        except _SPENT_ERRORS as error:
            record(role, error=error)
            raise
        record(role, result=result)
        return result

    reason = twin_model.reason_before_answer(said, keyword_care, people)
    if reason is None and has_index:
        reason = twin_model.DeepReason.HEART
    if reason is not None:
        try:
            return ChosenTurn(ask(deep=True), reason)
        except _SPENT_ERRORS:
            return ChosenTurn(ask(deep=False), None)
    try:
        return ChosenTurn(ask(deep=False), None)
    except _SPENT_ERRORS:
        return ChosenTurn(ask(deep=True), twin_model.DeepReason.BAD_ANSWER)


# ---- 本人とツインの部屋 ----


# 亡くなった方の話で、返事が出せなかったときの受け止め。「そうでしたか。」だけでは、重い話に冷たく聞こえた（tyumajinさんの指摘）
_STAY_RECEIPT = "そうなんですね。話してくださって、ありがとうございます。"


def talks_of_gone(about_people: list[str], gone: tuple[str, ...]) -> bool:
    """AI が読んだ「だれの話か」に、名簿の亡くなった方が入っているかを返す。"""
    return any(name in person for person in about_people for name in gone)


def _pass_rules(
    text: str, *, heard: str, care: CareKind, sources: list[str], negative: bool, gone: tuple[str, ...]
) -> tuple[str, str, list[dict[str, str]]]:
    """AI の返事を、本人との会話の決まりにすべて通す。残った文、言葉の決まりを通した文、落とした文と理由を返す。

    決まりに合わない文は、文ごとに落とす。亡くなった方と何かをする案と、本人が言っていない死の言葉も文ごとに落とし、
    ほかの受け止めの文は残す（訂正しない決まりと、話さない自由。原則6）。本人の言葉を伝える形で原文と照らせない文と、
    体や命の場面の受け止めに混ざった問いは、文の切れ目が読めないので返事ごと出さない。
    関連するテスト：tests/test_heart.py の test_亡くなった方の文だけを落とし_ほかの受け止めは残す
    """
    shown = _shown_reply(text, heard, care)
    kept, checks = heart.check_reply(shown, sources, negative=negative) if shown else ("", [])
    if kept:
        kept, gone_notes = heart.drop_gone_sentences(kept, list(gone), heard)
        checks = [*checks, *gone_notes]
    if kept and care is not CareKind.NONE and _HIDDEN_QUESTION.search(kept):
        checks = [*checks, {"文": kept, "理由": "受け止めに混ざった問い"}]
        kept = ""
    if kept and not words_are_real(kept, sources):
        checks = [*checks, {"文": kept, "理由": "原文と照らせない伝え方"}]
        kept = ""
    return kept, shown, checks


def respond_in_twin_room(
    conn: sqlite3.Connection,
    *,
    room: Room,
    self_user: User,
    message: RoomMessage,
    now: datetime,
    photos_dir: Path,
    client=None,
    ask: bool = True,
) -> None:
    """本人の発言に返事をする。AI が失敗したら例外のまま上げる。本人の発言は保存済み。

    返事は1回の呼び出しで書く（choose_turn）。名簿と心の目次（app/heart.py）をプロンプトキャッシュに載せ、
    今の会話、前の話の要約、前に聞いた本人の言葉と一緒に渡す。書いた返事は、出す前にプログラムの決まりに通す（_pass_rules）。
    問いの数、お休みの話題、気持ちの決めつけ、診断や服薬の指示、原文と照らせない伝え方は、ここで落とす。
    AI が亡くなった方の話と読んだ回は、問いを出さない（原則6）。
    返事の処理の記録は推論の記録に残し、あとからたどれる（fiction/twin_trace.py）。
    モデルの選び方と実測：docs/tools.md の「ツインとの会話エンジン」。関連するテスト：tests/test_heart.py、tests/test_twin.py
    """
    started = time.monotonic()
    conversation_id = message.conversation_id
    said = memory.message_text(conn, message)
    keyword_care = detect_care(said)
    context = memory.build_twin_context(conn, room, conversation_id, self_user, now.date())
    system = prompts.twin_system(self_user.display_name)
    # 名簿と心の目次。プロンプトキャッシュに載せる。記録が増えても読む量は上限まで（app/heart.py）
    block = heart.index_block(conn)
    gone = tuple(heart.gone_names(conn))
    photo = photos.get_photo(conn, message.photo_id) if message.photo_id else None
    target = f"会話 {conversation_id}"
    # 返事の処理の記録。何を書き、何が落ちたかを、あとから確かめるため（fiction/twin_trace.py で読む）
    trace: dict = {}
    written_at = time.monotonic()

    def record(role: str, *, result=None, error=None) -> None:
        if error is not None:
            _log_spent(conn, role, error, target)
        else:
            _log_result(conn, role, result, target)

    deep_reason = None
    try:
        if photo is not None:
            try:
                result = ai.twin_photo_turn(
                    photo,
                    photos.read_photo_bytes(photos_dir, photo),
                    system=system,
                    text=prompts.twin_user_text(**context) + "\n\n" + prompts.TWIN_PHOTO_INSTRUCTION,
                    client=client,
                    cached_system=block.text,
                )
            except _SPENT_ERRORS as error:
                record("写真の問い", error=error)
                raise
            record("写真の問い", result=result)
        else:
            chosen = choose_turn(
                system=system,
                text=prompts.twin_user_text(**context),
                said=said,
                keyword_care=keyword_care,
                record=record,
                client=client,
                cached_system=block.text,
                people=heart.people_words(conn),
                has_index=bool(block.numbers),
            )
            result, deep_reason = chosen.result, chosen.reason
    except _ALL_AI_ERRORS:
        if keyword_care is CareKind.NONE:
            raise
        # AI が断ったり答えなかったりしても、命や体に関わる言葉には、つなぎ先を必ず返す
        with ledger.atomic(conn):
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, _CARE_RECEIVE[keyword_care], now)
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, _CARE_LINES[keyword_care], now)
            _answer_pending(conn, conversation_id, None)
        return
    turn = result.value
    ai_care = settled_ai_care(turn.care, said)
    care = _stronger(ai_care, keyword_care)
    question = turn.question
    about_gone = talks_of_gone(turn.about_people, gone)
    # 問いを落とした理由。話した内容が分かるので、架空用のログにだけ入る（app/devlog.py）。
    dropped_because = None
    if care is not CareKind.NONE or says_goodbye(said):
        question = None
        dropped_because = "体や命の場面か、終えたそう"
    elif about_gone:
        # 亡くなった方の話には、問いを出さない。つらい出来事は AI から聞かない（原則6）
        question = None
        dropped_because = "亡くなった方の話"
    elif not ask:
        # 架空の記録を流す道具が、本人が話を続けない日に指定する（app/main.py の twin_may_ask）
        question = None
        dropped_because = "問わない日"
    elif question:
        resting = memory.resting_themes(conn)
        asked = memory.asked_questions(conn, room.id)
        if not question_is_allowed(question, turn.question_theme, resting=resting, asked=asked):
            question = None
            dropped_because = "聞かない話題か、同じ問い"
        elif memory.questions_in_a_row(conn, conversation_id) >= _MAX_QUESTIONS_IN_A_ROW:
            # 問いが続くと尋問のようになるので、受け止めるだけの回を入れる
            question = None
            dropped_because = "問いが続いた"
    # 本人の言葉を伝える形の文は、原文と照らせるものだけを通す
    trace["返事"] = {
        "モデル": result.model,
        "モデルを選んだ理由": deep_reason.value if deep_reason is not None else None,
        "入力": result.input_tokens,
        "出力": result.output_tokens,
        "ms": round((time.monotonic() - written_at) * 1000),
        "だれの話か": turn.about_people,
        "読み": turn.reading,
        "否定的な話": turn.negative,
        "使った番号": turn.used_numbers,
        "書いた返事": turn.reply,
        "書いた問い": turn.question,
    }
    sources = [context["transcript"], *context["self_words"], *block.words]
    if photo is not None:
        sources.append(result.value.photo_description)
    if question and not words_are_real(question, sources):
        question = None
        dropped_because = "原文と照らせない"
    heard = said + (result.value.photo_description if photo is not None else "")
    trimmed, shown_by_rules, checks = _pass_rules(turn.reply, heard=heard, care=care, sources=sources, negative=turn.negative, gone=gone)
    if care is not CareKind.NONE:
        fallback = _CARE_RECEIVE[care]
    else:
        fallback = _STAY_RECEIPT if about_gone else _HEARD_RECEIPT
    reply = trimmed or fallback
    with ledger.atomic(conn):
        _answer_pending(conn, conversation_id, turn.previous_question_result)
        if photo is not None:
            conn.execute(
                "UPDATE photos SET description = ? WHERE id = ?",
                (result.value.photo_description[:120], photo.id),
            )
        _say(conn, room, conversation_id, Place.DIARY_AND_TALK, reply, now)
        for line in care_lines(ai_care, keyword_care):
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, line, now)
        if question:
            _ask(conn, room, conversation_id, Place.DIARY_AND_TALK, question, turn.question_theme, now)
            memory.mark_candidate_used(conn, question, now.date())
        trace["決まり"] = {
            "決まりを通した文": shown_by_rules,
            "落とした文": checks,
            "画面の返事": reply,
            "画面の問い": question,
            "問いを落とした理由": dropped_because,
        }
        if turn.reading or block.numbers:
            # 読みは仮説として残す。画面には出さない。渡した番号だけを根拠にする
            db.insert_row(
                conn,
                "inference_logs",
                {
                    "kind": InferenceKind.TWIN_READING.value,
                    "question": said,
                    "result": json.dumps(
                        {
                            "about_people": turn.about_people,
                            "reading": turn.reading,
                            "negative": turn.negative,
                            "trace": trace,
                        },
                        ensure_ascii=False,
                    ),
                    "element_ids": [],
                    "statement_ids": heart.used_statement_ids(block, turn.used_numbers),
                    "model": result.model,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "created_at": now.isoformat(),
                },
            )
    notes = []
    if deep_reason is not None:
        notes.append(devlog.MODEL_CHOSEN)
    if turn.question and not question:
        notes.append(devlog.QUESTION_DROPPED)
    if not trimmed or reply != trimmed:
        notes.append(devlog.REPLY_REPLACED)
    elif "".join(trimmed.split()) != "".join(turn.reply.split()):
        notes.append(devlog.REPLY_TRIMMED)
    # 前の話を読めているかは、要約と本人の言葉の文字数で分かる。care は話の内容が分かるので text に入れる。
    devlog.record(
        devlog.TWIN_TURN,
        outcome=devlog.ASKED if question else devlog.NOT_ASKED,
        notes=notes,
        ms=devlog.since(started),
        said_chars=len(said),
        reply_chars=len(reply),
        question_chars=len(question or ""),
        summaries_chars=sum(len(line) for line in context["summaries"]),
        self_words_chars=sum(len(line) for line in context["self_words"]),
        transcript_chars=len(context["transcript"]),
        text={
            "発言": said,
            "読み": turn.reading,
            "返事の元": turn.reply,
            "返事": reply,
            "問いの元": turn.question,
            "問い": question,
            "問いを落とした理由": dropped_because,
            "care": care.value,
            "モデルを選んだ理由": deep_reason.value if deep_reason is not None else None,
            "写真の説明": result.value.photo_description if photo is not None else None,
        },
    )


def _answer_pending(conn: sqlite3.Connection, conversation_id: int, result: QuestionResult | None) -> None:
    """直前の問いに結果を書く。本人が断ったと読めたら、その話題はお休みになる。"""
    conn.execute(
        "UPDATE question_logs SET result = ? WHERE conversation_id = ? AND result IS NULL AND asked_on IS NOT NULL",
        ((result or QuestionResult.ANSWERED).value, conversation_id),
    )


def close_in_twin_room(
    conn: sqlite3.Connection,
    *,
    room: Room,
    self_user: User,
    conversation: Conversation,
    now: datetime,
    client=None,
) -> None:
    """「今日はここまで」の返事を作る。短いお礼と、今日の暮らしに役立つ一言。重い話で終わった日は受け止めだけにする。

    関連するテスト：tests/test_twin.py の test_重い話で終わった日は今日の一言を出さず受け止めだけを返す
    """
    lines = memory.conversation_lines(
        conn,
        conversation.id,
        self_user,
        max_lines=memory.LIMITS["current_utterances"],
        max_chars=memory.LIMITS["current_chars"],
        with_photos=True,
        with_added=True,
    )
    if not lines:
        return
    target = f"会話 {conversation.id}"
    heard = "\n".join(lines)
    text = f"## 今日の日付\n{memory.date_label(now.date())}\n\n## 今の時刻\n{now.hour}時{now.minute}分\n\n## 今日の話\n{heard}"
    system = prompts.twin_closing_system(self_user.display_name)
    gone = _gone_names(conn)

    def tip_is_bad(tip: str) -> str | None:
        return "薬や受診の話は書かないでください。" if advises_on_medicine(tip) else tip_problem(tip, now=now, heard=heard, gone=gone)

    def ask(*, deep: bool, feedback: str | None = None):
        role = config.TWIN_DEEP_ROLE if deep else "ツインの会話"
        asking = f"{text}\n\n## 前の一言の直すところ\n{feedback}" if feedback else text
        try:
            result = ai.twin_closing(system=system, text=asking, client=client, deep=deep)
        except _SPENT_ERRORS as error:
            _log_spent(conn, role, error, target)
            raise
        _log_result(conn, role, result, target)
        return result

    closing = ask(deep=False).value
    problem = tip_is_bad(closing.tip)
    if problem:
        # 答えをプログラムが判断し、モデルを Sonnet 5.5 へ選択して書き直す。それでも助言なら一言を出さない
        try:
            closing = ask(deep=True, feedback=problem).value
        except _ALL_AI_ERRORS:
            pass
    with ledger.atomic(conn):
        _say(conn, room, conversation.id, Place.DIARY_AND_TALK, closing.reply, now)
        # 重い話で終わった日は tip が空。受け止めの1文だけを返す
        if closing.tip and not tip_is_bad(closing.tip):
            _say(conn, room, conversation.id, Place.DIARY_AND_TALK, closing.tip, now)


# ---- グループ ----


# スタンプだけの発言。「（スタンプ：太陽）」の形で保存され、画面が絵にする（docs/api.md の「スタンプ」）
STAMP_ONLY = re.compile(r"\s*(?:（スタンプ：(?:太陽|星|花|月)）\s*)+")


def stamps_only(text: str) -> bool:
    """スタンプだけの発言かを返す。言葉がないので、AI に読ませる材料にも、問いへの答えにもしない。

    スタンプから、天気や気持ちを AI が作った（実測）。関連するテスト：tests/test_stamps.py
    """
    return bool(text) and STAMP_ONLY.fullmatch(text) is not None


def group_turn(
    conn: sqlite3.Connection,
    *,
    room: Room,
    conversation_id: int,
    trigger: RoomMessage,
    now: datetime,
    client=None,
) -> None:
    """グループのツインが書く。書くのは、@ツイン で呼ばれたときと、家族や知人の発言のあとの問いだけ。"""
    self_user = db.self_user(conn)
    sender = db.get_user(conn, trigger.sender_user_id)
    said = memory.message_text(conn, trigger)
    called = mentions_twin(said)
    pending = _pending_in_room(conn, room.id)
    if stamps_only(said):
        # スタンプだけでは AI を呼ばない。本人のスタンプだけで、待っている問いを答えたことにもしない
        return
    if sender.role is Role.SELF:
        # 本人が書いたら、待っている問いには答えたことにする
        with conn:
            conn.execute(
                "UPDATE question_logs SET result = ? WHERE room_id = ? AND result IS NULL AND asked_on IS NOT NULL",
                (QuestionResult.ANSWERED.value, room.id),
            )
        pending = False
    if called and asks_for_self(said):
        _say(conn, room, conversation_id, Place.GROUP, prompts.CANNOT_ANSWER_FOR_SELF, now)
        return
    if not called and (sender.role is Role.SELF or pending):
        return
    # 渡すのは、ツインが入ったあとのこの部屋の発言だけ。写真の説明も渡さない。
    people = room_store.members_of(conn, room.id)
    lines = memory.conversation_lines(
        conn,
        conversation_id,
        self_user,
        max_lines=memory.LIMITS["current_utterances"],
        max_chars=memory.LIMITS["current_chars"],
    )
    roster = "、".join(f"{person.display_name}（{person.role.value}）" for person in people)
    scene = (
        f"{sender.display_name}が @ツイン で呼びました。"
        if called
        else f"{sender.display_name}の発言のあとです。呼ばれてはいません。"
    )
    target = f"部屋 {room.id}"
    try:
        result = ai.group_turn(
            system=prompts.group_system(self_user.display_name),
            text=f"## 部屋の人\n{roster}\n\n## あなたが入ったあとの発言\n" + "\n".join(lines) + f"\n\n## 今の場面\n{scene}",
            client=client,
        )
    except _SPENT_ERRORS as error:
        _log_spent(conn, "グループのツイン", error, target)
        raise
    _log_result(conn, "グループのツイン", result, target)
    turn = result.value
    # AI を待つ間に本人がツインをはずしたら、何も書かない
    presence = room_store.active_presence(conn, room.id)
    if presence is None or presence.conversation_id != conversation_id:
        return
    if turn.kind is GroupTurnKind.CANNOT or (
        turn.kind is GroupTurnKind.ANSWER and called and reports_self(turn.text, self_user)
    ):
        # 本人の考えや発言を伝える答えは、代わりに答えたことになるので書かない
        _say(conn, room, conversation_id, Place.GROUP, prompts.CANNOT_ANSWER_FOR_SELF, now)
    elif turn.kind is GroupTurnKind.ANSWER and called:
        _say(conn, room, conversation_id, Place.GROUP, turn.text, now)
    elif turn.kind is GroupTurnKind.QUESTION and not pending and not called:
        if not any(word in turn.text for word in _NOT_TO_ASK) and not memory.is_repeated(
            turn.text, memory.asked_questions(conn, room.id)
        ):
            _ask(conn, room, conversation_id, Place.GROUP, turn.text, None, now)


def _pending_in_room(conn: sqlite3.Connection, room_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM question_logs WHERE room_id = ? AND result IS NULL AND asked_on IS NOT NULL LIMIT 1",
        (room_id,),
    ).fetchone()
    return row is not None


# ---- スレッド ----


def thread_turn(
    conn: sqlite3.Connection, *, letter_id: int, author_user_id: int, now: datetime, client=None, ask: bool = True
) -> None:
    """コメントが付いたら、本人への問いを1つだけ書く。本人が答えるまで、次の問いは書かない。"""
    self_user = db.self_user(conn)
    latest = conn.execute(
        "SELECT body FROM comments WHERE letter_id = ? AND author_user_id = ? ORDER BY rowid DESC LIMIT 1",
        (letter_id, author_user_id),
    ).fetchone()
    if latest is not None and stamps_only(latest["body"]):
        # スタンプだけのコメントでは問いを書かない。本人のスタンプだけで、問いを答えたことにもしない
        return
    if author_user_id == self_user.id:
        with conn:
            conn.execute(
                "UPDATE question_logs SET result = ? WHERE letter_id = ? AND result IS NULL AND asked_on IS NOT NULL",
                (QuestionResult.ANSWERED.value, letter_id),
            )
        return
    if not ask:
        return
    pending = conn.execute(
        "SELECT 1 FROM question_logs WHERE letter_id = ? AND result IS NULL AND asked_on IS NOT NULL LIMIT 1",
        (letter_id,),
    ).fetchone()
    if pending:
        return
    # 渡すのは、このスレッドに見えている便りとコメントだけ
    letter_body, lines = memory.thread_lines(conn, letter_id)
    asked = [
        row["question"]
        for row in conn.execute("SELECT question FROM question_logs WHERE letter_id = ?", (letter_id,))
    ]
    target = f"便り {letter_id}"
    try:
        result = ai.thread_question(
            system=prompts.thread_system(self_user.display_name),
            text=f"## 便り\n{letter_body}\n\n## コメント\n" + "\n".join(lines),
            client=client,
        )
    except _SPENT_ERRORS as error:
        _log_spent(conn, "スレッドの問い", error, target)
        raise
    _log_result(conn, "スレッドの問い", result, target)
    question = result.value.question
    if not question or any(word in question for word in _NOT_TO_ASK) or memory.is_repeated(question, asked):
        return
    with ledger.atomic(conn):
        comment = ledger.add_comment(
            conn,
            ledger.CommentDraft(letter_id=letter_id, body=question, commented_at=now, is_twin_question=True),
        )
        ledger.add_asked_question(
            conn,
            question=question,
            theme=None,
            asked_on=now.date(),
            letter_id=letter_id,
            utterance_id=comment.utterance_id,
        )
