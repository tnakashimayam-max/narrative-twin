"""架空の記録で足す、本人の短い受け答えのテスト。

役割：足した文が画面の会話にだけ出て記録にならないこと、ツインの次の返事が読めること、点検と時期の決まりを確かめる。
4つの極：1. 書く・話す（架空の記録を作る道具）。
守る原則：3（出どころを分ける）、7（答えのない問いを重ねない）、8（すべて架空）、9（チャットの行は一字も変えない）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import ai, config, db
from app.main import create_app
from fiction import added_words, answer_plan
from fiction.run_script import Line, Progress, check_lines, forbidden_in, run_lines
from tests.fake_claude import RoleClaude, _user_text

AT = "2026-10-11T08:0"
ADDED = "炊きたてたい。"
QUESTION = {"reply": "炊きたては香りがいいですね。", "question": "今朝は何を炊きましたか？", "question_theme": "好きなこと", "care": "なし", "previous_question_result": None}
NO_QUESTION = {"reply": "そうでしたか。", "question": None, "question_theme": None, "care": "なし", "previous_question_result": None}
FACT = "### この日の事実\n誠一の1日：夕は炊きたて1合と鯖の味噌煮。"


@pytest.fixture
def fake(monkeypatch):
    claude = RoleClaude()
    claude.answers["架空の受け答え"] = {"text": ADDED}
    claude.answers["架空の受け答えの確かめ"] = {"new_people": [], "new_events": [], "new_numbers": [], "not_chikugo": None}
    monkeypatch.setattr(ai, "_make_client", claude.client)
    return claude


@pytest.fixture
def world(tmp_path, fake):
    folder = tmp_path / "流し込み"
    folder.mkdir()
    deployment = replace(config.deployment(data_dir=folder), log_path=folder / "log.jsonl")
    app = create_app(deployment=deployment)
    return TestClient(app), deployment.db_path, Progress(folder / "progress.json")


def say(minute, text, **more):
    return {"at": f"{AT}{minute}", "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text, **more}


def added(minute, **more):
    return {"at": f"{AT}{minute}", "kind": "受け答え", "by": "誠一さん", "room": "ツイン", "hint": FACT, "limit": 25, **more}


def close(minute):
    return {"at": f"{AT}{minute}", "kind": "閉じる", "room": "ツイン", "by": "誠一さん"}


def run(world, rows):
    client, db_path, progress = world
    run_lines(client, [Line.model_validate(row) for row in rows], progress, db_path=db_path, workers=0)
    return client, db_path, progress


def twin_room(client):
    connection = db.connect(client.app.state.db_path)
    room = connection.execute("SELECT id FROM rooms WHERE kind = 'ツイン'").fetchone()["id"]
    connection.close()
    return room


def messages(client):
    connection = db.connect(client.app.state.db_path)
    user_id = db.self_user(connection).id
    connection.close()
    return client.get(f"/api/rooms/{twin_room(client)}/messages?after=0", headers={"X-NT-User": str(user_id)}).json()["items"]


def rows_of(db_path, sql, *args):
    connection = db.connect(db_path)
    try:
        return [tuple(row) for row in connection.execute(sql, args)]
    finally:
        connection.close()


# ---- 問いがあるときだけ足す ----


def test_問いがあれば本人の短い受け答えが画面に出て_ツインが受け止めて終わる(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    client, _, _ = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True)])

    items = messages(client)
    texts = [item["text"] for item in items]
    assert texts == ["飯は炊きたてが一番たい。", QUESTION["reply"], QUESTION["question"], ADDED, NO_QUESTION["reply"]]
    assert not items[-1]["is_question"]


def test_問いがなければ足さない(world, fake):
    fake.answers["ツインの会話"] = [NO_QUESTION]
    client, _, _ = run(world, [say(1, "雨だな。"), added(2, twin_replies=True)])

    assert ADDED not in [item["text"] for item in messages(client)]
    assert fake.bodies("架空の受け答え") == []


def test_足した文のあとにチャットの行を流すと_ツインの返事は一度だけ(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    client, _, _ = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2), say(3, "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。", ask=False)])

    assert [item["text"] for item in messages(client)] == [
        "飯は炊きたてが一番たい。",
        QUESTION["reply"],
        QUESTION["question"],
        ADDED,
        "一人で長風呂に入る時間だけは、誰にも邪魔されとうなか。",
        NO_QUESTION["reply"],
    ]


def test_足した文で問いは答えた扱いになり_答えのない問いが残らない(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    _, db_path, _ = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True), close(3)])

    assert rows_of(db_path, "SELECT result FROM question_logs WHERE asked_on IS NOT NULL") == [("答えた",)]


# ---- 記録にならない ----


def test_足した文は発言の表に入らず_言明にならず_ログにも本人の発言として残らない(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    _, db_path, progress = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True), close(3)])

    assert rows_of(db_path, "SELECT 1 FROM utterances WHERE text LIKE ?", f"%{ADDED}%") == []
    assert rows_of(db_path, "SELECT 1 FROM statements WHERE body LIKE ?", f"%{ADDED}%") == []
    assert ADDED not in [turn["text"] for entry in progress.story.values() for turn in entry["turns"]]
    assert [item["text"] for item in progress.extra["2026"] if item.get("kind") == "足した文"] == [ADDED]


def test_ツインの次の返事には足した文が渡り_分割と要約には渡らない(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True), close(3)])

    assert ADDED in _user_text(fake.bodies("ツインの会話")[-1])
    assert ADDED in _user_text(fake.bodies("今日はここまで")[-1])
    assert all(ADDED not in _user_text(body) for body in fake.bodies("言明への分割"))
    assert all(ADDED not in _user_text(body) for body in fake.bodies("会話の要約"))
    assert all(ADDED not in _user_text(body) for body in fake.bodies("人の候補"))


# ---- 問わない日 ----


def test_問わない日はツインの問いが画面にも記録にも出ない(world, fake):
    fake.answers["ツインの会話"] = [QUESTION]
    client, db_path, _ = run(world, [say(1, "雨だな。", ask=False), close(2)])

    assert not any(item["is_question"] for item in messages(client))
    assert rows_of(db_path, "SELECT 1 FROM question_logs WHERE asked_on IS NOT NULL") == []


def test_ふだんの起動ではツインは問える(world):
    client, _, _ = world

    assert client.app.state.twin_may_ask is True


def letter_lines(ask):
    comment = {"at": f"{AT}4", "kind": "コメント", "by": "美穂さん", "letter": "便り1", "text": "届いとるよ。"}
    if not ask:
        comment["ask"] = False
    return [
        say(1, "飯は炊きたてが一番たい。", ask=False),
        {"at": f"{AT}2", "kind": "便り", "by": "誠一さん", "ref": "便り1", "body": "飯は炊きたてが一番たい。", "share_with": "縁のある人まで"},
        close(3),
        comment,
    ]


def test_問わないコメントでは_スレッドの問いのAIを呼ばない(world, fake):
    fake.answers["ツインの会話"] = [NO_QUESTION]
    run(world, letter_lines(ask=False))

    assert fake.bodies("スレッドの問い") == []


def test_便りの問いに足した文はコメントに出るが言明にならず_問いが答えた扱いになる(world, fake):
    fake.answers["ツインの会話"] = [NO_QUESTION]
    fake.answers["スレッドの問い"] = {"question": "今朝は何を炊きましたか？"}
    client, db_path, progress = run(
        world,
        [
            *letter_lines(ask=True),
            {"at": f"{AT}5", "kind": "受け答え", "by": "誠一さん", "letter": "便り1", "hint": FACT, "limit": 25},
            {"at": f"{AT}6", "kind": "コメント", "by": "美穂さん", "letter": "便り1", "text": "よかったね。", "ask": False},
        ],
    )

    assert rows_of(db_path, "SELECT 1 FROM comments WHERE body = ?", ADDED) == [(1,)]
    assert rows_of(db_path, "SELECT 1 FROM statements WHERE body LIKE ?", f"%{ADDED}%") == []
    assert rows_of(db_path, "SELECT result FROM question_logs WHERE asked_on IS NOT NULL") == [("答えた",)]
    assert all(f"] {ADDED}" not in _user_text(body) and f"：{ADDED}" not in _user_text(body).split("発言")[-1] for body in fake.bodies("言明への分割")[-1:])
    assert [item["text"] for item in progress.extra["2026"] if item.get("kind") == "足した文"] == [ADDED]


# ---- 点検 ----


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("認知症たい。", "書かない言葉"),
        ("泊まりはせん。", "書かない言葉"),
        ("ほんまに旨か。", "関西"),
        ("それはええ。", "関西"),
        ("知らへん。", "関西"),
        ("わしは炊く。", "一人称"),
        ("私が炊きました。", "一人称"),
        ("炊きました。", "丁寧語"),
        ("米は3合たい。", "数字"),
        ("米は五合たい。", "数字"),
        ("炊きたてが一番に決まっとるたい。なんちゅうても飯ばい。", "字以内"),
        ("そげんこつは、よか。", "言葉にない"),
    ],
)
def test_足す文の決まりに合わない文を止める(text, word):
    assert word in added_words.added_problem(text, hint=FACT, limit=25, forbidden=forbidden_in)


@pytest.mark.parametrize("text", ["炊きたてたい。", "1合炊いた。", "忘れた。", "俺が炊く。", "また今度たい。"])
def test_筑後弁の短い受け答えは通す(text):
    assert added_words.added_problem(text, hint=FACT, limit=25, forbidden=forbidden_in) is None


def test_物忘れの物語が始まる前は_忘れたと言わせない():
    assert "忘れた" in added_words.added_problem("忘れた。", hint=FACT, limit=25, forbidden=forbidden_in, day="2026-10-11")
    assert added_words.added_problem("忘れた。", hint=FACT, limit=25, forbidden=forbidden_in, day="2033-01-01") is None


def test_最近と同じ受け答えは書き直させる():
    assert "別の言葉" in added_words.added_problem("よか。", hint=FACT, limit=25, forbidden=forbidden_in, recent=("よか。",))


def test_新しい人や出来事があると書き直させる(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    fake.answers["架空の受け答え"] = [{"text": "太郎が来たたい。"}, {"text": ADDED}]
    fake.answers["架空の受け答えの確かめ"] = [
        {"new_people": ["太郎"], "new_events": [], "new_numbers": [], "not_chikugo": None},
        {"new_people": [], "new_events": [], "new_numbers": [], "not_chikugo": None},
    ]
    client, _, progress = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True)])

    assert ADDED in [item["text"] for item in messages(client)]
    record = next(item for item in progress.extra["2026"] if item.get("kind") == "足した文")
    assert record["tries"] == 2 and "太郎" in record["reasons"][0]
    assert "太郎" in _user_text(fake.bodies("架空の受け答え")[-1])


def test_書き直しても通らなければ決まった短い言葉にする(world, fake):
    fake.answers["ツインの会話"] = [QUESTION, NO_QUESTION]
    fake.answers["架空の受け答え"] = {"text": "ほんまに旨か。"}
    client, _, progress = run(world, [say(1, "飯は炊きたてが一番たい。"), added(2, twin_replies=True)])

    assert added_words.fallback_for(25) in [item["text"] for item in messages(client)]
    record = next(item for item in progress.extra["2026"] if item.get("kind") == "足した文")
    assert record["fallback"] is True and record["tries"] == added_words.MAX_TRIES


# ---- 本人の時期 ----


@pytest.mark.parametrize(
    ("day", "limit"),
    [
        ("2032-09-30", added_words.LIMIT_FIRST),
        ("2032-10-01", added_words.LIMIT_SHORT),
        ("2034-09-30", added_words.LIMIT_SHORT),
        ("2034-10-01", added_words.LIMIT_BROKEN),
        ("2035-09-30", added_words.LIMIT_BROKEN),
        ("2035-10-01", None),
        ("2036-05-06", None),
    ],
)
def test_足す文の長さは本人の時期に合わせ_2035年10月からは足さない(day, limit):
    assert added_words.limit_for(day) == limit


# ---- 案の行の並び ----


def chat(at, place, text, by="誠一", marks=None):
    return {"at": at, "place": place, "by": by, "text": text, "marks": marks or {}, "photos": []}


def post(at, text):
    return {"at": at, "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text}


def plan(lines, rows, future_log=None):
    return answer_plan.apply_answers(lines, rows, future_log=future_log or {}, forbidden=forbidden_in)


def test_対象の日で本人の行が1つなら_問うて受け答えのあとにツインが返す():
    rows = [chat("2026-10-11T08:00", "つぶやき", "飯は炊きたてが一番たい。", marks={"決まった": "本02"})]

    result = plan([post("2026-10-11T08:00", "飯は炊きたてが一番たい。")], rows)

    assert "ask" not in result[0]
    assert result[1]["kind"] == "受け答え" and result[1]["twin_replies"] is True and result[1]["limit"] == 25


def test_対象の日で本人の行が2つなら_最初の行で問い_最後の行では問わない():
    rows = [chat("2030-05-01T08:00", "つぶやき", "雨たい。"), chat("2030-05-01T21:00", "ツインへ", "和子のことば話す。")]

    result = plan([post("2030-05-01T08:00", "雨たい。"), post("2030-05-01T21:00", "和子のことば話す。")], rows)

    assert [line["kind"] for line in result] == ["発言", "受け答え", "発言"]
    assert result[1]["twin_replies"] is False
    assert result[2]["ask"] is False


def test_対象でない日はツインが問わず_受け答えも足さない():
    rows = [chat("2030-05-01T08:00", "つぶやき", "雨たい。")]

    result = plan([post("2030-05-01T08:00", "雨たい。")], rows)

    assert [line["kind"] for line in result] == ["発言"]
    assert result[0]["ask"] is False


def test_2035年10月からは動画の期間でもツインが問わない():
    rows = [chat("2036-02-01T08:00", "つぶやき", "炊いた。")]

    result = plan([post("2036-02-01T08:00", "炊いた。")], rows)

    assert [line["kind"] for line in result] == ["発言"]
    assert result[0]["ask"] is False


def test_コメントの問いは対象の日の最初の人のコメントでだけ出す():
    rows = [
        chat("2026-10-11T08:00", "つぶやき", "炊いた。"),
        chat("2030-05-01T08:00", "つぶやき", "雨たい。"),
    ]
    comment = lambda at, by, letter: {"at": at, "kind": "コメント", "by": by, "letter": letter, "text": "よかね。"}
    lines = [
        comment("2026-10-11T09:00", "誠一さん", "便り1"),
        comment("2026-10-11T09:01", "美穂さん", "便り1"),
        comment("2026-10-11T09:02", "正雄さん", "便り1"),
        comment("2030-05-01T09:00", "美穂さん", "便り2"),
    ]

    result = plan(lines, rows)

    assert [(line["kind"], line.get("ask")) for line in result] == [
        ("コメント", False),
        ("コメント", None),
        ("受け答え", None),
        ("コメント", False),
        ("コメント", False),
    ]


def test_チャットの本文は一字も変えない():
    text = "庭の世話があるけん、ここは離れられん。"
    rows = [chat("2026-10-25T21:40", "ツインへ", text)]

    result = plan([post("2026-10-25T21:40", text)], rows)

    assert result[0]["text"] == text


def test_渡す事実に病名の文と本人のいない部屋の話を入れない():
    rows = [
        chat("2033-03-18T07:00", "誠一↔美穂", "よか。"),
        chat("2033-03-18T07:30", "美穂↔井ノ口", "検査の話です。", by="井ノ口"),
        chat("2033-03-18T18:00", "つぶやき", "雨たい。", marks={"決まった": "本"}),
    ]
    log = {"2033-03-18": {"誠一の1日": "朝に雨戸を開ける。認知症の話を聞く。夕は炊きたて。", "天気": "雨"}}

    hint = plan([post("2033-03-18T18:00", "雨たい。")], rows, log)[1]["hint"]

    assert "認知症" not in hint
    assert "雨戸" in hint and "炊きたて" in hint
    assert "検査の話" not in hint
    assert "よか" in hint


def test_渡す事実にその時刻より後のチャットを入れない():
    # 後の行を渡すと、チャットの次の行を写して同じ文が2回並ぶ
    rows = [
        chat("2026-10-28T18:00", "つぶやき", "夕はサンマ。"),
        chat("2026-10-28T18:41", "コメント", "当たり前たい。苦かところが旨か。"),
    ]

    hint = plan([post("2026-10-28T18:00", "夕はサンマ。")], rows)[1]["hint"]

    assert "夕はサンマ" in hint
    assert "苦かところ" not in hint


def test_案の行の並びは点検に通る():
    rows = [chat("2026-10-11T08:00", "つぶやき", "飯は炊きたてが一番たい。")]
    lines = plan([post("2026-10-11T08:00", "飯は炊きたてが一番たい。"), close_line("2026-10-11T08:00")], rows)

    findings = check_lines([Line.model_validate(line) for line in lines])

    assert [item for item in findings if item.problem] == []


def close_line(at):
    return {"at": at, "kind": "閉じる", "room": "ツイン", "by": "誠一さん"}


@pytest.mark.parametrize("text", ["事実にはなかけん、知らん。", "字数が足らん。", "話し方の例たい。", "ツインに聞け。", "豆腐は書いとらんたい。"])
def test_渡した決まりの言葉が足す文に漏れたら止める(text):
    assert "決まりの言葉" in added_words.added_problem(text, hint=FACT, limit=25, forbidden=forbidden_in)


def test_最近と同じ言い回しは書き直させる():
    assert "言い回し" in added_words.added_problem("見ればわかるたい。", hint=FACT, limit=25, forbidden=forbidden_in, recent=("さあ、見ればわかるたい。",))


def test_決まった短い言葉は最近使っていないものを選ぶ():
    first = added_words.fallback_for(25)
    assert added_words.fallback_for(25, (first,)) != first
