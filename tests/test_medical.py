"""医療の決まり（原則17）のテスト。

役割：してはいけないのは病気の診断と服薬の指示の2つだけ、という決まりを、語の確かめが守っていることを確かめる。
4つの極：1. 書く・話す、4. 10年後に代弁する。
守る原則：17。
"""

from __future__ import annotations

import pytest

from app import medical


@pytest.mark.parametrize(
    "text",
    [
        "避難中は飲まなくても大丈夫です。",
        "血圧が高い方ですね。",
        "先に診てもらう必要があります。",
        "〇〇錠のことですね。",
        "その薬は量を減らしてください。",
    ],
)
def test_診断と服薬の指示の言い切りは見つける(text):
    assert medical.orders_in(text)


@pytest.mark.parametrize(
    "text",
    [
        "血圧の薬を飲み忘れないよう、朝の湯のみの横に置いています。",
        "減塩のみそ汁でも、だしを濃くすると味が立ちます。",
        "腰が冷えるときは、温めてから動くと楽なことがあります。",
        "飲まなくても大丈夫かは、お医者さんか薬剤師さんに聞いてみてください。",
        "井ノ口先生は、病名を本人にも分かる言葉で話してくれる。",
    ],
)
def test_健康の話題の語だけでは見つけない(text):
    assert medical.orders_in(text) == []


def test_決まりの文は診断と服薬の指示の2つを書き_そのほかの健康の話はしてよいと書く():
    assert "病気の診断と、服薬の指示はしません" in medical.RULE
    assert "急ぐかどうか" in medical.RULE
    assert "薬の名前を推して直す" in medical.RULE
    assert "してかまいません" in medical.RULE
