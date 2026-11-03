"""ツインの会話で、どのモデルに答えさせるかをプログラムが判断する。

役割：発言の中身と Haiku 5.5 の答えを見て、必要なときだけモデルを Sonnet 5.5 へ選択する。
4つの極：1. 書く・話す。
守る原則：1（判断するのは本人）。本人の言葉の扱いと同意には関わらない。

なぜこう作ったか：
- ふだんの話は Haiku 5.5 で足りる。本人を待たせず、1回の返事の費用が Sonnet 5.5 の約20分の1で済む。
- 同じ20件を比べると、相談、体の話、人生に関わる話、気持ちの話で差が出た。Haiku 5.5 は暮らしの案を添えない回があり、
  施設の話に「急いで答えを出さなくてもいいと思います」と意見に近い文を書いた回もあった（docs/tools.md）。
- 判断は語と文の形を見るプログラムに置く。AI に判断させると、毎回の費用と待ち時間が増え、判断もぶれるため。

判断は2段ある。
- 答えの前：発言の中身で判断する。Sonnet 5.5 が要ると分かっているので、Haiku 5.5 を呼ばずに費用と時間を省く。
- 答えの後：Haiku 5.5 の答えで判断する。言い直しだけの返事と、決まりを通すと文が残らない返事は送り直す。

語の一覧は、Sonnet 5.5 へ回す場面を見つけるためのもの。語に当たらなくても Haiku 5.5 が答えるだけで、
誤っても言葉の質と費用の差で済む。安全のつなぎ先はここではなく app/twin.py の detect_care と care_lines が決める。

全体の表と実測：docs/tools.md の「ツインの会話でモデルを選ぶ」
関連するテスト：tests/test_twin_model.py
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.schemas import CareKind


class DeepReason(StrEnum):
    """モデルを Sonnet 5.5 へ選択した理由。話の内容が分かるので、自分用のログには書かない（app/devlog.py）。"""

    CARE = "体や命の場面"
    CONSULT = "相談"
    BODY = "体の話"
    LIFE = "人生に関わる話"
    FEELING = "気持ちの話"
    BAD_ANSWER = "Haiku が決まりに合う答えを出せなかった"
    NOTHING_LEFT = "決まりを通すと文が残らなかった"
    THIN = "言い直しだけの返事"
    HEALTH_TIP = "今日の一言が健康の助言になった"


# 本人がツインに何かを聞いている言葉。案を添えるか、決めるのは本人だと返すかの読み分けが要る。
_CONSULT_WORDS = (
    "どうしたら",
    "どうすれば",
    "どうしよう",
    "何にしよう",
    "べきか",
    "いいかな",
    "いいのかな",
    "どっち",
    "どちら",
    "困って",
    "困った",
    "迷って",
    "決めてくれ",
    "決めてほしい",
    "決めてください",
    "？",
    "?",
)
# 体の話。治し方を言わず、いたわる言葉だけを返すのに、言葉の加減が要る。
_BODY_WORDS = ("痛", "具合", "体調", "病院", "病気", "薬", "眠れな", "だるい", "しんどい")
# 人生に関わる話。意見に見える文を書かず、本人に決めてもらう話として扱う。
_LIFE_WORDS = ("施設", "入院", "介護", "手放", "遺言", "葬", "墓", "相続", "お金", "最期", "延命", "亡くな", "いなくな", "看取", "死")
# 気持ちの話。決めつけず、言い直しでもなく返すのに、言葉の加減が要る。
_FEELING_WORDS = (
    "寂し",
    "さびし",
    "悲し",
    "つらい",
    "辛い",
    "悔し",
    "不安",
    "心配",
    "怖",
    "後悔",
    "申し訳",
    "情けな",
    "腹が立",
    "うれし",
    "嬉し",
)

_THIN_ENDS = ("のですね", "んですね")
_SENTENCE = re.compile(r"[^。！!\n]+")


def reason_before_answer(said: str, keyword_care: CareKind) -> DeepReason | None:
    """発言の中身を見て、最初から Sonnet 5.5 に答えさせる理由を返す。なければ None。"""
    if keyword_care is not CareKind.NONE:
        return DeepReason.CARE
    for words, reason in (
        (_LIFE_WORDS, DeepReason.LIFE),
        (_BODY_WORDS, DeepReason.BODY),
        (_FEELING_WORDS, DeepReason.FEELING),
        (_CONSULT_WORDS, DeepReason.CONSULT),
    ):
        if any(word in said for word in words):
            return reason
    return None


def is_thin(reply: str) -> bool:
    """どの文も「〜のですね」で終わる、言い直しだけの返事かを返す。話の中身に反応する言葉がない。"""
    sentences = [sentence.strip() for sentence in _SENTENCE.findall(reply) if sentence.strip()]
    return bool(sentences) and all(sentence.endswith(_THIN_ENDS) for sentence in sentences)


def reason_after_answer(shown_reply: str) -> DeepReason | None:
    """Haiku 5.5 の答えを決まりに通した返事を見て、Sonnet 5.5 へ送り直す理由を返す。なければ None。"""
    if not shown_reply:
        return DeepReason.NOTHING_LEFT
    if is_thin(shown_reply):
        return DeepReason.THIN
    return None
