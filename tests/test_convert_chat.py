"""チャットの全件を script.jsonl に変える道具のテスト。

役割：行き先の決まり（本人の行、便り、代筆の部屋、人だけの部屋）、足す行の位置、写真の扱い、選ぶ版の絞り込み、
      同じ入力から同じ出力になることを確かめる。実データがある環境では、全件と選ぶ版の数も確かめる。
4つの極：1. 書く・話す、2. 分かち合う、4. 10年後に代弁する（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、8（人物と記録はすべて架空）、9（本文は一字も変えない）、10（「本人だけ」を守る）、15（人だけの部屋にツインを入れない）。

fiction/run_script.py の Line と check_lines は使わない。点検の中身が変わっても、変換の約束の結果が変わらないようにするため。
AI もサーバーも使わない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from fiction.convert_chat import Conversion, convert, find_parents, main, read_chat, select_days

ROOT = Path(__file__).resolve().parents[1]
STORY = Path(r"C:\dev\nt-story")
REAL_CHAT = STORY / "チャット_全件.jsonl"
REAL_FIXED = STORY / "決まった言葉.jsonl"
needs_real_data = pytest.mark.skipif(not REAL_CHAT.exists(), reason="実データの置き場がありません")


def row(at, place, by="誠一", text="架空の文です。", marks=None, photos=()):
    return {"at": at, "place": place, "by": by, "text": text, "marks": marks or {}, "photos": list(photos)}


def photo(number):
    return {"id": number, "desc": "架空の写真", "path": rf"C:\架空\photos\2026\{number}.jpg"}


def put_photos(folder: Path, *numbers):
    for number in numbers:
        (folder / f"{number}.jpg").write_bytes(b"jpg")


def run(rows, photo_dir, **options) -> Conversion:
    return convert(rows, photo_dir=photo_dir, **options)


def of_kind(result: Conversion, kind):
    return [line for line in result.lines if line["kind"] == kind]


def in_room(result: Conversion, room):
    return [line for line in result.lines if line["kind"] == "発言" and line["room"] == room]


def place_of(result: Conversion, **fields) -> int:
    """fields がすべて合う最初の行の位置を返す。"""
    for index, line in enumerate(result.lines):
        if all(line.get(key) == value for key, value in fields.items()):
            return index
    raise AssertionError(f"見つかりません：{fields}")


def assert_rooms_hold(lines):
    """流すときに止まる形になっていないかを、変換の出力だけで確かめる。"""
    members = {"ツイン": {"誠一さん"}}
    open_day = None
    closed_days = set()
    for previous, line in zip(lines, lines[1:]):
        assert line["at"] >= previous["at"], f"時刻が減っています：{previous} → {line}"
    for line in lines:
        day = line["at"][:10]
        assert open_day in (None, day), f"{open_day} の会話を閉じる前に日付が変わっています"
        if line["kind"] == "部屋":
            assert line["room"] not in members, f"部屋の名前が重なっています：{line['room']}"
            members[line["room"]] = {*line["members"], line["by"]}
        elif line["kind"] == "ツインを入れる":
            assert line["room"] in members
        elif line["kind"] == "発言":
            assert line["room"] in members, f"部屋がまだありません：{line}"
            assert line["by"] in members[line["room"]], f"部屋にいない人です：{line}"
            if line["room"] == "ツイン":
                assert day not in closed_days, f"ツインとの部屋の会話が1日に2回あります：{day}"
                open_day = day
        elif line["kind"] == "閉じる":
            assert open_day == day
            closed_days.add(day)
            open_day = None
    assert open_day is None


# ---- 名前と最初の行 ----


def test_最初の行で最初からいる5人を物語の設定に直す(tmp_path):
    result = run([row("2026-10-01T10:00", "ツインへ")], tmp_path)
    people = result.lines[:5]
    assert [line["kind"] for line in people] == ["人"] * 5
    assert {line["at"] for line in people} == {"2026-10-01T00:00"}
    assert {line["by"] for line in people} == {"誠一さん"}
    assert people[0] == {"at": "2026-10-01T00:00", "kind": "人", "by": "誠一さん", "name": "誠一さん", "birth_year": 1952}
    assert people[1]["name"] == "和子さん" and people[1]["is_deceased"] is True and people[1]["birth_year"] == 1954
    assert people[2]["relation"] == "長女" and people[2]["birth_year"] == 1985 and people[2]["residence"] == "遠く"
    assert people[3]["relation"] == "友人" and people[3]["birth_year"] == 1953 and people[3]["residence"] == "近く"
    assert people[4]["relation"] == "ご近所さん" and people[4]["birth_year"] == 1949 and people[4]["residence"] == "近く"


def test_和子さんの同居はnullと書いて消す(tmp_path):
    result = run([row("2026-10-01T10:00", "ツインへ")], tmp_path)
    kazuko = next(line for line in of_kind(result, "人") if line["name"] == "和子さん")
    # 書かないと「同居」が残るので、項目そのものが要る
    assert "residence" in kazuko and kazuko["residence"] is None


def test_知らない名前は推測せずに止める(tmp_path):
    with pytest.raises(ValueError, match="知らない名前"):
        run([row("2026-10-01T10:00", "誠一↔美穂", by="架空の人")], tmp_path)


def test_人の決まらない部屋は推測せずに止める(tmp_path):
    with pytest.raises(ValueError, match="人が決まりません"):
        run([row("2026-10-01T10:00", "どこかの部屋", by="美穂")], tmp_path)


# ---- ツインとの部屋と便り ----


def test_本文は一字も変えずに発言と便りに入る(tmp_path):
    text = "飯は炊きたてたい！「庭」が　好きやけん…。"
    result = run([row("2026-10-01T10:00", "つぶやき", text=text)], tmp_path)
    assert in_room(result, "ツイン")[0]["text"] == text
    assert of_kind(result, "便り")[0]["body"] == text


def test_つぶやきは発言と便りの2行になる(tmp_path):
    result = run([row("2026-10-01T10:00", "つぶやき")], tmp_path)
    letter = of_kind(result, "便り")[0]
    assert letter == {
        "at": "2026-10-01T10:00",
        "kind": "便り",
        "by": "誠一さん",
        "ref": "便り-2026-10-01T10:00",
        "body": "架空の文です。",
        "share_with": "縁のある人まで",
    }
    assert place_of(result, kind="発言", room="ツイン") < place_of(result, kind="便り")


def test_つぶやき家族の便りは家族までになる(tmp_path):
    result = run([row("2026-10-01T10:00", "つぶやき家族")], tmp_path)
    assert of_kind(result, "便り")[0]["share_with"] == "家族"


def test_ツインへの行は便りにならない(tmp_path):
    result = run([row("2026-10-01T10:00", "ツインへ")], tmp_path)
    assert len(in_room(result, "ツイン")) == 1
    assert of_kind(result, "便り") == []


def test_閉じるはその日のツインの部屋の最後のまとまりの直後に1回だけ入る(tmp_path):
    rows = [
        row("2026-10-01T09:00", "つぶやき"),
        row("2026-10-01T10:00", "コメント", by="美穂"),
        row("2026-10-01T11:00", "ツインへ"),
        row("2026-10-01T12:00", "つぶやき", photos=[photo("P1")]),
        row("2026-10-01T15:00", "誠一↔美穂", by="美穂"),
        row("2026-10-02T09:00", "ツインへ"),
    ]
    result = run(rows, tmp_path)
    closes = of_kind(result, "閉じる")
    assert [line["at"] for line in closes] == ["2026-10-01T12:00", "2026-10-02T09:00"]
    assert closes[0]["room"] == "ツイン" and closes[0]["by"] == "誠一さん"
    position = place_of(result, kind="閉じる")
    assert result.lines[position - 1]["kind"] == "便り"
    assert_rooms_hold(result.lines)


def test_人の部屋の行だけの日には閉じるを出さない(tmp_path):
    result = run([row("2026-10-01T15:00", "誠一↔美穂", by="美穂")], tmp_path)
    assert of_kind(result, "閉じる") == []


def test_本人だけの行に閉じるの直後の守る行が付く(tmp_path):
    rows = [
        row("2026-10-01T22:00", "本人だけ", text="誰にも言わん話たい。"),
        row("2026-10-01T23:00", "ツインへ", text="今日はここまで。"),
    ]
    result = run(rows, tmp_path)
    assert in_room(result, "ツイン")[0]["text"] == "誰にも言わん話たい。"
    position = place_of(result, kind="閉じる")
    assert result.lines[position + 1] == {
        "at": "2026-10-01T23:00",
        "kind": "本人だけにする",
        "by": "誠一さん",
        "text": "誰にも言わん話たい。",
    }
    assert of_kind(result, "便り") == []


# ---- コメント ----


def test_コメントは時刻で直前のつぶやきの便りに付く(tmp_path):
    rows = [
        row("2026-10-01T09:00", "つぶやき"),
        row("2026-10-01T09:30", "ツインへ"),
        row("2026-10-01T10:00", "つぶやき家族"),
        row("2026-10-01T11:00", "コメント", by="美穂", text="見たよ。"),
    ]
    result = run(rows, tmp_path)
    assert of_kind(result, "コメント") == [
        {"at": "2026-10-01T11:00", "kind": "コメント", "by": "美穂さん", "letter": "便り-2026-10-01T10:00", "text": "見たよ。"}
    ]


def test_親の投稿がないコメントは止める(tmp_path):
    with pytest.raises(ValueError, match="親の投稿"):
        run([row("2026-10-01T09:00", "コメント", by="美穂")], tmp_path)


def test_田中さんのコメントは便りのコメントにならず部屋の発言になる(tmp_path):
    rows = [
        row("2036-02-01T09:00", "つぶやき"),
        row("2036-02-01T10:00", "コメント", by="田中", text="拝見しました。"),
    ]
    result = run(rows, tmp_path)
    assert of_kind(result, "コメント") == []
    said = in_room(result, "誠一さんのそば（田中さんと）")
    assert [(line["by"], line["text"]) for line in said] == [("田中さん", "拝見しました。")]
    assert_rooms_hold(result.lines)


def test_代筆の投稿へのコメントは便りのコメントにならず部屋の発言になる(tmp_path):
    rows = [
        row("2035-11-01T09:00", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
        row("2035-11-01T10:00", "コメント", by="美穂", text="ありがとう。"),
    ]
    result = run(rows, tmp_path)
    assert of_kind(result, "コメント") == []
    assert [line["by"] for line in in_room(result, "誠一さんのそば")] == ["正雄さん", "美穂さん"]


# ---- 代筆の部屋 ----


def test_代筆は本人の行にも便りにもならない(tmp_path):
    rows = [row("2035-10-02T17:10", "つぶやき", by="正雄", text="正雄が入れとる。", marks={"代筆": "誠一"})]
    result = run(rows, tmp_path)
    assert in_room(result, "ツイン") == []
    assert of_kind(result, "便り") == []
    assert of_kind(result, "閉じる") == []
    said = in_room(result, "誠一さんのそば")
    assert [(line["by"], line["text"]) for line in said] == [("正雄さん", "正雄が入れとる。")]


def test_代筆の部屋は田中さんを招く日を境に分かれそれぞれ話す人だけが入る(tmp_path):
    rows = [
        row("2035-10-02T10:00", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
        row("2035-10-02T11:00", "コメント", by="節子"),
        row("2036-01-17T23:00", "つぶやき", by="美穂", marks={"代筆": "誠一"}),
        row("2036-01-18T08:00", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
        row("2036-01-18T09:00", "コメント", by="田中"),
    ]
    result = run(rows, tmp_path)
    rooms = {line["room"]: line for line in of_kind(result, "部屋")}
    before = rooms["誠一さんのそば"]
    after = rooms["誠一さんのそば（田中さんと）"]
    assert (before["by"], before["room_kind"], before["members"]) == ("誠一さん", "グループ", ["正雄さん", "節子さん", "美穂さん"])
    assert (after["by"], after["room_kind"], after["members"]) == ("誠一さん", "グループ", ["正雄さん", "田中さん"])
    assert [line["by"] for line in in_room(result, "誠一さんのそば")] == ["正雄さん", "節子さん", "美穂さん"]
    assert [line["by"] for line in in_room(result, "誠一さんのそば（田中さんと）")] == ["正雄さん", "田中さん"]
    assert_rooms_hold(result.lines)


def test_代筆の部屋にはツインを入れて部屋の次の行に置く(tmp_path):
    rows = [row("2035-10-02T10:00", "つぶやき", by="正雄", marks={"代筆": "誠一"})]
    result = run(rows, tmp_path)
    position = place_of(result, kind="部屋", room="誠一さんのそば")
    assert result.lines[position + 1] == {
        "at": "2035-10-02T10:00",
        "kind": "ツインを入れる",
        "by": "誠一さん",
        "room": "誠一さんのそば",
        "present": True,
    }
    assert result.lines[position + 2]["kind"] == "発言"


def test_田中さんのコメントが最初の行でも部屋を先に作る(tmp_path):
    # 招いたあとの部屋の最初の行が代筆の投稿とは限らない
    rows = [
        row("2036-03-15T09:00", "つぶやき"),
        row("2036-03-15T10:00", "コメント", by="田中"),
        row("2036-03-20T10:00", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
    ]
    result = run(rows, tmp_path)
    assert place_of(result, kind="招く") < place_of(result, kind="部屋") < place_of(result, kind="発言", by="田中さん")
    assert_rooms_hold(result.lines)


# ---- 人だけの部屋、チーム、ケア会議 ----


def test_人だけの部屋は最初の行の前に作りツインを入れない(tmp_path):
    rows = [
        row("2026-10-01T10:00", "誠一↔美穂", by="美穂", text="一回だけ投稿してみよ。"),
        row("2026-10-01T10:10", "誠一↔美穂", by="誠一"),
    ]
    result = run(rows, tmp_path)
    room = of_kind(result, "部屋")[0]
    assert room == {
        "at": "2026-10-01T10:00",
        "kind": "部屋",
        "by": "美穂さん",
        "room": "誠一↔美穂",
        "room_kind": "1対1",
        "members": ["誠一さん"],
    }
    assert place_of(result, kind="部屋") < place_of(result, kind="発言", room="誠一↔美穂")
    assert of_kind(result, "ツインを入れる") == []


def test_人だけの部屋の写真も1枚目は文と同じ行で2枚目以降は写真だけになる(tmp_path):
    put_photos(tmp_path, "P1", "P2")
    result = run([row("2026-10-01T10:00", "誠一↔健太", by="健太", photos=[photo("P1"), photo("P2")])], tmp_path)
    said = in_room(result, "誠一↔健太")
    assert said[0]["photo"] == "P1.jpg" and "text" in said[0]
    assert said[1] == {"at": "2026-10-01T10:00", "kind": "発言", "room": "誠一↔健太", "by": "健太くん", "photo": "P2.jpg"}


def test_チームは田中さんと美穂さんと井ノ口先生のグループでツインを入れない(tmp_path):
    result = run([row("2036-01-18T18:00", "チーム", by="田中")], tmp_path)
    room = of_kind(result, "部屋")[0]
    assert (room["by"], room["room_kind"], room["members"]) == ("田中さん", "グループ", ["美穂さん", "井ノ口先生"])
    assert of_kind(result, "ツインを入れる") == []
    assert_rooms_hold(result.lines)


def test_ケア会議は田中さんが作り美穂さんがツインを入れる(tmp_path):
    # 最初に話す人が本人でも、作るのは田中さん
    result = run([row("2036-05-06T10:30", "ケア会議", by="誠一", text="家におる。")], tmp_path)
    room = of_kind(result, "部屋")[0]
    assert (room["by"], room["room"], room["room_kind"], room["members"]) == ("田中さん", "ケア会議", "グループ", ["誠一さん", "美穂さん"])
    twin = of_kind(result, "ツインを入れる")[0]
    assert (twin["by"], twin["room"], twin["present"]) == ("美穂さん", "ケア会議", True)
    assert in_room(result, "ケア会議")[0]["text"] == "家におる。"
    assert place_of(result, kind="招く") < place_of(result, kind="部屋") < place_of(result, kind="ツインを入れる") < place_of(result, kind="発言", room="ケア会議")


# ---- 人を足す ----


def test_井ノ口先生は部屋の行の直前に足す(tmp_path):
    # 話す前に、部屋に入る行で先に現れる
    result = run([row("2027-12-14T18:30", "美穂↔井ノ口", by="美穂")], tmp_path)
    position = place_of(result, kind="部屋")
    assert result.lines[position - 1] == {
        "at": "2027-12-14T18:30",
        "kind": "人",
        "by": "誠一さん",
        "name": "井ノ口先生",
        "role": "縁のある人",
        "relation": "かかりつけ医",
        "gender": "男性",
        "residence": "近く",
    }


def test_健太くんは最初に現れる行の直前に1回だけ足す(tmp_path):
    rows = [
        row("2031-04-06T10:12", "誠一↔健太", by="健太"),
        row("2031-04-06T10:20", "誠一↔健太", by="健太"),
    ]
    result = run(rows, tmp_path)
    added = [line for line in of_kind(result, "人") if line["name"] == "健太くん"]
    assert added == [
        {
            "at": "2031-04-06T10:12",
            "kind": "人",
            "by": "誠一さん",
            "name": "健太くん",
            "role": "家族",
            "relation": "孫",
            "birth_year": 2024,
            "gender": "男性",
            "residence": "遠く",
        }
    ]
    assert result.lines[result.lines.index(added[0]) + 1]["kind"] == "部屋"


def test_田中さんは美穂さんが招いてから現れる(tmp_path):
    result = run([row("2036-01-18T12:05", "美穂↔田中", by="田中")], tmp_path)
    position = place_of(result, kind="部屋")
    assert result.lines[position - 1] == {
        "at": "2036-01-18T12:05",
        "kind": "招く",
        "by": "美穂さん",
        "name": "田中さん",
        "called_name": "田中さん",
        "relation": "ケアマネジャー",
    }
    assert len(of_kind(result, "招く")) == 1


def test_現れない人は足さない(tmp_path):
    result = run([row("2026-10-01T10:00", "ツインへ")], tmp_path)
    assert {line["name"] for line in of_kind(result, "人")} == {"誠一さん", "和子さん", "美穂さん", "正雄さん", "節子さん"}
    assert of_kind(result, "招く") == []


# ---- 今の様子、訂正、託す、推測 ----


def test_今の様子は今の様子の行になる(tmp_path):
    result = run([row("2035-11-23T15:10", "今の様子", by="美穂", text="手を添えると安心した顔になります。")], tmp_path)
    assert of_kind(result, "今の様子") == [
        {"at": "2035-11-23T15:10", "kind": "今の様子", "by": "美穂さん", "text": "手を添えると安心した顔になります。"}
    ]


def test_訂正の直後に美穂さんへの託す行が入る(tmp_path):
    result = run([row("2026-11-03T21:05", "訂正", text="妻と過ごした場所だから大切")], tmp_path)
    position = place_of(result, kind="訂正")
    assert result.lines[position] == {
        "at": "2026-11-03T21:05",
        "kind": "訂正",
        "by": "誠一さん",
        "match": "庭",
        "guess": True,
        "text": "妻と過ごした場所だから大切",
    }
    assert result.lines[position + 1] == {
        "at": "2026-11-03T21:05",
        "kind": "託す",
        "by": "誠一さん",
        "delegates": ["美穂さん"],
        "themes": ["家族", "仕事・役割", "住まい・土地", "好きなこと", "大切にしてきたこと", "人の手を借りること", "つらかったこと"],
    }


def test_推測01を入れた版は_推測の日より前の行が同じで_控えから切り替えられる(tmp_path):
    rows = [row("2026-10-24T09:00", "ツインへ", text="前の日の話。"), row("2026-10-25T09:00", "ツインへ", text="庭の世話があるけん。"), row("2026-11-03T21:05", "訂正")]
    plain = run(rows, tmp_path)
    guessed = run(rows, tmp_path, script_guess=True)
    first_guess_day = next(index for index, line in enumerate(plain.lines) if line["at"].startswith("2026-10-25"))
    assert guessed.lines[:first_guess_day] == plain.lines[:first_guess_day]
    assert len(of_kind(guessed, "推測")) == 1
    assert of_kind(plain, "推測") == []


def test_訂正の言葉を指定できる(tmp_path):
    result = run([row("2026-11-03T21:05", "訂正")], tmp_path, correction_match="庭の世話")
    assert of_kind(result, "訂正")[0]["match"] == "庭の世話"


def test_その日の訂正でなければ託す行を入れず知らせる(tmp_path):
    result = run([row("2026-11-04T21:05", "訂正")], tmp_path)
    assert of_kind(result, "託す") == []
    assert any("託す" in note for note in result.notes)


def test_推測は指定したときだけ閉じるの直後に入る(tmp_path):
    rows = [row("2026-10-25T21:40", "ツインへ", text="庭の世話があるけん、ここは離れられん。")]
    assert of_kind(run(rows, tmp_path), "推測") == []
    result = run(rows, tmp_path, script_guess=True)
    position = place_of(result, kind="閉じる")
    assert result.lines[position + 1] == {
        "at": "2026-10-25T21:40",
        "kind": "推測",
        "by": "誠一さん",
        "text": "庭のある暮らしそのものを大切にされているのかもしれません。",
        "match": "庭の世話があるけん",
    }


def test_推測の日に閉じる行がなければ知らせる(tmp_path):
    result = run([row("2026-10-26T21:40", "ツインへ")], tmp_path, script_guess=True)
    assert of_kind(result, "推測") == []
    assert any("推測" in note for note in result.notes)


# ---- 段階と整理 ----


def test_段階は決まった時刻に入る(tmp_path):
    result = run([row("2026-10-01T10:00", "ツインへ")], tmp_path)
    assert of_kind(result, "段階") == [
        {"at": "2032-10-01T00:00", "kind": "段階", "by": "誠一さん", "stage": "要支援"},
        {"at": "2034-09-14T00:00", "kind": "段階", "by": "誠一さん", "stage": "要介護"},
    ]


def test_整理は節目の日の最後の行より前にならない(tmp_path):
    rows = [
        row("2026-11-02T12:20", "つぶやき"),
        row("2026-11-02T23:59", "ツインへ"),
        row("2026-11-03T09:00", "ツインへ"),
        row("2036-05-05T12:30", "誠一↔美穂", by="美穂"),
        row("2036-09-30T22:10", "誠一↔美穂", by="誠一"),
    ]
    result = run(rows, tmp_path)
    assert [line["at"] for line in of_kind(result, "整理")] == ["2026-11-02T23:59", "2036-05-05T23:59", "2036-09-30T23:59"]
    first = place_of(result, kind="整理")
    # 同じ 23:59 の発言と閉じる行のあとで、次の日の行の前
    assert result.lines[first - 1]["kind"] == "閉じる"
    assert result.lines[first + 1]["at"] == "2026-11-03T09:00"
    assert_rooms_hold(result.lines)


def test_足した行を入れても時刻は減らない(tmp_path):
    rows = [
        row("2026-10-01T09:00", "誠一↔美穂", by="美穂"),
        row("2026-10-01T10:00", "つぶやき", photos=[photo("P1"), photo("P2")]),
        row("2026-10-01T11:00", "コメント", by="美穂"),
        row("2026-10-25T21:40", "ツインへ"),
        row("2026-11-03T21:05", "訂正"),
        row("2027-12-14T18:30", "誠一↔井ノ口", by="井ノ口"),
        row("2032-10-01T08:00", "本人だけ"),
        row("2035-10-02T17:10", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
        row("2036-01-18T12:05", "美穂↔田中", by="田中"),
        row("2036-05-06T10:30", "ケア会議", by="田中"),
    ]
    put_photos(tmp_path, "P1", "P2")
    result = run(rows, tmp_path, script_guess=True)
    assert [line["at"] for line in result.lines] == sorted(line["at"] for line in result.lines)
    assert_rooms_hold(result.lines)


def test_入力の時刻が戻っていると止める(tmp_path):
    with pytest.raises(ValueError, match="時刻が減っています"):
        run([row("2026-10-02T10:00", "ツインへ"), row("2026-10-01T10:00", "誠一↔美穂", by="美穂")], tmp_path)


def test_選ぶ版で崩れた行が流れない入力でも時刻が戻っていれば止める(tmp_path):
    # 出力だけを見ていると気づけない。コメントの親が別の投稿に付いたまま進んでしまう
    rows = posts("2030-03", range(1, 7))
    for day in ("2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"):
        mark_day(rows, day, 出来事="010")
    rows.append(row("2030-03-04T11:00", "コメント", by="美穂"))
    with pytest.raises(ValueError, match="時刻が減っています"):
        run(rows, tmp_path, select=True)


# ---- 写真 ----


def test_2枚の写真の2枚目は写真だけの発言になり便りには1枚目が付く(tmp_path):
    put_photos(tmp_path, "P20261001-1", "P20261001-2")
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[photo("P20261001-1"), photo("P20261001-2")])]
    result = run(rows, tmp_path)
    said = in_room(result, "ツイン")
    assert said[0]["photo"] == "P20261001-1.jpg" and said[0]["text"] == "架空の文です。"
    assert said[1] == {"at": "2026-10-01T10:00", "kind": "発言", "room": "ツイン", "by": "誠一さん", "photo": "P20261001-2.jpg"}
    assert of_kind(result, "便り")[0]["photo"] == "P20261001-1.jpg"
    assert result.missing_photos == []


def test_ない写真は文だけになり番号が報告される(tmp_path):
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[photo("P1")])]
    result = run(rows, tmp_path)
    said = in_room(result, "ツイン")
    assert len(said) == 1 and "photo" not in said[0] and said[0]["text"] == "架空の文です。"
    assert "photo" not in of_kind(result, "便り")[0]
    assert result.missing_photos == ["P1"]


def test_2枚目がないときは写真だけの行を出さない(tmp_path):
    put_photos(tmp_path, "P1")
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[photo("P1"), photo("P2")])]
    result = run(rows, tmp_path)
    assert len(in_room(result, "ツイン")) == 1
    assert result.missing_photos == ["P2"]


def test_1枚目がなく2枚目があるときは2枚目を写真だけの行にする(tmp_path):
    put_photos(tmp_path, "P2")
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[photo("P1"), photo("P2")])]
    result = run(rows, tmp_path)
    said = in_room(result, "ツイン")
    assert "photo" not in said[0]
    assert said[1]["photo"] == "P2.jpg"
    assert "photo" not in of_kind(result, "便り")[0]
    assert result.missing_photos == ["P1"]


def test_区切りが斜め線の写真の場所でもファイル名を取り出す(tmp_path):
    put_photos(tmp_path, "P1")
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[{"id": "P1", "desc": "架空", "path": "photos/2026/P1.jpg"}])]
    assert in_room(run(rows, tmp_path), "ツイン")[0]["photo"] == "P1.jpg"


def test_コメントと今の様子の写真は流さず足りない数にも入れない(tmp_path):
    put_photos(tmp_path, "P1", "P2")
    rows = [
        row("2026-10-01T10:00", "つぶやき"),
        row("2026-10-01T11:00", "コメント", by="正雄", photos=[photo("P1")]),
        row("2026-10-01T12:00", "今の様子", by="美穂", photos=[photo("P2")]),
    ]
    result = run(rows, tmp_path)
    assert all("photo" not in line for line in result.lines)
    assert result.unflowed_photos == [("P1", "コメント"), ("P2", "今の様子")]
    assert result.missing_photos == []


def test_足りない写真のファイルは番号と流さない写真を分けて書く(tmp_path):
    chat = tmp_path / "チャット.jsonl"
    rows = [
        row("2026-10-01T10:00", "つぶやき", photos=[photo("P1")]),
        row("2026-10-01T11:00", "コメント", by="正雄", photos=[photo("P2")]),
    ]
    chat.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")
    missing = tmp_path / "足りない写真.txt"
    code = main(["--chat", str(chat), "--out", str(tmp_path / "script.jsonl"), "--photos", str(tmp_path), "--missing", str(missing)])
    assert code == 0
    assert missing.read_text(encoding="utf-8").splitlines() == ["P1", "流さない写真：P2（コメント）"]


# ---- 選ぶ版 ----


def posts(month, days, **fields):
    """その月の日ごとに本人のつぶやきを1行ずつ作る。"""
    return [row(f"{month}-{day:02d}T10:00", "つぶやき", **fields) for day in days]


def mark_day(rows, day, **marks):
    for item in rows:
        if item["at"].startswith(day):
            item["marks"].update(marks)


def test_選ぶ版は印の付いた日を選び残りの日は流さない(tmp_path):
    rows = posts("2030-03", range(1, 9))
    mark_day(rows, "2030-03-01", 決まった="本01")
    mark_day(rows, "2030-03-03", 出来事="010")
    rows += [
        row("2030-03-02T11:00", "ツインへ"),
        row("2030-03-04T09:00", "誠一↔美穂", by="美穂", marks={"思い出": "2030-03-04"}),
    ]
    rows.sort(key=lambda item: item["at"])
    assert select_days(rows) == {"2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"}
    result = run(rows, tmp_path, select=True)
    assert {line["at"][:10] for line in in_room(result, "ツイン")} == {"2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"}
    assert {line["at"][:10] for line in of_kind(result, "閉じる")} == {"2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"}
    assert result.selected_days == ["2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"]


def test_選ぶ版は田中さんのチームとケア会議に書かれた日を選ぶ(tmp_path):
    rows = posts("2035-03", range(1, 9))
    rows += [
        row("2036-01-18T18:00", "チーム", by="田中", text="2035年3月2日のお言葉に戻りましょう。"),
        row("2036-05-06T10:30", "ケア会議", by="田中", text="2035年3月5日の記録と、2035年3月7日の記録があります。"),
        row("2036-05-06T10:40", "ケア会議", by="誠一", text="2035年3月8日のことたい。"),
        row("2036-05-06T10:50", "チーム", by="美穂", text="2035年3月3日のことです。"),
    ]
    # 田中さんの行に書かれた3日と、足りない1日として残りの先頭の1日。本人と美穂さんの行に書かれた日は選ばない
    assert select_days(rows) == {"2035-03-01", "2035-03-02", "2035-03-05", "2035-03-07"}


def test_選ぶ版で印の日がツインの部屋の開かない日なら選ばない(tmp_path):
    rows = [
        row("2030-03-01T09:00", "誠一↔美穂", by="美穂", marks={"出来事": "010"}),
        row("2030-03-02T09:00", "つぶやき", by="正雄", marks={"代筆": "誠一", "決まった": "本09"}),
    ]
    assert select_days(rows) == set()


def test_選ぶ版は足りない月を残りの日から間隔をあけて足す(tmp_path):
    # 印のない10日から4日を選ぶ。step は 10 // 4 = 2 なので、1、3、5、7日
    rows = posts("2030-04", range(1, 11))
    assert select_days(rows) == {"2030-04-01", "2030-04-03", "2030-04-05", "2030-04-07"}


def test_選ぶ版で足りない月の日が少なければあるだけ選ぶ(tmp_path):
    rows = posts("2030-05", [1, 2])
    assert select_days(rows) == {"2030-05-01", "2030-05-02"}


def test_選ぶ版で4日あれば足さない(tmp_path):
    rows = posts("2030-06", range(1, 7))
    for day in ("2030-06-02", "2030-06-03", "2030-06-04", "2030-06-05"):
        mark_day(rows, day, 出来事="010")
    assert select_days(rows) == {"2030-06-02", "2030-06-03", "2030-06-04", "2030-06-05"}


def test_選ぶ版で親が流れないコメントは流さず前の投稿にも付け替えない(tmp_path):
    rows = []
    for day in range(1, 7):
        rows.append(row(f"2030-03-{day:02d}T10:00", "つぶやき"))
        rows.append(row(f"2030-03-{day:02d}T11:00", "コメント", by="美穂", text=f"{day}日のコメント"))
    for day in ("2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"):
        mark_day(rows, day, 出来事="010")
    result = run(rows, tmp_path, select=True)
    comments = of_kind(result, "コメント")
    assert [line["text"] for line in comments] == ["1日のコメント", "2日のコメント", "3日のコメント", "4日のコメント"]
    assert [line["letter"] for line in comments] == [f"便り-2030-03-0{day}T10:00" for day in range(1, 5)]
    assert result.comments_on_selected == 4


def test_選ぶ版で親が流れない田中さんのコメントも流さない(tmp_path):
    rows = posts("2036-03", range(1, 7))
    rows += [
        row("2036-03-04T11:00", "コメント", by="田中", text="流れるコメント"),
        row("2036-03-06T11:00", "コメント", by="田中", text="流れないコメント"),
    ]
    rows.sort(key=lambda item: item["at"])
    for day in ("2036-03-01", "2036-03-02", "2036-03-03", "2036-03-04"):
        mark_day(rows, day, 出来事="010")
    result = run(rows, tmp_path, select=True)
    texts = [line["text"] for line in in_room(result, "誠一さんのそば（田中さんと）")]
    assert texts == ["流れるコメント"]


def test_選ぶ版でも人の部屋と今の様子と代筆と訂正は全部流す(tmp_path):
    rows = posts("2030-03", range(1, 7))
    for day in ("2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"):
        mark_day(rows, day, 出来事="010")
    rows += [
        row("2030-03-06T12:00", "誠一↔正雄", by="正雄", text="人の部屋"),
        row("2030-03-06T13:00", "今の様子", by="美穂", text="今の様子"),
        row("2030-03-06T14:00", "訂正", text="訂正"),
        row("2035-10-02T10:00", "つぶやき", by="正雄", text="代筆", marks={"代筆": "誠一"}),
        row("2035-10-02T11:00", "コメント", by="美穂", text="代筆へのコメント"),
    ]
    result = run(rows, tmp_path, select=True)
    assert in_room(result, "誠一↔正雄")[0]["text"] == "人の部屋"
    assert of_kind(result, "今の様子")[0]["text"] == "今の様子"
    assert of_kind(result, "訂正")[0]["text"] == "訂正"
    assert [line["text"] for line in in_room(result, "誠一さんのそば")] == ["代筆", "代筆へのコメント"]


def test_選ぶ版で流れない本人の行の写真は足りない写真に数えない(tmp_path):
    rows = posts("2030-03", range(1, 7))
    for day in ("2030-03-01", "2030-03-02", "2030-03-03", "2030-03-04"):
        mark_day(rows, day, 出来事="010")
    rows[5]["photos"] = [photo("P6")]
    assert run(rows, tmp_path, select=True).missing_photos == []
    assert run(rows, tmp_path).missing_photos == ["P6"]


def test_選ぶ版で流れない部屋の人は部屋の人に入れない(tmp_path):
    rows = posts("2036-03", range(1, 7))
    for day in ("2036-03-01", "2036-03-02", "2036-03-03", "2036-03-04"):
        mark_day(rows, day, 出来事="010")
    rows += [
        row("2036-03-06T11:00", "コメント", by="田中", text="流れない"),
        row("2036-03-07T09:00", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
    ]
    # 3月6日は選ばれないので、その投稿への田中さんのコメントは流れず、代筆の部屋の人にも招く行にも出ない
    result = run(rows, tmp_path, select=True)
    assert of_kind(result, "部屋")[0]["members"] == ["正雄さん"]
    assert of_kind(result, "招く") == []


# ---- 同じ入力から同じ出力 ----


def small_chat():
    return [
        row("2026-10-01T09:00", "誠一↔美穂", by="美穂"),
        row("2026-10-01T10:00", "つぶやき", photos=[photo("P1"), photo("P2")]),
        row("2026-10-01T11:00", "コメント", by="美穂"),
        row("2030-03-01T10:00", "つぶやき"),
        row("2035-10-02T17:10", "つぶやき", by="正雄", marks={"代筆": "誠一"}),
        row("2035-10-03T17:10", "コメント", by="節子"),
        row("2035-10-04T17:10", "コメント", by="美穂"),
        row("2035-10-05T17:10", "コメント", by="井ノ口"),
        row("2035-10-06T17:10", "コメント", by="健太"),
        row("2036-01-18T12:05", "美穂↔田中", by="田中"),
    ]


def test_同じ入力からは同じ出力になる(tmp_path):
    chat = tmp_path / "チャット.jsonl"
    chat.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in small_chat()), encoding="utf-8")
    first, second = tmp_path / "1.jsonl", tmp_path / "2.jsonl"
    assert main(["--chat", str(chat), "--out", str(first), "--photos", str(tmp_path)]) == 0
    assert main(["--chat", str(chat), "--out", str(second), "--photos", str(tmp_path)]) == 0
    assert first.read_bytes() == second.read_bytes()


def test_プロセスが変わっても同じ出力になる(tmp_path):
    # 文字列の set の順番はプロセスごとに変わる。同じプロセスの比較では、その違いに気づけない
    chat = tmp_path / "チャット.jsonl"
    chat.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in small_chat()), encoding="utf-8")
    outputs = []
    for seed in ("1", "2"):
        out = tmp_path / f"{seed}.jsonl"
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, "-m", "fiction.convert_chat", "--chat", str(chat), "--out", str(out), "--photos", str(tmp_path), "--select"]
        subprocess.run(command, cwd=ROOT, env=env, check=True, capture_output=True)
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


def test_書き出しの改行はLFで1行に1件のJSONになる(tmp_path):
    chat = tmp_path / "チャット.jsonl"
    chat.write_text(json.dumps(row("2026-10-01T10:00", "ツインへ"), ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "script.jsonl"
    assert main(["--chat", str(chat), "--out", str(out), "--photos", str(tmp_path)]) == 0
    raw = out.read_bytes()
    assert b"\r" not in raw
    lines = raw.decode("utf-8").splitlines()
    assert all(json.loads(line)["at"] for line in lines)


def test_標準出力に行数と部屋と足りない写真の数を出す(tmp_path, capsys):
    chat = tmp_path / "チャット.jsonl"
    rows = [row("2026-10-01T10:00", "つぶやき", photos=[photo("P1")]), row("2026-10-01T11:00", "誠一↔美穂", by="美穂")]
    chat.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")
    assert main(["--chat", str(chat), "--out", str(tmp_path / "script.jsonl"), "--photos", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "便り：1" in out
    assert "ツイン：1" in out and "誠一↔美穂：1" in out
    assert "足りない写真：1枚" in out


def test_決まりから外れた入力では何も書かずに止める(tmp_path, capsys):
    chat = tmp_path / "チャット.jsonl"
    chat.write_text(json.dumps(row("2026-10-01T10:00", "どこかの部屋", by="美穂"), ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "script.jsonl"
    assert main(["--chat", str(chat), "--out", str(out), "--photos", str(tmp_path)]) == 2
    assert "止めました" in capsys.readouterr().out
    assert not out.exists()


def test_入力のファイルがなければ止める(tmp_path, capsys):
    code = main(["--chat", str(tmp_path / "ない.jsonl"), "--out", str(tmp_path / "script.jsonl"), "--photos", str(tmp_path)])
    assert code == 2
    assert "入力のファイルがありません" in capsys.readouterr().out
    assert not (tmp_path / "script.jsonl").exists()


# ---- 実データ（置き場がなければ飛ばす） ----


@needs_real_data
def test_実データの全件に決まった言葉34件がすべて入る(tmp_path):
    result = run(read_chat(REAL_CHAT), tmp_path)
    fixed = [json.loads(raw) for raw in REAL_FIXED.read_text(encoding="utf-8").splitlines() if raw.strip()]
    assert len(fixed) == 34
    said = [line.get(key) for line in result.lines for key in ("text", "body") if line.get(key)]
    lost = [item["text"] for item in fixed if not any(item["text"] in text for text in said)]
    assert lost == []


@needs_real_data
def test_実データの選ぶ版は845日で本人の行1285でコメント1093(tmp_path):
    result = run(read_chat(REAL_CHAT), tmp_path, select=True)
    assert len(result.selected_days) == 845
    assert result.self_rows == 1285
    assert result.comments_on_selected == 1093


@needs_real_data
@pytest.mark.parametrize("select", [False, True])
def test_実データで部屋と話し手と時刻の順が崩れない(tmp_path, select):
    result = run(read_chat(REAL_CHAT), tmp_path, select=select, script_guess=True)
    assert_rooms_hold(result.lines)
    assert result.notes == []


@needs_real_data
def test_実データで足す人と代筆の部屋は決まった日に現れる(tmp_path):
    result = run(read_chat(REAL_CHAT), tmp_path)
    assert next(line for line in of_kind(result, "人") if line["name"] == "井ノ口先生")["at"][:10] == "2027-12-14"
    assert next(line for line in of_kind(result, "人") if line["name"] == "健太くん")["at"][:10] == "2031-04-06"
    assert of_kind(result, "招く")[0]["at"][:10] == "2036-01-18"
    rooms = {line["room"]: line for line in of_kind(result, "部屋")}
    assert rooms["誠一さんのそば"]["at"][:10] == "2035-10-02"
    assert "田中さん" not in rooms["誠一さんのそば"]["members"]
    assert "田中さん" in rooms["誠一さんのそば（田中さんと）"]["members"]
    assert rooms["誠一さんのそば（田中さんと）"]["at"] >= of_kind(result, "招く")[0]["at"]


@needs_real_data
def test_実データの代筆の親のコメントの話し手は決まった人数になる():
    rows = read_chat(REAL_CHAT)
    parents = find_parents(rows)
    speakers = Counter(rows[index]["by"] for index, parent in parents.items() if rows[parent]["by"] != "誠一")
    assert speakers == {"美穂": 15, "田中": 5, "正雄": 3, "井ノ口": 2, "健太": 1, "節子": 1}


@needs_real_data
def test_実データの本人だけの行には全部守る行が付く(tmp_path):
    rows = read_chat(REAL_CHAT)
    private = [item["text"] for item in rows if item["place"] == "本人だけ"]
    assert [line["text"] for line in of_kind(run(rows, tmp_path), "本人だけにする")] == private


@needs_real_data
def test_実データの流さない写真はコメントと今の様子の2枚だけ(tmp_path):
    result = run(read_chat(REAL_CHAT), tmp_path)
    assert result.unflowed_photos == [("P20270613-2", "コメント"), ("P20351123-1", "今の様子")]
