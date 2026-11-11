"""script.jsonl を流す道具のテスト。

役割：点検が決まりどおりに見つけること、今ある道だけで流せること、止まっても続きから流せることを確かめる。
4つの極：1. 書く・話す、2. 分かち合う、3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、8（人物と記録はすべて架空）、9（must の言葉は一字も変えない）。

本物の Claude API は呼ばない。通信だけを差し替える。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import ai, config, ledger
from app.main import create_app
from fiction import run_script
from fiction.log_models import LogConversation
from fiction.run_script import Line, Progress, StopRun, check_lines, forbidden_in, run_lines, write_story
from tests.fake_claude import RoleClaude

AT = "2026-04-12T08:"
SETUP = [
    {"at": "2026-04-01T00:00", "kind": "人", "by": "誠一さん", "name": "和子さん", "relation": "妻", "is_deceased": True},
    {"at": "2026-04-01T00:01", "kind": "人", "by": "誠一さん", "name": "美穂さん", "relation": "長女", "residence": "遠く"},
]


def line(**fields) -> Line:
    return Line.model_validate({"at": f"{AT}00", "by": "誠一さん", **fields})


def say(text, minute="10", **fields):
    row = {"at": f"{AT}{minute}", "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text, **fields}
    # 写真だけの発言は text を書かない
    return row if text is not None else {key: value for key, value in row.items() if key != "text"}


def close(minute="30", day="2026-04-12"):
    return {"at": f"{day}T08:{minute}", "kind": "閉じる", "room": "ツイン", "by": "誠一さん"}


def problems(rows, fixed=None):
    lines = [Line.model_validate(row) for row in rows]
    return [item.message for item in check_lines(lines, fixed) if item.problem]


def notices(rows):
    lines = [Line.model_validate(row) for row in rows]
    return [item.message for item in check_lines(lines) if not item.problem]


# ---- 点検 ----


def test_決まりどおりのファイルは問題なし():
    assert problems([*SETUP, say("今朝は縁側でお茶を飲みました。"), close()]) == []


def test_時刻が前の行より前だと問題になる():
    assert any("時刻" in text for text in problems([say("あ。", "20"), say("い。", "10"), close()]))


def test_部屋にいない人の発言は問題になる():
    rows = [
        {"at": f"{AT}00", "kind": "部屋", "by": "誠一さん", "room": "将棋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": f"{AT}10", "kind": "発言", "room": "将棋", "by": "節子さん", "text": "こんにちは。"},
    ]

    assert any("部屋「将棋」にいません" in text for text in problems(rows))


def test_ツインとの部屋で話せるのは本人だけ():
    assert any("いません" in text for text in problems([say("こんにちは。", by="美穂さん")]))


def test_亡くなった和子さんは話し手にできない():
    rows = [*SETUP, {"at": f"{AT}10", "kind": "コメント", "by": "和子さん", "letter": "便り", "text": "はい。"}]

    assert any("話し手にできません" in text for text in problems(rows))


def test_健太くんは人の行で登録すれば話し手にできる():
    person = {"at": f"{AT}00", "kind": "人", "by": "誠一さん", "name": "健太くん", "role": "家族", "relation": "孫"}
    room = {"at": f"{AT}01", "kind": "部屋", "by": "誠一さん", "room": "孫と", "room_kind": "1対1", "members": ["健太くん"]}
    spoken = {"at": f"{AT}02", "kind": "発言", "room": "孫と", "by": "健太くん", "text": "おじいちゃん、こんにちは。"}

    assert problems([person, room, spoken]) == []


def test_健太くんも登録する前は話し手にできない():
    spoken = {"at": f"{AT}00", "kind": "コメント", "by": "健太くん", "letter": "便り", "text": "はい。"}

    assert any("まだいない人" in text for text in problems([spoken]))


def test_田中さんは2036年より前には話せない(monkeypatch):
    monkeypatch.setattr(config, "PROFESSIONAL_FROM", date(2036, 1, 1))
    invite = {"at": "2036-01-02T09:00", "kind": "招く", "by": "美穂さん", "name": "田中さん"}
    early = {"at": "2035-12-31T09:00", "kind": "招く", "by": "美穂さん", "name": "田中さん"}

    assert problems([invite]) == []
    assert any("以降" in text for text in problems([early]))


def test_閉じる前に日付が変わると問題になる():
    rows = [say("あ。"), {**say("い。"), "at": "2026-04-13T08:00"}, close(day="2026-04-13")]

    assert any("閉じる前に日付が変わっています" in text for text in problems(rows))


def test_ツインとの部屋の会話は1日に1回まで():
    rows = [say("あ。", "10"), close("11"), say("い。", "12"), close("13")]

    assert any("1日に1回まで" in text for text in problems(rows))


def test_会話の最初の行は_answerにできない():
    rows = [{"at": f"{AT}10", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"hint": "庭の話"}}, close()]

    assert any("最初の行は answer" in text for text in problems(rows))


def test_textとanswerは片方だけ():
    with pytest.raises(ValueError, match="どちらか1つ"):
        line(kind="発言", room="ツイン", text="あ。", answer={"hint": "庭"})
    with pytest.raises(ValueError, match="どちらか1つ"):
        line(kind="発言", room="ツイン")


def test_answerにはhintかmustがいる():
    with pytest.raises(ValueError, match="hint か must"):
        line(kind="発言", room="ツイン", answer={})


def test_mustは文の切れ目で終わる():
    rows = [say("あ。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": "庭を見るのが"}}, close()]

    assert any("文の切れ目" in text for text in problems(rows))


def test_決まった言葉は同じ日に一字も違わず入っていれば合格():
    word = run_script.FixedWord(date=date(2026, 4, 12), text="毎朝この庭を見るのが楽しみなんです。", by="誠一さん")

    assert problems([say("毎朝この庭を見るのが楽しみなんです。"), close()], [word]) == []


def test_決まった言葉はmustの中にあっても合格():
    word = run_script.FixedWord(date=date(2026, 4, 12), text="毎朝この庭を見るのが楽しみなんです。")
    rows = [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": word.text}}, close()]

    assert problems(rows, [word]) == []


def test_決まった言葉が一字違うと問題になる():
    word = run_script.FixedWord(date=date(2026, 4, 12), text="毎朝この庭を見るのが楽しみなんです。")

    assert any("決まった言葉" in text for text in problems([say("毎朝この庭を見るのが楽しみです。"), close()], [word]))


def test_決まった言葉が別の日にあると問題になり_その日を知らせる():
    word = run_script.FixedWord(date=date(2026, 4, 13), text="庭が好きです。")

    found = problems([say("庭が好きです。"), close()], [word])

    assert any("2026-04-12" in text for text in found)


def test_決まった言葉がhintの中だけなら合格にしない():
    word = run_script.FixedWord(date=date(2026, 4, 12), text="庭が好きです。")
    rows = [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"hint": "庭が好きです。"}}, close()]

    assert any("決まった言葉" in text for text in problems(rows, [word]))


@pytest.mark.parametrize("word", ["認知症", "診断", "脳出血", "誤嚥", "肺炎", "糖尿病", "後遺症", "泊まり", "ショートステイ", "短期入所"])
def test_病名と診断と泊まりの話は問題になる(word):
    assert any(word in text for text in problems([say(f"先週、{word}の話を聞きました。"), close()]))


@pytest.mark.parametrize("field", ["hint", "must"])
def test_answerの中の病名も問題になる(field):
    value = "肺炎になった。"
    rows = [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {field: value}}, close()]

    assert any("肺炎" in text for text in problems(rows))


def test_看病と病院は病名にしない():
    assert forbidden_in("毎日妻の看病で病院へ通いました。") == []


def people_room(*spoken, twin=False):
    """人の部屋を作り、その部屋の発言を並べる。"""
    rows = [{"at": f"{AT}00", "kind": "部屋", "by": "誠一さん", "room": "家族", "room_kind": "グループ", "members": ["美穂さん"]}]
    if twin:
        rows.append({"at": f"{AT}01", "kind": "ツインを入れる", "by": "誠一さん", "room": "家族", "present": True})
    rows += [{"at": f"{AT}10", "kind": "発言", "room": "家族", "by": "美穂さん", "text": text} for text in spoken]
    return rows


@pytest.mark.parametrize(
    "text",
    ["お医者さんに認知症と言われました。", "アルツハイマーの話を聞きました。", "診断の話をしました。", "アルツハイマー型認知症だそうです。", "脳血管性認知症と聞きました。"],
)
def test_ツインのいない部屋の発言では_認知症とアルツハイマーと診断を通す(text):
    assert problems(people_room(text)) == []


@pytest.mark.parametrize("word", ["脳出血", "糖尿病", "後遺症", "泊まり", "ショートステイ"])
def test_ツインのいない部屋でも_ほかの病名と泊まりの語は止める(word):
    assert any(word in text for text in problems(people_room(f"{word}の話をしました。")))


def test_ツインのいない部屋でも_認知症のほかの病名がつけば止める():
    assert any("糖尿病" in text for text in problems(people_room("糖尿病と認知症の話をしました。")))


def test_ツインのいる部屋では_認知症は止める():
    assert any("認知症" in text for text in problems(people_room("認知症と言われました。", twin=True)))


def test_ツインをはずしたあとの部屋では_認知症を通す():
    rows = [
        *people_room(twin=True),
        {"at": f"{AT}05", "kind": "ツインを入れる", "by": "誠一さん", "room": "家族", "present": False},
        {"at": f"{AT}10", "kind": "発言", "room": "家族", "by": "美穂さん", "text": "認知症と言われました。"},
    ]

    assert problems(rows) == []


def test_ツインとの部屋では_認知症は止める():
    assert any("認知症" in text for text in problems([say("認知症と言われました。"), close()]))


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "今の様子", "by": "美穂さん", "text": "認知症と言われました。"},
        {"kind": "便り", "by": "誠一さん", "ref": "便り", "body": "認知症と言われました。", "share_with": "家族"},
    ],
)
def test_発言でない行では_認知症は止める(row):
    rows = [say("おはよう。"), close(), {"at": f"{AT}40", **row}]

    assert any("認知症" in text for text in problems(rows))


def test_写真だけの発言は書ける(tmp_path, monkeypatch):
    (tmp_path / "庭.jpg").write_bytes(b"x")
    monkeypatch.setattr(run_script, "PHOTO_DIR", tmp_path)

    assert problems([say(None, photo="庭.jpg"), close()]) == []


def test_textもanswerも写真もない発言は書けない():
    with pytest.raises(ValueError, match="写真だけなら photo"):
        line(kind="発言", room="ツイン")


def test_便りは閉じる前に出せる():
    letter = {"at": f"{AT}15", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"}

    assert problems([say("おはよう。", "10"), letter, close()]) == []


def test_ツインとの部屋の会話がない日には便りを出せない():
    letter = {"at": f"{AT}15", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"}

    assert any("会話がある日" in text for text in problems([letter]))


def test_段階は決まった言葉だけ書ける():
    with pytest.raises(ValueError):
        line(kind="段階", stage="とても元気")
    with pytest.raises(ValueError, match="stage"):
        line(kind="段階")


def test_段階の行は本人の行にする():
    rows = [{"at": f"{AT}00", "kind": "段階", "by": "美穂さん", "stage": "要支援"}]

    assert any("段階の行" in text for text in problems(rows))


def test_本人だけにするは閉じるのあとに書く():
    rows = [say("庭が好きなんです。"), {"at": f"{AT}11", "kind": "本人だけにする", "by": "誠一さん", "text": "庭が好きなんです。"}, close()]

    assert any("閉じるのあと" in text for text in problems(rows))


def test_本人だけにするは本人のコメントの文も指せる():
    rows = [
        say("おはよう。"),
        close(),
        {"at": f"{AT}31", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"},
        {"at": f"{AT}32", "kind": "コメント", "by": "誠一さん", "letter": "庭", "text": "和子の話は今はしません。"},
        {"at": f"{AT}33", "kind": "本人だけにする", "by": "誠一さん", "text": "和子の話は今はしません。"},
    ]

    assert problems(rows) == []


def test_本人だけにするのtextは本人の発言にある文にする():
    rows = [say("庭が好きなんです。"), close(), {"at": f"{AT}31", "kind": "本人だけにする", "by": "誠一さん", "text": "別の話です。"}]

    assert any("本人の発言に見つかりません" in text for text in problems(rows))


def test_推測の文は言い切らない形にする():
    rows = [say("庭が好きなんです。"), close(), {"at": f"{AT}31", "kind": "推測", "by": "誠一さん", "match": "庭", "text": "庭が大切です。"}]

    assert any("言い切らない" in text for text in problems(rows))


def test_推測の文にも病名は書けない():
    rows = [
        say("庭が好きなんです。"),
        close(),
        {"at": f"{AT}31", "kind": "推測", "by": "誠一さん", "match": "庭", "text": "認知症かもしれません。"},
    ]

    assert any("認知症" in text for text in problems(rows))


def test_推測と本人だけにするの文は_決まった言葉に数えない():
    word = run_script.FixedWord(date=date(2026, 4, 12), text="庭が大切なのかもしれません。")
    rows = [
        say("おはよう。"),
        close(),
        {"at": f"{AT}31", "kind": "推測", "by": "誠一さん", "match": "おはよう", "text": word.text},
    ]

    assert any("決まった言葉" in text for text in problems(rows, [word]))


def test_ツインが決まった文を返す言葉は_問題ではなく知らせる():
    rows = [say("和子が倒れた日のことを思い出します。"), close()]

    assert problems(rows) == []
    assert any("倒れた" in text for text in notices(rows))


def test_checkはAPIもAIも呼ばない(tmp_path, monkeypatch):
    script = tmp_path / "script.jsonl"
    script.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [say("あ。"), close()]), encoding="utf-8")

    def refuse(*_args, **_kwargs):
        raise AssertionError("呼ばない")

    monkeypatch.setattr(run_script, "create_app", refuse)
    monkeypatch.setattr(ai, "_make_client", refuse)
    folder = tmp_path / "流し込み"

    assert run_script.main([str(script), "--check", "--folder", str(folder)]) == 0
    assert not folder.exists()


def test_checkは問題があると1を返す(tmp_path):
    script = tmp_path / "script.jsonl"
    script.write_text(json.dumps(say("認知症です。"), ensure_ascii=False), encoding="utf-8")

    assert run_script.main([str(script), "--check"]) == 1


def test_読めない行は行番号つきで知らせる(tmp_path, capsys):
    script = tmp_path / "script.jsonl"
    script.write_text(json.dumps(say("あ。"), ensure_ascii=False) + "\n{こわれた行}\n", encoding="utf-8")

    assert run_script.main([str(script), "--check"]) == 1
    assert "2行目" in capsys.readouterr().out


# ---- 流す ----


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["不明"] = {"before": "庭を見ています。"}
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def world(tmp_path, claude):
    folder = tmp_path / "流し込み"
    folder.mkdir()
    deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
    app = create_app(deployment=deployment)
    return TestClient(app), deployment.db_path, Progress(folder / "progress.json")


def flow(world, rows, **options):
    client, db_path, progress = world
    return run_lines(client, [Line.model_validate(row) for row in rows], progress, db_path=db_path, **options)


def as_self(client, path):
    return client.get(path, headers={"X-NT-User": "1"}).json()


def statement(body, number, theme="好きなこと"):
    return {
        "source": "本人の言葉",
        "subject": "誠一さん",
        "speaker": "誠一さん",
        "body": body,
        "period": "今",
        "theme": theme,
        "petals": [],
        "utterance_numbers": [number],
        "interpretation": None,
        "confirm_question": None,
    }


def twin_room_id(client):
    return next(item["id"] for item in as_self(client, "/api/rooms")["items"] if item["kind"] == "ツイン")


def messages(client):
    return as_self(client, f"/api/rooms/{twin_room_id(client)}/messages?after=0")["items"]


def test_自分用の起動なら何も送らずに止まる(tmp_path):
    sent = []

    class Mine:
        def get(self, path, **_kwargs):
            return type("R", (), {"status_code": 200, "content": b"1", "json": lambda self: {"mine": True, "label": "自分の記録で試用中"}})()

        def post(self, *args, **_kwargs):
            sent.append(args)

        def request(self, *args, **_kwargs):
            sent.append(args)

    progress = Progress(tmp_path / "progress.json")

    with pytest.raises(StopRun, match="架空用の起動ではありません"):
        run_lines(Mine(), [Line.model_validate(say("あ。"))], progress, db_path=tmp_path / "x.db")
    assert sent == []


@pytest.mark.parametrize("folder", ["data/mine", "data/mine/x", "data", "data/story", "data/story/x"])
def test_自分用の置き場と今の保存先には流さない(folder):
    with pytest.raises(StopRun):
        run_script.safe_folder(config.DATA_DIR.parent / folder)


def test_別のフォルダには流せる(tmp_path):
    assert run_script.safe_folder(tmp_path / "流し込み").name == "流し込み"


def test_前の物語を読み込まない(world):
    client, _, _ = world

    assert as_self(client, f"/api/rooms/{twin_room_id(client)}/messages?after=0")["items"] == []
    assert as_self(client, "/api/statements")["items"] == []


def test_日付を動かして流す(world):
    client, _, _ = world

    flow(world, [say("今朝は縁側でお茶を飲みました。"), close()])

    assert messages(client)[0]["sent_at"].startswith("2026-04-12")


def test_流す時刻はatのとおりになる(world):
    client, _, _ = world

    flow(world, [say("今朝は縁側でお茶を飲みました。", "10"), close("30")])

    assert messages(client)[0]["sent_at"].startswith("2026-04-12T08:10:00")
    closed = as_self(client, "/api/archive")["days"][0]["conversations"][0]["items"]
    assert closed[-1]["sent_at"].startswith("2026-04-12T08:30:00")


def test_同じ時刻の行は1秒ずつずらして_順番を時刻に残す():
    rows = [say("あ。", "10"), say("い。", "10"), say("う。", "10"), say("え。", "11"), close("11")]

    stamps = run_script.spaced_times([Line.model_validate(row) for row in rows])

    assert [stamp.strftime("%H:%M:%S") for stamp in stamps] == ["08:10:00", "08:10:01", "08:10:02", "08:11:00", "08:11:01"]


def test_時刻をずらしても日付は変わらない():
    rows = [{**say(f"{number}。"), "at": "2026-04-12T23:59:58"} for number in range(3)]

    stamps = run_script.spaced_times([Line.model_validate(row) for row in rows])

    assert all(stamp.date() == date(2026, 4, 12) for stamp in stamps)


def test_同じ時刻の行も_発言の順と時刻の順が同じになる(world):
    client, _, _ = world

    flow(world, [say("あ。", "10"), say("い。", "10"), say("う。", "10"), close("10")])

    spoken = [item for item in messages(client) if not item["is_twin"]]
    assert [item["text"] for item in spoken] == ["あ。", "い。", "う。"]
    assert [item["sent_at"] for item in spoken] == sorted(item["sent_at"] for item in spoken)


def test_健太くんを登録すると_話し手として流せる(world):
    client, _, _ = world
    rows = [
        {"at": f"{AT}00", "kind": "人", "by": "誠一さん", "name": "健太くん", "role": "家族", "relation": "孫"},
        say("おはよう。", "10"),
        close("20"),
        {"at": f"{AT}21", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"},
        {"at": f"{AT}30", "kind": "コメント", "by": "健太くん", "letter": "庭", "text": "また行くね。"},
    ]

    assert flow(world, rows) == len(rows)

    assert "健太くん" in {item["name"] for item in client.get("/api/users").json()["items"]}


def test_写真だけの発言を流せる(world, claude, tmp_path, monkeypatch):
    client, _, _ = world
    picture = tmp_path / "写真置き場"
    picture.mkdir()
    Image.new("RGB", (40, 30), "green").save(picture / "庭.jpg")
    monkeypatch.setattr(run_script, "PHOTO_DIR", picture)
    claude.answers["ツインの会話"] = {"reply": "庭の写真ですね。", "photo_description": "緑の写真です。"}

    flow(world, [say(None, photo="庭.jpg"), close()])

    first = messages(client)[0]
    assert first["photo_id"] is not None
    assert first["text"] is None


def test_写真だけの発言を人の部屋にも流せる(world, tmp_path, monkeypatch):
    client, _, _ = world
    picture = tmp_path / "写真置き場"
    picture.mkdir()
    Image.new("RGB", (40, 30), "green").save(picture / "庭.jpg")
    monkeypatch.setattr(run_script, "PHOTO_DIR", picture)
    rows = [
        {"at": f"{AT}00", "kind": "部屋", "by": "誠一さん", "room": "家族", "room_kind": "グループ", "members": ["美穂さん"]},
        {"at": f"{AT}10", "kind": "発言", "room": "家族", "by": "美穂さん", "photo": "庭.jpg"},
    ]

    assert flow(world, rows) == 2


def test_閉じる前の便りは会話を指さない(world):
    client, db_path, _ = world
    rows = [
        say("庭が好きなんです。", "10"),
        {"at": f"{AT}15", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"},
        {"at": f"{AT}20", "kind": "コメント", "by": "美穂さん", "letter": "庭", "text": "また帰ります。"},
        close("30"),
    ]

    flow(world, rows)

    conn = ledger.db.connect(db_path)
    try:
        assert conn.execute("SELECT conversation_id FROM letters").fetchone()[0] is None
        assert conn.execute("SELECT closed_at FROM conversations WHERE room_id IS NOT NULL").fetchone()[0] is not None
    finally:
        conn.close()
    assert [item["body"] for item in as_self(client, "/api/letters")["items"]] == ["庭の話です。"]


def test_閉じたあとの便りは_その日の会話を指す(world):
    _, db_path, _ = world
    rows = [
        say("庭が好きなんです。", "10"),
        close("20"),
        {"at": f"{AT}25", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話です。", "share_with": "家族"},
    ]

    flow(world, rows)

    conn = ledger.db.connect(db_path)
    try:
        assert conn.execute("SELECT conversation_id FROM letters").fetchone()[0] is not None
    finally:
        conn.close()


def stage_line(stage="要支援", when=f"{AT}00"):
    return {"at": when, "kind": "段階", "by": "誠一さん", "stage": stage}


def test_段階の行で人生の段階を切り替える(world):
    _, db_path, _ = world
    next_day = {**say("次の日の話です。"), "at": "2026-04-13T08:10"}

    flow(world, [stage_line(), say("庭が好きなんです。"), close(), next_day, close(day="2026-04-13")])

    conn = ledger.db.connect(db_path)
    try:
        assert ledger.life_stage_on(conn, date(2026, 4, 12)).value == "要支援"
        stages = [row["life_stage"] for row in conn.execute("SELECT life_stage FROM conversations WHERE room_id IS NOT NULL")]
    finally:
        conn.close()
    assert stages == ["要支援", "要支援"]


def test_段階の行は続きから流しても重ならない(world):
    _, db_path, progress = world
    rows = [stage_line()]
    flow(world, rows)
    progress.digests.clear()

    flow(world, rows)

    conn = ledger.db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    finally:
        conn.close()


def test_段階の行だけの日は_これまでの記録の一覧に出ない(world):
    client, _, _ = world
    rows = [
        stage_line(when="2026-04-10T09:00"),
        say("庭が好きなんです。"),
        close(),
    ]

    flow(world, rows)

    assert [day["date"] for day in as_self(client, "/api/archive")["days"]] == ["2026-04-12"]


def test_段階の行はAIを呼ばない(world, claude):
    flow(world, [stage_line()])

    assert claude.requests == []


def words_and_guess():
    """同じ日に、休ませる発言と休ませない発言を話す。"""
    private = "和子の話は、今はしたくありません。"
    other = "庭のつつじが咲きました。"
    answer = {
        "statements": [
            statement(private, 1, theme="つらかったこと"),
            {**statement("つらい日だったのかもしれません。", 1), "source": "AIの推測", "speaker": "ツイン（AI）"},
            # 2番はツインの返事なので、本人の次の発言は3番になる
            statement(other, 3),
        ]
    }
    return private, other, answer


def test_本人だけにするは_その発言の記録だけを休ませる(world, claude):
    client, _, _ = world
    private, other, answer = words_and_guess()
    claude.answers["言明への分割"] = answer

    flow(
        world,
        [
            say(private, "10"),
            say(other, "11"),
            close("20"),
            {"at": f"{AT}21", "kind": "本人だけにする", "by": "誠一さん", "text": private},
        ],
    )

    items = {item["body"]: item for item in as_self(client, "/api/statements")["items"]}
    assert items[private]["confirm_status"] == "お休み"
    assert items[private]["share_scope"] == "本人だけ"
    assert items["つらい日だったのかもしれません。"]["confirm_status"] == "お休み"
    assert items[other]["confirm_status"] == "未確認"


def test_本人だけにするは続きから流しても_行を重ねない(world, claude):
    _, db_path, progress = world
    private, _, answer = words_and_guess()
    claude.answers["言明への分割"] = answer
    rows = [
        say(private, "10"),
        close("20"),
        {"at": f"{AT}21", "kind": "本人だけにする", "by": "誠一さん", "text": private},
    ]
    flow(world, rows)
    progress.digests.pop()
    progress.extra.clear()

    flow(world, rows)

    conn = ledger.db.connect(db_path)
    try:
        resting = conn.execute("SELECT COUNT(*) FROM statements WHERE confirm_status = 'お休み'").fetchone()[0]
    finally:
        conn.close()
    assert resting == 2


def test_本人だけにする発言がまだ分かれていなければ止める(world, claude):
    private, _, _ = words_and_guess()
    claude.answers["言明への分割"] = 400
    rows = [
        say(private, "10"),
        close("20"),
        {"at": f"{AT}21", "kind": "本人だけにする", "by": "誠一さん", "text": private},
    ]

    with pytest.raises(StopRun, match="まだ言明に分かれていません"):
        flow(world, rows)


def test_本人だけにする文はmustの一部でも指せる(world, claude):
    client, _, _ = world
    must = "和子の話は今はしません。"
    claude.answers["言明への分割"] = {"statements": [statement(must, 3)]}
    claude.answers["不明"] = {"before": "そうですね。"}
    rows = [
        say("おはよう。", "10"),
        {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": must}},
        close("20"),
        {"at": f"{AT}21", "kind": "本人だけにする", "by": "誠一さん", "text": must},
    ]

    flow(world, rows)

    assert as_self(client, "/api/statements")["items"][0]["confirm_status"] == "お休み"


def guess_line(minute="31", **fields):
    return {
        "at": f"{AT}{minute}",
        "kind": "推測",
        "by": "誠一さん",
        "match": "庭が好き",
        "text": "庭に思い出があるのかもしれません。",
        **fields,
    }


def test_推測の行は_根拠の発言を指すAIの推測として入り_重ならない(world, claude):
    client, db_path, progress = world
    rows = [say("庭が好きなんです。"), close(), guess_line()]
    flow(world, rows)
    progress.digests.pop()

    flow(world, rows)

    conn = ledger.db.connect(db_path)
    try:
        guesses = conn.execute("SELECT * FROM statements WHERE source = 'AIの推測'").fetchall()
        spoken = conn.execute("SELECT id, text FROM utterances WHERE speaker_is_twin = 0").fetchone()
    finally:
        conn.close()
    assert len(guesses) == 1
    assert json.loads(guesses[0]["source_utterance_ids"]) == [spoken["id"]]
    assert spoken["text"] == "庭が好きなんです。"
    assert guesses[0]["speaker_is_twin"] == 1
    assert guesses[0]["confirm_status"] == "未確認"
    assert guesses[0]["share_scope"] == "本人だけ"
    assert guesses[0]["effective_date"] == "2026-04-12"
    assert not any("庭に思い出があるのかもしれません" in text for text in [claude.sent_text()])


def test_推測の行は本人が確かめられる(world):
    client, _, _ = world
    rows = [
        say("庭が好きなんです。"),
        close(),
        guess_line(),
        {"at": f"{AT}40", "kind": "確かめ", "by": "誠一さん", "match": "庭に思い出がある"},
    ]

    flow(world, rows)

    item = next(row for row in as_self(client, "/api/statements")["items"] if row["source"] == "AIの推測")
    assert item["confirm_status"] == "確認済み"


def test_推測の根拠の発言が見つからなければ止まる(world):
    with pytest.raises(StopRun, match="本人の発言が見つかりません"):
        flow(world, [say("おはよう。"), close(), guess_line()])


def test_推測の行はテーマを指定できる(world):
    client, _, _ = world

    flow(world, [say("庭が好きなんです。"), close(), guess_line(theme="住まい・土地")])

    item = next(row for row in as_self(client, "/api/statements")["items"] if row["source"] == "AIの推測")
    assert item["theme"] == "住まい・土地"


def test_確かめたあとの記録にも_今は話したくないと答えられる(world, claude):
    client, _, _ = world
    words = "庭が好きなんです。"
    claude.answers["言明への分割"] = {"statements": [statement(words, 1)]}
    rows = [
        say(words),
        close(),
        {"at": f"{AT}31", "kind": "確かめ", "by": "誠一さん", "match": "庭が好き"},
        {"at": f"{AT}32", "kind": "確かめ", "by": "誠一さん", "match": "庭が好き", "reply": "今は話したくない"},
    ]

    flow(world, rows)

    assert as_self(client, "/api/statements")["items"][0]["confirm_status"] == "お休み"


def test_同じ文の今の様子は別の日でもそれぞれ入る(world):
    client, _, _ = world
    rows = [
        {"at": "2026-04-12T17:00", "kind": "今の様子", "by": "美穂さん", "text": "元気でした。"},
        {"at": "2026-04-13T17:00", "kind": "今の様子", "by": "美穂さん", "text": "元気でした。"},
    ]

    flow(world, rows)

    days = sorted(item["effective_date"] for item in as_self(client, "/api/statements")["items"])
    assert days == ["2026-04-12", "2026-04-13"]


def test_textは一字も変えずに送る(world):
    client, _, _ = world
    text = "今朝は縁側でお茶を、飲みました！"

    flow(world, [say(text), close()])

    assert messages(client)[0]["text"] == text


def test_人の行でnullを書くと_その項目を空にする(world):
    client, _, _ = world

    flow(world, [{"at": "2026-04-01T00:00", "kind": "人", "by": "誠一さん", "name": "和子さん", "residence": None, "is_deceased": True}])

    people = {item["name"]: item for item in as_self(client, "/api/people")["items"]}
    assert people["和子さん"]["residence"] is None


def test_人の行で和子さんを亡くなった方にすると_ログインできなくなる(world):
    client, _, _ = world

    flow(world, SETUP)

    assert "和子さん" not in {item["name"] for item in client.get("/api/users").json()["items"]}
    people = {item["name"]: item for item in as_self(client, "/api/people")["items"]}
    assert people["和子さん"]["is_deceased"] is True
    assert people["美穂さん"]["residence"] == "遠く"


def test_answerはツインの実際の言葉を見て作り_mustを一字も変えない(world, claude):
    client, _, _ = world
    claude.answers["ツインの会話"] = {"reply": "目印の受け止めです。", "question": "目印の問いはどうでしょうか？"}
    must = "毎朝この庭を見るのが楽しみなんです。"

    flow(
        world,
        [
            say("おはよう。"),
            {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"hint": "庭の話", "must": must}},
            close(),
        ],
    )

    sent = [item["text"] for item in messages(client) if not item["is_twin"]]
    assert must in sent[1]
    asked = json.dumps(claude.bodies("不明"), ensure_ascii=False)
    assert "目印の問いはどうでしょうか？" in asked


def test_mustの前の文がつながっていれば作り直し_それでもだめならmustだけを送る(world, claude):
    client, _, progress = world
    claude.answers["不明"] = {"before": "和子の庭なので、"}
    must = "毎朝この庭を見るのが楽しみなんです。"

    flow(
        world,
        [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": must}}, close()],
    )

    assert len(claude.bodies("不明")) == 2
    assert [item["text"] for item in messages(client) if not item["is_twin"]][1] == must
    assert any("must の文だけ" in text for text in progress.warnings)


def test_mustがほかの言葉につながっていたら作り直す(world, claude):
    client, _, _ = world
    must = "毎朝この庭を見るのが楽しみなんです。"
    claude.answers["不明"] = [{"before": "和子の庭なので、"}, {"before": "和子の庭です。", "after": "朝がいいですね。"}]

    flow(
        world,
        [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": must}}, close()],
    )

    assert len(claude.bodies("不明")) == 2
    assert [item["text"] for item in messages(client) if not item["is_twin"]][1] == f"和子の庭です。{must}朝がいいですね。"


def test_答えのmustはAIの書き写しではなくプログラムが入れる(world, claude):
    client, _, _ = world
    must = "毎朝この庭を、見るのが楽しみなんです！"
    claude.answers["不明"] = {"before": "", "after": "別の言い方はしません。"}

    flow(
        world,
        [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": must}}, close()],
    )

    assert [item["text"] for item in messages(client) if not item["is_twin"]][1] == f"{must}別の言い方はしません。"


def test_mustが1つの文かを見分ける():
    must = "庭が好きです。"

    assert run_script.stands_alone(must, "庭が好きです。")
    assert run_script.stands_alone(must, "そうですね。庭が好きです。毎朝見ます。")
    assert run_script.stands_alone(must, "はい！ 庭が好きです。")
    assert not run_script.stands_alone(must, "和子の庭が好きです。")
    assert not run_script.stands_alone(must, "そうですね、庭が好きです。")


def test_hintだけの答えが作れなければ止まる(world, claude):
    claude.answers["不明"] = {"before": "先週、肺炎の話を聞きました。"}

    with pytest.raises(StopRun, match="筋に合う答え"):
        flow(world, [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"hint": "庭の話"}}, close()])


def test_閉じたら裏の処理を待ってから次の行へ進む(world, claude):
    client, _, _ = world
    words = "毎朝この庭を見るのが楽しみなんです。"
    claude.answers["言明への分割"] = {"statements": [statement(words, 1)]}

    flow(world, [say(words), close(), {"at": f"{AT}31", "kind": "確かめ", "by": "誠一さん", "match": "庭を見る"}])

    items = as_self(client, "/api/statements")["items"]
    assert items[0]["body"] == words
    assert items[0]["confirm_status"] == "確認済み"


def test_訂正は元の行を残して新しい行を足す(world, claude):
    client, db_path, _ = world
    words = "庭が好きなんです。"
    claude.answers["言明への分割"] = {"statements": [statement(words, 1)]}

    flow(
        world,
        [
            say(words),
            close(),
            {"at": f"{AT}31", "kind": "訂正", "by": "誠一さん", "match": "庭が好き", "text": "和子と暮らした場所だから大切なんです。"},
        ],
    )

    bodies = [item["body"] for item in as_self(client, "/api/statements")["items"]]
    assert "和子と暮らした場所だから大切なんです。" in bodies
    assert words not in bodies
    conn = ledger.db.connect(db_path)
    try:
        kept = [row["body"] for row in conn.execute("SELECT body FROM statements")]
    finally:
        conn.close()
    assert words in kept


def test_記録が見つからない確かめは止まる_keep_goingなら書き残して続ける(world, claude):
    client, db_path, progress = world
    rows = [say("おはよう。"), close(), {"at": f"{AT}31", "kind": "確かめ", "by": "誠一さん", "match": "ない言葉"}]

    with pytest.raises(StopRun, match="見つかりません"):
        flow(world, rows)

    assert run_lines(client, [Line.model_validate(row) for row in rows], progress, db_path=db_path, keep_going=True) == 1
    assert any("ない言葉" in text for text in progress.warnings)


def full_story():
    return [
        *SETUP,
        {"at": "2026-04-01T00:02", "kind": "部屋", "by": "誠一さん", "room": "将棋の部屋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": "2026-04-01T00:03", "kind": "託す", "by": "誠一さん", "delegates": ["美穂さん"], "themes": ["好きなこと"]},
        say("庭が好きなんです。", topic="縁側の朝"),
        close(),
        {"at": f"{AT}31", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話をしました。", "share_with": "縁のある人まで"},
        {"at": f"{AT}40", "kind": "コメント", "by": "美穂さん", "letter": "庭", "text": "また帰ります。"},
        {"at": f"{AT}41", "kind": "コメント", "by": "節子さん", "letter": "庭", "text": "きれいな庭です。"},
        {"at": f"{AT}50", "kind": "今の様子", "by": "美穂さん", "text": "庭を眺めていました。"},
        {"at": f"{AT}55", "kind": "発言", "room": "将棋の部屋", "by": "正雄さん", "text": "また指しましょう。"},
        {"at": "2036-04-01T10:00", "kind": "招く", "by": "美穂さん", "name": "田中さん", "relation": "ケアマネジャー"},
        {"at": "2036-04-01T10:05", "kind": "今の様子", "by": "田中さん", "text": "縁側で落ち着いておられました。", "at_meeting": True},
    ]


def test_全部の種類の行を今ある道で流せる(world):
    client, _, progress = world

    assert flow(world, full_story()) == len(full_story())

    assert [item["body"] for item in as_self(client, "/api/letters")["items"]] == ["庭の話をしました。"]
    assert {item["name"] for item in client.get("/api/users").json()["items"]} >= {"田中さん"}
    assert progress.warnings == []


def test_止めたところから続きを流せて_発言は増えない(world, claude):
    client, _, progress = world
    rows = full_story()

    assert flow(world, rows, stop_after=5) == 5
    assert len(progress.digests) == 5
    assert flow(world, rows) == len(rows) - 5

    assert sum(1 for item in messages(client) if not item["is_twin"]) == 1


def test_会話の途中で止めて開き直しても_会話は閉じない(tmp_path, claude):
    script = tmp_path / "script.jsonl"
    rows = [say("庭が好きなんです。", "10"), say("毎朝見ています。", "11"), close()]
    script.write_text(chr(10).join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    folder = tmp_path / "流し込み"

    assert run_script.main([str(script), "--folder", str(folder), "--stop-after", "1"]) == 0
    assert run_script.main([str(script), "--folder", str(folder)]) == 0

    client, _ = run_script.open_client(folder)
    client.post("/api/dev/date", json={"date": "2026-04-12"})
    conn = ledger.db.connect(folder / "narrative_twin.db")
    try:
        conversations = conn.execute("SELECT id FROM conversations WHERE trigger IS NULL OR trigger <> ''").fetchall()
    finally:
        conn.close()
    assert len(conversations) == 1
    assert [item["text"] for item in messages(client) if not item["is_twin"]] == ["庭が好きなんです。", "毎朝見ています。"]


def test_整理の行で固める処理を流す(world, claude):
    client, db_path, _ = world
    words = "庭が好きなんです。"
    claude.answers["言明への分割"] = {"statements": [statement(words, 1)]}

    flow(world, [say(words), close(), {"at": f"{AT}40", "kind": "整理", "by": "誠一さん"}])

    conn = ledger.db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM consolidation_runs").fetchone()[0] >= 1
    finally:
        conn.close()


def test_送ったあとで止まっても_送り直して発言は増えない(world):
    client, _, progress = world
    rows = [say("庭が好きなんです。"), close()]
    flow(world, rows[:1])
    # 発言を送ったが、終わった印を書く前に止まった状態にする
    progress.digests.clear()
    progress.room_seq.clear()

    flow(world, rows)

    assert sum(1 for item in messages(client) if not item["is_twin"]) == 1


def test_便りを送ったあとで止まっても_便りは増えない(world):
    client, _, progress = world
    rows = [
        say("庭が好きなんです。"),
        close(),
        {"at": f"{AT}31", "kind": "便り", "by": "誠一さん", "ref": "庭", "body": "庭の話をしました。", "share_with": "家族"},
    ]
    flow(world, rows)
    progress.digests.pop()
    progress.sending = 3

    flow(world, rows)

    assert len(as_self(client, "/api/letters")["items"]) == 1


def test_今の様子を送ったあとで止まっても_増えない(world):
    client, _, progress = world
    rows = [{"at": f"{AT}50", "kind": "今の様子", "by": "美穂さん", "text": "庭を眺めていました。"}]
    flow(world, rows)
    progress.digests.pop()
    progress.sending = 1

    flow(world, rows)

    assert sum(1 for item in as_self(client, "/api/statements")["items"] if item["body"] == "庭を眺めていました。") == 1


def test_流した行を変えたら止まる(world):
    flow(world, [say("庭が好きなんです。"), close()])

    with pytest.raises(StopRun, match="変わっています"):
        flow(world, [say("庭が大好きなんです。"), close()])


def test_あとに行を足して続きを流せる(world):
    flow(world, [say("庭が好きなんです。"), close()])

    assert flow(world, [say("庭が好きなんです。"), close(), {"at": f"{AT}50", "kind": "今の様子", "by": "美穂さん", "text": "元気です。"}]) == 1


def test_指定の日までで止まる(world):
    rows = full_story()

    count = flow(world, rows, until=date(2026, 4, 12))

    assert count == len(rows) - 2


def test_費用の枠を超えそうなら止まる(world):
    _, _, progress = world
    progress.answer_usd = 9.0

    with pytest.raises(StopRun, match="費用の枠"):
        flow(world, [say("庭が好きなんです。")], budget_usd=5.0)


def test_今のログの形で別の場所に書き出す(world, tmp_path):
    _, _, progress = world
    flow(world, full_story())

    written = write_story(progress, tmp_path / "出力")

    log = json.loads((tmp_path / "出力" / "log_2026.json").read_text(encoding="utf-8"))
    conversation = LogConversation.model_validate(log["2026-04-12"])
    assert conversation.trigger == "縁側の朝"
    assert conversation.turns[0].text == "庭が好きなんです。"
    assert conversation.letter == "庭の話をしました。"
    assert {item.speaker for item in conversation.comments} == {"美穂さん", "節子さん"}
    extra = json.loads((tmp_path / "出力" / "extra_2026.json").read_text(encoding="utf-8"))
    assert {item["kind"] for item in extra} == {"今の様子", "部屋の発言"}
    assert all(Path(path).parent == tmp_path / "出力" for path in written)


def test_訂正があった日はログの確かめが直したになる(world, claude, tmp_path):
    _, _, progress = world
    claude.answers["言明への分割"] = {"statements": [statement("庭が好きなんです。", 1)]}
    flow(
        world,
        [
            say("庭が好きなんです。"),
            close(),
            {"at": f"{AT}31", "kind": "訂正", "by": "誠一さん", "match": "庭が好き", "text": "場所が大切なんです。"},
        ],
    )

    write_story(progress, tmp_path / "出力")

    log = json.loads((tmp_path / "出力" / "log_2026.json").read_text(encoding="utf-8"))
    assert log["2026-04-12"]["confirmation"] == "直した"


def test_試しのファイルは決まりどおり():
    path = Path(run_script.__file__).parent / "sample_script.jsonl"

    lines, read_problems = run_script.read_script(path)

    assert read_problems == []
    assert [item for item in check_lines(lines) if item.problem] == []


def test_写真つきの発言を流せる(world, claude, tmp_path, monkeypatch):
    client, _, _ = world
    picture = tmp_path / "写真置き場"
    picture.mkdir()
    Image.new("RGB", (40, 30), "green").save(picture / "庭.jpg")
    monkeypatch.setattr(run_script, "PHOTO_DIR", picture)
    claude.answers["ツインの会話"] = {"reply": "庭の写真ですね。", "photo_description": "緑の写真です。"}

    flow(world, [say("庭の写真です。", photo="庭.jpg"), close()])

    assert messages(client)[0]["photo_id"] is not None


def test_グループにツインを入れて流せる(world):
    client, _, _ = world
    rows = [
        {"at": f"{AT}00", "kind": "部屋", "by": "誠一さん", "room": "将棋", "room_kind": "グループ", "members": ["正雄さん"]},
        {"at": f"{AT}01", "kind": "ツインを入れる", "by": "誠一さん", "room": "将棋", "present": True},
        {"at": f"{AT}02", "kind": "発言", "room": "将棋", "by": "正雄さん", "text": "また指しましょう。"},
    ]

    assert flow(world, rows) == 3

    room = next(item for item in as_self(client, "/api/rooms")["items"] if item["name"] == "将棋")
    assert room["twin_present"] is True


def test_今は話したくないと答えると_その記録を休ませる(world, claude):
    client, _, _ = world
    words = "庭が好きなんです。"
    claude.answers["言明への分割"] = {"statements": [statement(words, 1)]}

    flow(world, [say(words), close(), {"at": f"{AT}31", "kind": "確かめ", "by": "誠一さん", "match": "庭が好き", "reply": "今は話したくない"}])

    assert as_self(client, "/api/statements")["items"][0]["confirm_status"] == "お休み"


def test_流し込みの別のデータベースにもデモの顔が入る(tmp_path, monkeypatch):
    from PIL import Image

    from fiction import faces

    source = tmp_path / "faces"
    source.mkdir()
    for name in faces.FACE_FILES.values():
        Image.new("RGB", (64, 64), (200, 180, 150)).save(source / name, format="JPEG")
    monkeypatch.setattr(config, "DEMO_FACES_DIR", source)
    folder = tmp_path / "流し込み"

    _, db_path = run_script.open_client(folder)
    run_script.open_client(folder)

    conn = ledger.db.connect(db_path)
    try:
        filled = conn.execute("SELECT name FROM users WHERE photo_id IS NOT NULL AND profile_photo_id IS NOT NULL ORDER BY id").fetchall()
        count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        conn.close()
    assert [row["name"] for row in filled] == ["誠一さん", "美穂さん", "正雄さん", "節子さん"]
    assert count == 8
    assert (folder / "photos").is_dir()


def test_写真のフォルダがなくても流し込みの別のデータベースを開ける(tmp_path):
    client, db_path = run_script.open_client(tmp_path / "流し込み")

    assert client.get("/api/users").status_code == 200
    conn = ledger.db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0
    finally:
        conn.close()
