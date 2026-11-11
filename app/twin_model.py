"""ツインの会話と言明への分割で、どのモデルに任せるかをプログラムが判断する。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：発言の中身を見て、必要なときだけモデルを Sonnet 5.5 へ選択する。
4つの極：1. 書く・話す（ツインの会話）、3. 読み解き、積み上げる（言明への分割）。
守る原則：1（判断するのは本人）。本人の言葉の照合と同意の絞り込みはどのモデルでも同じで、ここでは変えない。

ツインの会話について、なぜこう作ったか：
- ふだんの話は Haiku 5.5 で足りる。本人を待たせず、1回の返事の費用が Sonnet 5.5 の約20分の1で済む。
- 同じ20件を比べると、相談、体の話、人生に関わる話、気持ちの話で差が出た。Haiku 5.5 は暮らしの案を添えない回があり、
  施設の話に「急いで答えを出さなくてもいいと思います」と意見に近い文を書いた回もあった（docs/tools.md）。
- 判断は語と文の形を見るプログラムに置く。AI に判断させると、毎回の費用と待ち時間が増え、判断もぶれるため。

判断は答えの前に1回だけ行う。発言の中身で選んだモデルに、1回の呼び出しで返事を書かせる。

語の一覧は、Sonnet 5.5 へ回す場面を見つけるためのもの。語に当たらなくても Haiku 5.5 が答えるだけで、
誤っても言葉の質と費用の差で済む。安全のつなぎ先はここではなく app/twin.py の detect_care と care_lines が決める。

言明への分割について、なぜこう作ったか（reason_to_split_deep）：
- 本人の言葉は作り直せない。比べで Haiku 5.5 は発言を1つ落とし、言い切りの文を作った。回数の多い短い話は Haiku 5.5 で足りる。
- 同じ3単位の比べで Sonnet 5.5 は費用が約12倍（$0.0059 と $0.0683）、時間は短かった（39.0秒と23.2秒）。
  全部を Sonnet 5.5 にせず、本人が長く話した回と、人生、体、気持ちの語がある回だけにする。

全体の表と実測：docs/tools.md の「ツインの会話でモデルを選ぶ」「重い言葉の分割でモデルを選ぶ」
関連するテスト：tests/test_twin_model.py
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum

from app.schemas import CareKind, Place


class DeepReason(StrEnum):
    """モデルを Sonnet 5.5 へ選択した理由。話の内容が分かるので、自分用のログには書かない（app/devlog.py）。"""

    CARE = "体や命の場面"
    CONSULT = "相談"
    BODY = "体の話"
    LIFE = "人生に関わる話"
    PERSON = "人の話"
    HEART = "心の目次を踏まえて返す"
    FEELING = "気持ちの話"
    LONG = "本人が長く話した"
    BAD_ANSWER = "Haiku が決まりに合う答えを出せなかった"


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

# 言明への分割で Sonnet 5.5 を選ぶ発言の長さ（字数）。
SPLIT_DEEP_CHARS = 80
# 言明への分割で Sonnet 5.5 を選ぶ話の種類。相談は入れない。「？」だけで選ばないため。
_SPLIT_DEEP_REASONS = frozenset({DeepReason.CARE, DeepReason.BODY, DeepReason.LIFE, DeepReason.FEELING})


def reason_before_answer(said: str, keyword_care: CareKind, people: Iterable[str] = ()) -> DeepReason | None:
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
    # 名簿の人が出た話。心の目次を踏まえて返すのに、言葉の加減が要る（app/heart.py）
    if any(word in said for word in people):
        return DeepReason.PERSON
    return None


def reason_to_split_deep(
    place: str, self_words: Iterable[str], care_of: Callable[[str], CareKind]
) -> DeepReason | None:
    """言明への分割で、発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択する。理由を返す。なければ None。

    対象はツインとの部屋の会話だけ。スレッドのコメントとグループは Haiku 5.5 のままにする。
    本人の発言のどれか1つが長いか重い語を含めば選ぶ。AI には判断させない。毎回の費用が増え、判断もぶれるため。
    care_of は app/twin.py の detect_care。twin.py がこのファイルを読み込むので、ここからは読み込まず受け取る。
    関連するテスト：tests/test_twin_model.py
    """
    if place != Place.DIARY_AND_TALK:
        return None
    for said in self_words:
        if len(said) >= SPLIT_DEEP_CHARS:
            return DeepReason.LONG
        reason = reason_before_answer(said, care_of(said))
        if reason in _SPLIT_DEEP_REASONS:
            return reason
    return None
