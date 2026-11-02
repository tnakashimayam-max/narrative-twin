"""本人の言葉の抜き出しの切り方を確かめる（決まった手順で動く層）。

役割：本人の言葉の本文が、発言の原文の中で文の切れ目から切れ目までになっているかを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：9（本人の言葉は、一字も変えずに届ける。言い換えない）。

原文に含まれるだけでは足りない。「家で暮らしたいとは思いません。」から「家で暮らしたい」を
抜き出すと、一字も変えていなくても意味が逆になるため。

切れ目は、原文の先頭と末尾、「。」「！」「？」（半角の ! ? を含む）だけにする。
- 読点は切れ目にしない。後ろに否定や条件が続く文が多く、止める語の一覧を作っても際限がないため。
- 改行は切れ目にしない。文の途中で改行した原文で、意味が逆になる切り方が通るため。
- かぎかっこの中だけの抜き出しは止める。他人の言葉を本人の言葉にしてしまうため。
  発言全体がかっこで囲まれているときだけ、かっこの内側を発言として扱う。

本人の言葉は、誤って入れるより、捨てるほうへ倒す。発言の原文は残るので作り直せる。

この確かめでは止められない切り方がある（「施設は嫌です。でも和子のためなら行きます。」の前の文だけ、
など）。それはプロンプトの決まりと本人の確認で補う。

関連するテスト：tests/test_verbatim.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

_SENTENCE_ENDS = "。！？!?"
_PAIRS = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】"}
# 本文の直後にあってよい記号。？と！は入れない。その直前で止めると、問いや強い調子が
# 断定に変わるため（「家に帰りたい？」→「家に帰りたい」）。
_ALLOWED_AFTER = "。"


def _unwrap(text: str) -> tuple[str, int]:
    """発言全体がかっこで囲まれていれば、内側の文字列と、その開始位置を返す。"""
    stripped = text.strip()
    if len(stripped) >= 2 and _PAIRS.get(stripped[0]) == stripped[-1]:
        return stripped[1:-1], text.index(stripped) + 1
    return text, 0


def _only_a_quotation(body: str) -> bool:
    """本文が、かぎかっこの中身だけかどうかを返す。"""
    core = body.rstrip(_SENTENCE_ENDS)
    return len(core) >= 2 and _PAIRS.get(core[0]) == core[-1]


def _starts_at_boundary(text: str, start: int) -> bool:
    """本文の前が文の切れ目（空白は飛ばす）かどうかを返す。"""
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    return index < 0 or text[index] in _SENTENCE_ENDS


def _ends_at_boundary(text: str, end: int, body: str) -> bool:
    """本文の後ろが文の切れ目（空白は飛ばす）かどうかを返す。"""
    if body[-1] in _SENTENCE_ENDS:
        return True
    index = end
    while index < len(text) and text[index].isspace():
        index += 1
    return index >= len(text) or text[index] in _ALLOWED_AFTER


def find_sentence_span(text: str, body: str) -> tuple[int, int] | None:
    """本文が文の切れ目で始まり終わる位置を返す。なければ None を返す。

    同じ語が文の途中と単独の文の両方にあるときは、どれか1つが合っていればよい。
    """
    if not body or _only_a_quotation(body):
        return None
    inner, offset = _unwrap(text)
    start = inner.find(body)
    while start != -1:
        end = start + len(body)
        if _starts_at_boundary(inner, start) and _ends_at_boundary(inner, end, body):
            return start + offset, end + offset
        start = inner.find(body, start + 1)
    return None


def quote_problem(text: str, body: str) -> str | None:
    """本人の言葉として入れられない理由を返す。問題がなければ None を返す。

    理由を分けるのは、やり直しのときに AI へ伝える内容と、入らなかった件数の内訳に使うため。
    空白を除いて照合しないのは、保存する本文が原文に含まれることを保つため。
    """
    if not any(char.isalnum() for char in body):
        return "本文に言葉がありません"
    if body != body.strip():
        return "本文の前後に空白があります"
    if body not in text:
        return "発言の原文にありません"
    if find_sentence_span(text, body) is None:
        return "文の途中で切れています"
    return None
