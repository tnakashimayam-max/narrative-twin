"""本人の言葉の抜き出しの切り方のテスト。

役割：文の切れ目で始まり終わる抜き出しだけが通ることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：9（本人の言葉は、一字も変えずに届ける）。
"""

from __future__ import annotations

import pytest

from app import ledger
from app.ledger import LedgerError
from app.verbatim import find_sentence_span, quote_problem

PASSES = [
    ("毎朝この庭を見るのが楽しみなんです。", "毎朝この庭を見るのが楽しみなんです。"),
    ("最初は材木屋の手伝いをしていた。そのあと製材所に移った。", "そのあと製材所に移った。"),
    ("和子に無理をさせるくらいなら、人の手を借りる", "和子に無理をさせるくらいなら、人の手を借りる"),
    ("紫陽花が咲きました！\n今年は色が濃い", "今年は色が濃い"),
    ("人に頼るのも悪くない。", "人に頼るのも悪くない"),
    ("朝はお茶を飲みます。庭も見ます。夜は早く寝ます。", "朝はお茶を飲みます。庭も見ます。"),
    ("庭は好き、か。庭は好き。", "庭は好き。"),
    ("「毎朝この庭を見るのが楽しみなんです」", "毎朝この庭を見るのが楽しみなんです"),
    ("  庭が好きです。  ", "庭が好きです。"),
    # 読点の前後で意味が決まる文は、全体なら入る
    ("庭が好きなのではなく、妻と過ごした場所だから大切", "庭が好きなのではなく、妻と過ごした場所だから大切"),
    ("「庭が好きなのではなく、妻と過ごした場所だから大切」", "庭が好きなのではなく、妻と過ごした場所だから大切"),
    # かっこが閉じたあとの本人の言葉は入る
    ("和子は「無理です。」と言った。私はそうは思わない。", "私はそうは思わない。"),
    # かっこが閉じている本文は入る
    ("和子は「もう無理です。施設に入りましょう。」と言った。", "和子は「もう無理です。施設に入りましょう。」と言った。"),
]

STOPS = [
    ("家で暮らしたいとは思いません。", "家で暮らしたい"),
    ("施設には行きたくないです。", "施設には行きたく"),
    ("本当は家にいたいけど、和子に迷惑はかけたくない。", "本当は家にいたいけど"),
    ("本当は家にいたいけど、和子に迷惑はかけたくない。", "和子に迷惑はかけたくない。"),
    ("妻は「施設に入ったほうがいい」と言う。", "施設に入ったほうがいい"),
    ("家に帰りたい？", "家に帰りたい"),
    ("すばらしい！", "すばらしい"),
    ("もういいよ…でも本当は違う。", "もういいよ"),
    ("ありません。", "ありません。 ではない"),
    # 文の途中の改行は切れ目にならない
    ("家で暮らしたい\nとは思いません。", "家で暮らしたい"),
    ("本当は家にいたいけど、\n和子に迷惑はかけたくない。", "和子に迷惑はかけたくない。"),
    # かぎかっこの中の他人の言葉は本人の言葉にならない
    ("妻は言った。「施設に入ったほうがいい」。本当にそうか。", "施設に入ったほうがいい"),
    ("妻は言った。「施設に入ったほうがいい」。本当にそうか。", "「施設に入ったほうがいい」。"),
    ("「施設に入ったほうがいい」\nと妻は言う。", "施設に入ったほうがいい"),
    # 本文の始まりより前に閉じていないかっこがあれば、他人の言葉の中にいる
    ("和子は「もう無理です。施設に入りましょう。」と言った。", "施設に入りましょう。"),
    ("和子は「もう無理です。施設に入りましょう。」と言った。", "施設に入りましょう"),
    ("娘は『帰ってきて。家にいて。』と言う。", "家にいて。"),
    ("（医師の話。もう歩けません。）と聞いた。", "もう歩けません。"),
    # 先頭と末尾がかっこでも、最初のかっこが途中で閉じれば、発言全体を囲んでいない
    ("「帰りましょう。」と妻。「施設は嫌だ。行きたくない」", "行きたくない"),
    ("「帰りましょう。」と妻。「施設は嫌だ。行きたくない」", "帰りましょう。"),
    # 囲んでいないと分かっても、先頭と末尾のかっこを含む本文は今までどおり入らない
    ("「帰りましょう。」と妻。「施設は嫌だ。行きたくない」", "「帰りましょう。」と妻。"),
    # 本文の中でかっこが閉じないものは入らない
    ("和子は「もう無理です。施設に入りましょう。」と言った。", "和子は「もう無理です。"),
    ("妻は「帰ろう。」と言った。私は「嫌だ。行かない。」と答えた。", "私は「嫌だ。"),
]


@pytest.mark.parametrize(
    "body", [" 庭が好きです。", "庭が好きです。\n", "。", "。。"], ids=["前に空白", "後ろに改行", "句点だけ", "句点2つ"]
)
def test_原文と一字も違わない言葉のある本文だけが通る(body):
    assert quote_problem(" 庭が好きです。\n。。", body) is not None


def test_全体がかっこで囲まれた発言は内側を発言として扱う():
    assert quote_problem("「毎朝この庭を見ます。花も見ます。」", "花も見ます。") is None
    assert find_sentence_span("「毎朝この庭を見ます。」", "毎朝この庭を見ます。") == (1, 11)


def test_句点のあとの改行は切れ目のまま():
    assert quote_problem("庭が好きです。\n花も好きです。", "花も好きです。") is None
    assert quote_problem("庭が好きです。\n花も好きです。", "庭が好きです。") is None


@pytest.mark.parametrize(("text", "body"), PASSES)
def test_文の切れ目で始まり終わる抜き出しは通る(text, body):
    assert find_sentence_span(text, body) is not None
    assert quote_problem(text, body) is None


@pytest.mark.parametrize(("text", "body"), STOPS)
def test_文の途中で切れる抜き出しは止める(text, body):
    assert find_sentence_span(text, body) is None
    assert quote_problem(text, body) is not None


def test_原文にない文と文の途中は理由を分ける():
    assert quote_problem("庭が好きです。", "海が好きです。") == "発言の原文にありません"
    assert quote_problem("庭が好きではありません。", "庭が好き") == "文の途中で切れています"


def test_空の本文は通らない():
    assert find_sentence_span("庭が好きです。", "   ") is None


def test_見つかった位置を返す():
    assert find_sentence_span("朝です。庭です。", "庭です。") == (4, 8)


# ---- 台帳でも止める ----


def test_文の途中で切れた本人の言葉は台帳に入らない(conn, add_utterance, make_draft):
    utterance = add_utterance("家で暮らしたいとは思いません。")

    with pytest.raises(LedgerError, match="文の途中"):
        ledger.append_statement(conn, make_draft([utterance.id], "家で暮らしたい"))


def test_原文にない本人の言葉は台帳に入らない(conn, add_utterance, make_draft):
    utterance = add_utterance("庭が好きです。")

    with pytest.raises(LedgerError, match="原文にありません"):
        ledger.append_statement(conn, make_draft([utterance.id], "海が好きです。"))


def test_本人の訂正も断片では入らない(conn, users, add_statement, add_utterance):
    original = add_statement("娘とは週に一度話します。")
    said = add_utterance("娘とは毎日話します。そうでもないかな。")

    with pytest.raises(LedgerError, match="文の途中"):
        ledger.correct_statement(
            conn,
            original.id,
            actor_user_id=users["誠一さん"].id,
            utterance_id=said.id,
            body="娘とは毎日",
        )
