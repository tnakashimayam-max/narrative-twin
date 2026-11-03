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
from fiction.load import LogConversation
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
    return {"at": f"{AT}{minute}", "kind": "発言", "room": "ツイン", "by": "誠一さん", "text": text, **fields}


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


def test_健太くんは話し手にも登録もできない():
    assert any("話し手にできません" in text for text in problems([say("はい。", by="健太くん")]))
    person = {"at": f"{AT}00", "kind": "人", "by": "誠一さん", "name": "健太くん", "role": "家族"}
    assert any("ログインできる人" in text for text in problems([person]))


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
    fake.answers["不明"] = {"text": "庭を見ています。毎朝この庭を見るのが楽しみなんです。"}
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
        "interpretations": [],
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


def test_answerにmustが入らなければ作り直し_それでもだめならmustだけを送る(world, claude):
    client, _, progress = world
    claude.answers["不明"] = {"text": "庭を見るのが楽しみです。"}
    must = "毎朝この庭を見るのが楽しみなんです。"

    flow(
        world,
        [say("おはよう。"), {"at": f"{AT}11", "kind": "発言", "room": "ツイン", "by": "誠一さん", "answer": {"must": must}}, close()],
    )

    assert len(claude.bodies("不明")) == 2
    assert [item["text"] for item in messages(client) if not item["is_twin"]][1] == must
    assert any("must の文だけ" in text for text in progress.warnings)


def test_hintだけの答えが作れなければ止まる(world, claude):
    claude.answers["不明"] = {"text": "先週、肺炎の話を聞きました。"}

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
