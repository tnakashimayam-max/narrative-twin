"""本人の確かめの行を足す決まりのテスト。

役割：どの本人の文を確かめるか、どこに置くかを確かめる。
4つの極：3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：6（「本人だけ」の言葉は確かめに出さない）、8（人物と記録はすべて架空）、9（本人の文は一字も変えない）。

本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiction import confirm_plan as cp

REAL_CHAT = Path(r"C:\dev\nt-story\チャット_全件.jsonl")
REAL_FIXED = REAL_CHAT.with_name("決まった言葉.jsonl")
needs_real_data = pytest.mark.skipif(not REAL_CHAT.exists(), reason="実データの置き場がありません")


def row(at, place, text, by="誠一", mark=None):
    return {"at": at, "place": place, "by": by, "text": text, "marks": {"決まった": mark} if mark else {}, "photos": []}


def fixed_word(text, by="誠一さん"):
    return {"date": "2030-01-01", "by": by, "text": text}


def close(at):
    return {"at": at, "kind": "閉じる", "room": "ツイン", "by": "誠一さん"}


def said(at, text):
    return {"at": at, "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text}


def matches(targets):
    return [item.match for item in targets]


# ---- 選ぶ文 ----


def test_決まった言葉は本の印だけを確かめ_だけと訂は入れない():
    rows = [
        row("2030-01-04T22:40", "ツインへ", "福岡の施設なんざ行かん。", mark="本09"),
        row("2027-08-14T23:50", "本人だけ", "兄貴も77か。もう会うこともなかろう。", mark="だけ01"),
        row("2026-11-03T21:05", "訂正", "庭が好きなのではなく、妻と過ごした場所だから大切", mark="訂01"),
        row("2034-08-13T21:30", "今の様子", "支払いは千円札ばかりで、財布が小銭で膨らんでいます。", by="美穂", mark="様01"),
    ]
    fixed = [fixed_word("福岡の施設なんざ行かん。"), fixed_word("兄貴も77か。もう会うこともなかろう。")]

    assert matches(cp.fixed_targets(rows, fixed)) == ["福岡の施設なんざ行かん。"]


def test_決まった言葉は行の全文でなく_決まった文から選ぶ():
    rows = [row("2028-02-20T16:50", "つぶやき", "風呂上がりに瓶の牛乳を飲んで、帰りのバスで居眠りした。誰にも気兼ねせん一人旅が一番たい。", mark="本08")]

    assert matches(cp.fixed_targets(rows, [fixed_word("誰にも気兼ねせん一人旅が一番たい。")])) == ["誰にも気兼ねせん一人旅が一番たい。"]


def test_決まった言葉と引用は2035年9月までで_そのあとは足さない():
    rows = [
        row("2035-09-14T23:40", "ツインへ", "わしは、ここで、かずことおる。", mark="本16"),
        row("2036-06-14T17:45", "つぶやき", "今日の飯は旨かったばい。もう一杯。", mark="本17"),
        row("2035-12-16T22:00", "ツインへ", "先生は他人の数に入っとらんかった。"),
        row("2036-02-10T22:40", "チーム", "昨年12月16日、2035年12月16日に「先生は他人の数に入っとらんかった。」とあります。", by="田中"),
    ]
    fixed = [fixed_word("わしは、ここで、かずことおる。"), fixed_word("今日の飯は旨かったばい。")]

    assert matches(cp.fixed_targets(rows, fixed)) == ["わしは、ここで、かずことおる。"]
    assert cp.cited_targets(rows) == []


def test_引用は本人の行にある文だけで_ほかの人の言葉と日付のない語は入れない():
    rows = [
        row("2029-10-17T18:00", "つぶやき", "肉は冷凍してもよかが、飯は冷凍したら飯じゃなくなる。"),
        row("2029-06-24T17:30", "コメント", "負けると黙るくせに、次の日取りだけはきっちり決める人です。", by="正雄"),
        row("2026-10-01T17:55", "つぶやき", "何ば書けばよかか分からん。"),
        row(
            "2036-02-10T20:05",
            "チーム",
            "2029年10月17日には「肉は冷凍してもよかが、飯は冷凍したら飯じゃなくなる。」。"
            "2029年6月24日の正雄さんのコメントに「負けると黙るくせに、次の日取りだけはきっちり決める人です。」。"
            "「よか」と言われました。",
            by="田中",
        ),
    ]

    assert matches(cp.cited_targets(rows)) == ["肉は冷凍してもよかが、飯は冷凍したら飯じゃなくなる。"]


def test_日付のない語は_本人の行にあっても引用に数えない():
    rows = [row("2026-10-01T17:55", "つぶやき", "何ば書けばよかか分からん。"), row("2036-05-06T11:00", "ケア会議", "「分からん」とのことです。", by="田中")]

    assert cp.cited_targets(rows) == []


def test_引用の中の二重かぎかっこは_元の行のかぎかっこにそろえて探す():
    rows = [
        row("2033-01-11T18:00", "つぶやき", "健太の三学期の目標は「飛車をうまく使う」。"),
        row("2036-02-10T22:10", "チーム", "2033年1月11日のつぶやきには「健太の三学期の目標は『飛車をうまく使う』。」とあります。", by="田中"),
    ]

    assert matches(cp.cited_targets(rows)) == ["健太の三学期の目標は「飛車をうまく使う」。"]


def test_引用が文の途中でも_matchは文の全体にする():
    rows = [
        row("2030-06-16T20:20", "つぶやき", "大浴場に二十人と浸かるより、朝に鮮魚店へ行って、ヒラメを買うた。"),
        row("2036-02-10T21:00", "チーム", "2030年6月16日のつぶやきでは「大浴場に二十人と浸かるより」と書いています。", by="田中"),
    ]

    assert matches(cp.cited_targets(rows)) == ["大浴場に二十人と浸かるより、朝に鮮魚店へ行って、ヒラメを買うた。"]


def test_本人だけの行は引用されても入れない():
    rows = [
        row("2033-08-14T23:40", "本人だけ", "兄貴、俺が悪かった。一言謝りたかった。"),
        row("2036-02-10T21:00", "チーム", "2033年8月14日に「兄貴、俺が悪かった。」とあります。", by="田中"),
    ]

    assert cp.cited_targets(rows) == []


def test_人の部屋の行は言明にならないので入れない():
    rows = [
        row("2026-10-01T17:55", "誠一↔美穂", "何ば書けばよかか分からん。"),
        row("2036-02-10T21:00", "チーム", "2026年10月1日に「何ば書けばよかか分からん。」とあります。", by="田中"),
    ]

    assert cp.cited_targets(rows) == []


def test_ツインへは各月2行までで_2034年9月まで():
    rows = [
        row("2034-09-01T21:00", "ツインへ", "一つ目の長か文たい。"),
        row("2034-09-02T21:00", "ツインへ", "二つ目の長か文たい。"),
        row("2034-09-03T21:00", "ツインへ", "三つ目の長か文たい。"),
        row("2034-10-01T21:00", "ツインへ", "十月の長か文たい。"),
    ]

    assert matches(cp.twin_targets(rows, set())) == ["一つ目の長か文たい。", "二つ目の長か文たい。"]


def test_ツインへですでに選んだ文の行は_2行に数えない():
    rows = [
        row("2030-01-01T21:00", "ツインへ", "決まった言葉の長か文たい。"),
        row("2030-01-02T21:00", "ツインへ", "二つ目の長か文たい。"),
        row("2030-01-03T21:00", "ツインへ", "三つ目の長か文たい。"),
    ]

    assert matches(cp.twin_targets(rows, {"決まった言葉の長か文たい。"})) == ["二つ目の長か文たい。", "三つ目の長か文たい。"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("冷や飯なんざ食えるか。飯は炊きたてが一番たい。", "飯は炊きたてが一番たい。"),
        ("よか。", None),
        ("ホークス勝った！今日はよか日たい。", "今日はよか日たい。"),
    ],
)
def test_matchは文の途中で切らず_いちばん長い文を選び_短い文は使わない(text, expected):
    assert cp.longest_sentence(text) == expected


def test_同じ文は1回だけ確かめる():
    rows = [
        row("2030-01-04T22:40", "ツインへ", "福岡の施設なんざ行かん。", mark="本09"),
        row("2036-02-10T21:00", "チーム", "2030年1月4日に「福岡の施設なんざ行かん。」とあります。", by="田中"),
    ]

    assert matches(cp.choose_targets(rows, [fixed_word("福岡の施設なんざ行かん。")])) == ["福岡の施設なんざ行かん。"]


# ---- 置く場所 ----


def test_その月の最後の閉じるのあとにまとめて置き_本人だけの行より後ろにする():
    rows = [row("2030-01-04T08:00", "ツインへ", "一つ目の長か文たい。"), row("2030-01-20T08:00", "ツインへ", "二つ目の長か文たい。")]
    lines = [
        said("2030-01-04T08:00", "一つ目の長か文たい。"),
        close("2030-01-04T08:30"),
        said("2030-01-20T08:00", "二つ目の長か文たい。"),
        close("2030-01-20T08:30"),
        {"at": "2030-01-20T08:30", "kind": "本人だけにする", "by": "誠一さん", "text": "別の文たい。"},
        said("2030-02-01T08:00", "二月の文たい。"),
    ]

    result, left = cp.add_confirmations(lines, rows, [])

    assert left == []
    assert [line["kind"] for line in result] == ["発言", "閉じる", "発言", "閉じる", "本人だけにする", "確かめ", "確かめ", "発言"]
    assert result[5] == {"at": "2030-01-20T08:30", "kind": "確かめ", "by": "誠一さん", "match": "一つ目の長か文たい。"}
    assert result[6]["match"] == "二つ目の長か文たい。"


def test_訂正より前には確かめを置かず_次のまとめで確かめる():
    rows = [row("2026-10-18T21:30", "ツインへ", "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。"), row("2026-11-11T21:45", "ツインへ", "雨の音ば聞きよると、足音がしそうになる。")]
    lines = [
        said("2026-10-18T21:30", "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。"),
        close("2026-10-31T21:40"),
        {"at": "2026-11-02T23:59", "kind": "整理", "by": "誠一さん"},
        close("2026-11-02T23:00"),
        {"at": "2026-11-03T21:05", "kind": "訂正", "by": "誠一さん", "match": "庭", "guess": True, "text": "庭が好きなのではなく"},
        said("2026-11-11T21:45", "雨の音ば聞きよると、足音がしそうになる。"),
        close("2026-11-30T21:50"),
    ]

    result, left = cp.add_confirmations(lines, rows, [])

    assert left == []
    kinds = [line["kind"] for line in result]
    assert kinds.index("確かめ") > kinds.index("訂正")
    assert [line["at"] for line in result if line["kind"] == "確かめ"] == ["2026-11-30T21:50", "2026-11-30T21:50"]
    assert [line["match"] for line in result if line["kind"] == "確かめ"] == [
        "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。",
        "雨の音ば聞きよると、足音がしそうになる。",
    ]


def test_閉じるより後に話した文は次の月にまとめる():
    rows = [row("2030-01-31T23:00", "ツインへ", "月の終わりの文たい。")]
    lines = [
        close("2030-01-20T08:30"),
        {"at": "2030-01-31T23:00", "kind": "便り", "by": "誠一さん", "ref": "便り1", "body": "月の終わりの文たい。", "share_with": "家族"},
        close("2030-02-10T08:30"),
    ]

    result, left = cp.add_confirmations(lines, rows, [])

    assert left == []
    assert [line["kind"] for line in result] == ["閉じる", "便り", "閉じる", "確かめ"]
    assert result[3]["at"] == "2030-02-10T08:30"


def test_流さない日の文は足さない():
    rows = [row("2030-01-04T08:00", "ツインへ", "流さない日の文たい。"), row("2030-01-05T08:00", "ツインへ", "流す日の長か文たい。")]
    lines = [said("2030-01-05T08:00", "流す日の長か文たい。"), close("2030-01-05T08:30")]

    result, _ = cp.add_confirmations(lines, rows, [])

    assert [line["match"] for line in result if line["kind"] == "確かめ"] == ["流す日の長か文たい。"]


def test_2035年10月以降の閉じるのあとには置かない():
    rows = [row("2034-09-30T23:00", "ツインへ", "九月の終わりの文たい。")]
    lines = [close("2034-09-01T08:30"), said("2034-09-30T23:00", "九月の終わりの文たい。"), close("2035-10-01T08:30")]

    result, left = cp.add_confirmations(lines, rows, [])

    assert [line["kind"] for line in result] == ["閉じる", "発言", "閉じる"]
    assert matches(left) == ["九月の終わりの文たい。"]


# ---- 実データ ----


@needs_real_data
def test_実データで_本人だけの言葉は確かめに入らない():
    rows = [json.loads(raw) for raw in REAL_CHAT.read_text(encoding="utf-8").splitlines() if raw.strip()]
    fixed = [json.loads(raw) for raw in REAL_FIXED.read_text(encoding="utf-8").splitlines() if raw.strip()]
    private = [item["text"] for item in rows if item["place"] == "本人だけ"]

    targets = cp.choose_targets(rows, fixed)

    assert len(private) == 8
    assert targets
    assert not [item.match for item in targets if any(item.match in text for text in private)]
    assert all(item.at[:7] <= "2035-09" for item in targets)


@needs_real_data
def test_実データで_matchはどれも本人の文に一字も違わず入っている():
    rows = [json.loads(raw) for raw in REAL_CHAT.read_text(encoding="utf-8").splitlines() if raw.strip()]
    fixed = [json.loads(raw) for raw in REAL_FIXED.read_text(encoding="utf-8").splitlines() if raw.strip()]
    mine = [item["text"] for item in rows if item["by"] == "誠一"]

    for target in cp.choose_targets(rows, fixed):
        assert any(target.match in text for text in mine), target.match
