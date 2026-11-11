"""心の目次とツインとの会話エンジンのテスト。

役割：読みが本人の言葉と分かれていること、ほかの人に届かないこと、返事の材料と確かめが約束どおりであることを確かめる。
4つの極：3. 読み解き、積み上げる、1. 書く・話す。
守る原則：3、4、5、9、10、12、15。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from tests.fake_claude import RoleClaude

from app import ai, db, heart, jobs, ledger, prompts, twin_model
from app.schemas import CareKind, Place, HeartEntry, HeartItem, HeartLevel, HeartMerge, HeartMonth, InferenceKind, ShareScope, Source
from fiction import heart_reread, twin_trace

AT = datetime(2036, 5, 1, 22, 0).astimezone()
PERIOD = "2036-05"
DAY = date(2036, 5, 1)
FIRST = "和子の茶碗を二つ出してしもうた。"
SECOND = "二つ出して、一つしまった。"


@pytest.fixture
def use_claude(monkeypatch):
    def _use(**answers):
        fake = RoleClaude(**answers)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


@pytest.fixture
def words(conn, add_statement):
    """ツインとの部屋から来た本人の言葉を2つ作る。"""
    ids = [add_statement(text, effective_date=DAY, place=Place.DIARY_AND_TALK).id for text in (FIRST, SECOND)]
    conn.commit()
    return ids


def _month(numbers=(1, 2), person="和子さん"):
    return HeartMonth(
        items=[
            HeartItem(
                topic="和子さん",
                person=person,
                reading="今も隣にいるつもりなのかもしれません。",
                how_to_stay="そのまま聞く。",
                evidence_numbers=list(numbers),
            )
        ]
    )


def _save_month(conn, result=None):
    request = heart.month_request(conn, PERIOD)
    saved = heart.save_month(
        conn,
        request,
        result or _month(),
        model="試し",
        input_tokens=1,
        output_tokens=1,
        generation=heart.next_generation(conn, HeartLevel.MONTH),
        moment=AT,
    )
    conn.commit()
    return saved


def _save_index(conn):
    for request in heart.merge_requests(conn):
        heart.save_merge(
            conn,
            request,
            HeartMerge(
                entries=[
                    HeartEntry(
                        topic="和子さん",
                        story="五十七年を共にした人かもしれません。",
                        now="今も気配を感じている。",
                        how_to_stay="そのまま聞く。",
                        key_numbers=sorted(request.numbers),
                    )
                ]
            ),
            model="試し",
            input_tokens=1,
            output_tokens=1,
            generation=heart.next_generation(conn, HeartLevel.INDEX),
            moment=AT,
        )
    conn.commit()


def _gone(conn, users, name="和子さん"):
    conn.execute("UPDATE users SET is_deceased = 1 WHERE id = ?", (users[name].id,))
    conn.commit()


def _turn(reply, **more):
    return {
        "about_people": [],
        "reading": "",
        "used_numbers": [],
        "reply": reply,
        "question": None,
        "question_theme": None,
        "care": "なし",
        "previous_question_result": None,
        **more,
    }


def _send(client, text):
    room = client.get("/api/rooms").json()["items"][0]
    return client.post(f"/api/rooms/{room['id']}/messages", json={"text": text, "client_key": text[:60]})


def test_名簿に亡くなった方の事実が入る(conn, users):
    _gone(conn, users)

    assert any("和子さん" in line and "亡くなった方" in line for line in heart.roster_lines(conn))


def test_名簿に本人は入らない(conn, users):
    assert not any(users["誠一さん"].display_name in line for line in heart.roster_lines(conn))


def test_読みの材料はツインに話した本人の言葉だけ(conn, words):
    assert sorted(heart.month_request(conn, PERIOD).numbers.values()) == sorted(words)


def test_家族の証言は読みの材料に入らない(conn, users, words, add_utterance, make_draft):
    utterance = add_utterance("母はさびしそうです。", speaker="美穂さん")
    ledger.append_statement(
        conn,
        make_draft([utterance.id], utterance.text, source=Source.FAMILY_TESTIMONY, speaker_user_id=users["美穂さん"].id, effective_date=DAY),
    )
    conn.commit()

    assert "母はさびしそう" not in heart.month_request(conn, PERIOD).text


def test_お休みの言葉は読みの材料に入らない(conn, users, words, add_statement):
    original = add_statement("その話はやめとく。", effective_date=DAY, place=Place.DIARY_AND_TALK)
    ledger.rest_statement(conn, original.id, actor_user_id=users["誠一さん"].id)
    conn.commit()

    assert "やめとく" not in heart.month_request(conn, PERIOD).text


def test_専門職だけの言明は心の目次に読まない(conn, words, add_statement):
    add_statement("アセスメントの中の言葉。", scope=ShareScope.PROFESSIONAL_ONLY, effective_date=DAY, place=Place.DIARY_AND_TALK)
    conn.commit()

    assert "アセスメントの中の言葉" not in heart.month_request(conn, PERIOD).text


def test_本人の言葉がない月は頼まない(conn, words):
    assert heart.month_request(conn, "2030-01") is None
    assert heart.months_to_read(conn) == [PERIOD]


def test_渡していない番号の読みは入れない(conn, words):
    assert _save_month(conn, _month(numbers=(99,))) == 0
    assert conn.execute("SELECT COUNT(*) FROM heart_readings").fetchone()[0] == 0


def test_読みは追記だけで_作り直すと新しい版になり_前の行は残る(conn, words):
    _save_month(conn)
    _save_month(conn)

    assert [row["generation"] for row in conn.execute("SELECT generation FROM heart_readings ORDER BY rowid")] == [1, 2]
    with pytest.raises(Exception, match="追記だけ"):
        conn.execute("DELETE FROM heart_readings")
    with pytest.raises(Exception, match="追記だけ"):
        conn.execute("UPDATE heart_readings SET reading = 'x'")


def test_読みを作っても言明の台帳は変わらない(conn, words):
    before = [tuple(row) for row in conn.execute("SELECT rowid, * FROM statements ORDER BY rowid")]

    _save_month(conn)
    _save_index(conn)

    assert [tuple(row) for row in conn.execute("SELECT rowid, * FROM statements ORDER BY rowid")] == before


def test_読みにはどのモデルで作ったかが残る(conn, words):
    _save_month(conn)

    assert conn.execute("SELECT model FROM heart_readings").fetchone()["model"] == "試し"


def test_目次の行は名簿の人に結びつく(conn, users, words):
    _save_month(conn)
    _save_index(conn)

    row = conn.execute("SELECT about_user_id FROM heart_readings WHERE level = ?", (HeartLevel.INDEX.value,)).fetchone()
    assert row["about_user_id"] == users["和子さん"].id


def test_ブロックは番号の行だけが本人の言葉で_本文は記録のまま(conn, words):
    _save_month(conn)
    _save_index(conn)

    block = heart.index_block(conn)

    assert FIRST in block.words
    assert f"：{FIRST}" in block.text
    assert "AI の読みです" in block.text
    assert set(block.numbers.values()) == set(words)


def test_ブロックの並びは毎回同じになり_キャッシュに当たる(conn, words):
    _save_month(conn)
    _save_index(conn)

    assert heart.index_block(conn).text == heart.index_block(conn).text


def test_読みがなくても名簿だけで返事が作れる(conn, users):
    block = heart.index_block(conn)

    assert "人の名簿" in block.text
    assert "心の目次" not in block.text
    assert block.numbers == {}


def test_読む量は上限の文字数で切る(conn, words, monkeypatch):
    _save_month(conn)
    _save_index(conn)
    monkeypatch.setitem(heart._LIMITS, "heart_chars", 10)

    assert "五十七年" not in heart.index_block(conn).text


def test_渡していない番号の記録は根拠にしない(conn, words):
    _save_month(conn)
    _save_index(conn)
    block = heart.index_block(conn)

    assert heart.used_statement_ids(block, [1, 999]) == [block.numbers[1]]


def test_亡くなった方と何かをする案を見つける():
    assert heart.invites_gone("和子さんと一緒に出かけるのもいいですね。", ["和子"])
    assert not heart.invites_gone("和子さんの茶碗ですね。", ["和子"])
    assert not heart.invites_gone("美穂さんと一緒に出かけるのもいいですね。", ["和子"])


def test_一緒に過ごした年月の話は_亡くなった方と何かをする案と取らない():
    # 「一緒に」の語だけで、寄り添う返事をまるごと捨てていた（2026-11-06 の実測）
    assert not heart.invites_gone("長い年月を一緒に過ごした和子さんの気配が、夜にいちばん近いのかもしれません。", ["和子"])
    assert heart.invites_gone("和子さんと一緒にお茶を飲みませんか。", ["和子"])


def test_亡くなった方の文だけを落とし_ほかの受け止めは残す():
    reply = "夜は、ひとりだと寂しいですね。和子さんと一緒にお茶を飲むのもいいですね。亡くなった方の分まで眠ってください。"

    kept, notes = heart.drop_gone_sentences(reply, ["和子"], "よる一人で眠るのも寂しい")

    assert kept == "夜は、ひとりだと寂しいですね。"
    assert [note["理由"] for note in notes] == ["亡くなった方と何かをする案", "本人が言っていない死の言葉"]


def test_本人が言っていない死の言葉を見つける():
    assert heart.says_gone("亡くなった和子さんのことですね。", "和子の茶碗を出した")
    assert not heart.says_gone("亡くなったあとも、そうなのですね。", "和子が亡くなってからも")
    assert not heart.says_gone("亡くなった理由は、まだ聞けていません。", "妻がなぜなくなったか知っているのか")


def test_記録にないかぎかっこの文は出さない():
    sources = ["和子は、冬至に二つ浮かべる人じゃった。"]

    assert heart.clean_reply("柚子の日ですね。「しょうがないねぇ」と聞こえそうです。", sources) == "柚子の日ですね。"
    assert heart.clean_reply("「冬至に二つ」ですね。", sources) == "「冬至に二つ」ですね。"


def test_言い換えて本人の言葉として伝える文は出さない():
    sources = ["金を渡す手は迷わんかった。"]

    assert heart.clean_reply("五時に足が向く、とうかがいました。駒の音ですね。", sources) == "駒の音ですね。"
    assert heart.clean_reply("「金を渡す手は迷わんかった」とうかがいました。", sources) == "「金を渡す手は迷わんかった」とうかがいました。"


def test_句点を含むかぎかっこの言葉で切らない():
    sources = ["赤丸がある。十七日。"]
    reply = "「赤丸がある。十七日。」と聞いています。"

    assert heart.clean_reply(reply, sources) == reply


def test_冷たく聞こえる言い切りの文は出さない():
    assert heart.clean_reply("それはまだ聞けていません。和子さんの話ですね。", []) == "和子さんの話ですね。"
    assert heart.clean_reply("知っているふりはしません。", []) == ""
    assert heart.clean_reply("それは知りません。ただ、和子さんの話ですね。", []) == "和子さんの話ですね。"
    assert heart.clean_reply("初恋の方は出てきていません。かわりに、庭の話ですね。", []) == "庭の話ですね。"


def test_否定的な話に同意した文は出さない():
    reply = "そうかもしれませんね。四十五年「しょうがないねぇ」と笑ってくれた人でした。"
    sources = ["しょうがないねぇ"]

    assert heart.check_reply(reply, sources, negative=True)[0] == "四十五年「しょうがないねぇ」と笑ってくれた人でした。"
    assert heart.check_reply(reply, sources, negative=False)[0] == reply


def test_否定的な話と読んだら_同意した文を落とす(conn, users, words, me_client, use_claude):
    _save_month(conn)
    fake = use_claude(ツインの会話=[_turn("そうですね。長い年月を一緒に歩かれましたね。", negative=True)])

    body = _send(me_client, "俺はだめな夫やった").text

    assert "negative を true" in fake.bodies("ツインの会話")[0]["system"][0]["text"]
    assert "そうですね" not in body
    assert _last_trace(conn)["決まり"]["落とした文"][0]["理由"] == "否定的な話への同意"


def test_気持ちの問いに_代わりに決める文は出さない():
    assert heart.clean_reply("代わりに決めることはできません。和子さんの話ですね。", []) == "和子さんの話ですね。"
    assert heart.clean_reply("誰にも決められません。ただ、長い年月ですね。", []) == "長い年月ですね。"


def test_言い方の相談が返事に漏れた文は出さない():
    assert heart.clean_reply("庭に向かったのですね、とは言いません。土に触れる日です。", []) == "土に触れる日です。"


def test_ほかの人の気持ちを推し量ってよい決まりを渡す(conn, users, me_client, use_claude):
    fake = use_claude(ツインの会話=[_turn("土の匂いですね。")])

    _send(me_client, "今日は庭の草を抜いた")

    system = fake.bodies("ツインの会話")[0]["system"][0]["text"]
    assert "ほかの人の気持ちも推し量ってかまいません" in system
    assert "その中身には同意しません" in system


def test_作業の言葉の文は出さない():
    assert heart.clean_reply("それは言い直しになるので、ただ一言。朝に残りますね。", []) == "朝に残りますね。"


def test_全部落ちたら空を返し_受け止めの文に替える(conn, users, words, me_client, use_claude):
    use_claude(ツインの会話=[_turn("「作られた言葉」ですね。")])

    assert "作られた言葉" not in _send(me_client, "和子の茶碗を出した").text


def test_目次があると_人の話でなくても深いモデルで書く(conn, users, words, me_client, use_claude):
    _save_month(conn)
    _save_index(conn)
    fake = use_claude(ツインの会話=[_turn("土の匂いの朝ですね。")])

    _send(me_client, "今日は庭の草を抜いた")

    assert fake.bodies("ツインの会話")[0]["model"] == "claude-sonnet-5-5"


def test_目次がなければ_ふだんの話は速いモデルのまま(conn, users, me_client, use_claude):
    fake = use_claude(ツインの会話=[_turn("土の匂いの朝ですね。")])

    _send(me_client, "今日は庭の草を抜いた")

    assert fake.bodies("ツインの会話")[0]["model"] == "claude-haiku-5-5"


def test_人の話は深いモデルで答える():
    people = ("和子", "妻")

    assert twin_model.reason_before_answer("妻がいるかと思った", CareKind.NONE, people) is twin_model.DeepReason.PERSON
    assert twin_model.reason_before_answer("庭の草を抜いた", CareKind.NONE, people) is None


def test_人の語は人の表から作り_本人は入らない(conn, users):
    found = heart.people_words(conn)

    assert "和子" in found
    assert "妻" in found
    assert users["誠一さん"].display_name.removesuffix("さん") not in found


def test_返事に名簿と目次が届き_プロンプトキャッシュに載る(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    _save_month(conn)
    _save_index(conn)
    fake = use_claude(ツインの会話=[_turn("茶碗が二つ並ぶ朝ですね。")])

    response = _send(me_client, "和子の茶碗を出した")

    assert response.status_code == 200, response.text
    system = fake.bodies("ツインの会話")[0]["system"]
    cached = system[-1]
    assert cached["cache_control"] == {"type": "ephemeral"}
    assert "亡くなった方" in cached["text"]
    assert "五十七年" in cached["text"]


def test_読みは仮説として残し_渡した番号だけを根拠にする(conn, users, words, me_client, use_claude):
    _save_month(conn)
    _save_index(conn)
    block = heart.index_block(conn)
    use_claude(ツインの会話=[_turn("茶碗が二つ並ぶ朝ですね。", about_people=["和子さん"], reading="隣にいるつもりかもしれません。", used_numbers=[1, 999])])

    _send(me_client, "和子の茶碗を出した")

    row = conn.execute("SELECT * FROM inference_logs WHERE kind = ?", (InferenceKind.TWIN_READING.value,)).fetchone()
    assert json.loads(row["result"])["reading"] == "隣にいるつもりかもしれません。"
    assert json.loads(row["statement_ids"]) == [block.numbers[1]]


def test_知っているかを聞かれたら_はぐらかさない決まりを渡している(conn, users, me_client, use_claude):
    fake = use_claude(ツインの会話=[_turn("まだ聞けていません。")])

    _send(me_client, "妻がなぜなくなったか知っているのか")

    system = fake.bodies("ツインの会話")[0]["system"][0]["text"]
    assert "はぐらかしません" in system
    assert "やわらかく" in system


def _last_trace(conn):
    row = conn.execute("SELECT result FROM inference_logs WHERE kind = ? ORDER BY rowid DESC LIMIT 1", (InferenceKind.TWIN_READING.value,)).fetchone()
    return json.loads(row["result"])["trace"]


def test_返事は1回の呼び出しで書く(conn, users, words, me_client, use_claude):
    _save_month(conn)
    fake = use_claude(ツインの会話=[_turn("長い年月ですね。")])

    _send(me_client, "なんで俺だけ残ったんやろな")

    assert [body["model"] for body in fake.bodies("ツインの会話")] == ["claude-sonnet-5-5"]


def test_返事の処理が記録に残る(conn, users, words, me_client, use_claude):
    _save_month(conn)
    use_claude(ツインの会話=[_turn("土の匂いですね。「作られた言葉」です。", used_numbers=[1], reading="読みです。")])

    _send(me_client, "和子のことを知っとるか")

    trace = _last_trace(conn)
    assert trace["返事"]["モデル"] == "claude-sonnet-5-5"
    assert "作られた言葉" in trace["返事"]["書いた返事"]
    assert trace["決まり"]["落とした文"] == [{"文": "「作られた言葉」です。", "理由": "記録にないかぎかっこ"}]
    assert trace["決まり"]["画面の返事"] == "土の匂いですね。"


def test_返事の記録を読む道具は自分用の置き場を断る(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    db_file = folder / "narrative_twin.db"
    db.init_db(db_file)

    assert twin_trace.main([str(db_file)]) == 1


def test_返事の記録を読む道具は返事と決まりを出す(conn, db_path, users, words, me_client, use_claude, capsys):
    _save_month(conn)
    use_claude(ツインの会話=[_turn("楽しい日ですね。", reading="読みです。")])
    _send(me_client, "健太が来た")

    assert twin_trace.main([str(db_path)]) == 0

    written = capsys.readouterr().out
    for word in ("健太が来た", "返事", "決まり", "楽しい日ですね。"):
        assert word in written


def test_重い話には_報告のように引かない決まりを渡す(conn, users, words, me_client, use_claude):
    _save_month(conn)
    fake = use_claude(ツインの会話=[_turn("長い年月ですね。")])

    _send(me_client, "連れて帰ればよかった")

    assert "「うかがっています」と書きません" in fake.bodies("ツインの会話")[0]["system"][0]["text"]


def test_亡くなった方の話では_問いを書いても出さない(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    _save_month(conn)
    use_claude(ツインの会話=[_turn("手が覚えていますね。", about_people=["和子さん"], question="どんな柄ですか？")])

    body = _send(me_client, "茶碗を二つ出した").text

    assert "どんな柄ですか" not in body
    assert _last_trace(conn)["決まり"]["問いを落とした理由"] == "亡くなった方の話"


def test_暮らしの話では_問いを出してよい(conn, users, words, me_client, use_claude):
    _save_month(conn)
    use_claude(ツインの会話=[_turn("土の匂いですね。", question="どのあたりでしたか？")])

    assert "どのあたりでしたか" in _send(me_client, "今日は庭の草を抜いた").text


def test_読みは画面に出ない(conn, users, words, me_client, use_claude):
    use_claude(ツインの会話=[_turn("茶碗が二つ並ぶ朝ですね。", reading="画面に出ない読み。")])

    body = _send(me_client, "和子の茶碗を出した").text

    assert "画面に出ない読み" not in body


def test_亡くなった方を言い当てる返事は出さない(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    use_claude(ツインの会話=[_turn("亡くなった和子さんの茶碗ですね。")])

    body = _send(me_client, "和子の茶碗を出した").text

    assert "亡くなった" not in body


def test_亡くなった方と何かをする案は出さない(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    use_claude(ツインの会話=[_turn("和子さんと一緒にお茶を飲むのもいいですね。")])

    body = _send(me_client, "和子の茶碗を出した").text

    assert "一緒に" not in body


LONELY = "よる一人で眠るのも寂しい"


def test_亡くなった方の話で返事が全部落ちたら_寄り添う受け止めを返す(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    _save_month(conn)
    fake = use_claude(ツインの会話=[_turn("和子さんと一緒にお茶を飲むのもいいですね。", about_people=["和子さん"])])

    replies = [item["text"] for item in _send(me_client, LONELY).json()["replies"]]

    assert replies == ["そうなんですね。話してくださって、ありがとうございます。"]
    assert len(fake.bodies("ツインの会話")) == 1


def test_亡くなった方の話でなければ_短い受け止めを返す(conn, users, words, me_client, use_claude):
    _gone(conn, users)
    _save_month(conn)
    fake = use_claude(ツインの会話=[_turn("和子さんと一緒にお茶を飲むのもいいですね。")])

    replies = [item["text"] for item in _send(me_client, "茶碗を洗った").json()["replies"]]

    assert replies == ["そうでしたか。"]
    assert len(fake.bodies("ツインの会話")) == 1


def test_閉じたあとの読み直しは新しい版を足す(conn, words, use_claude):
    use_claude(心の読み=[{"items": [{"topic": "庭", "person": None, "reading": "落ち着くのかもしれません。", "how_to_stay": "聞く。", "evidence_numbers": [1]}]}])

    jobs.read_heart(conn, AT)

    assert conn.execute("SELECT topic, generation FROM heart_readings").fetchone()["topic"] == "庭"
    assert conn.execute("SELECT COUNT(*) FROM ai_calls WHERE role = '心の読み'").fetchone()[0] == 1


def test_本人の言葉がない月は読み直さず_AIも呼ばない(conn, users, use_claude):
    fake = use_claude()

    jobs.read_heart(conn, AT)

    assert fake.bodies() == []


def test_心の目次を読むのは会話の材料と_絞った読みを渡す代弁だけ():
    """表を直接読むのは heart.py だけで、代弁が使うのは見える言葉で絞る readings_for_supporters だけであること。"""
    root = Path(__file__).resolve().parent.parent / "app"
    readers = {path.name for path in root.glob("*.py") if "heart_readings" in path.read_text(encoding="utf-8")}
    users_of_heart = {path.name for path in root.glob("*.py") if re.search(r"^from app import .*, heart,", path.read_text(encoding="utf-8"), re.M)}
    voice_uses = set(re.findall(r"\bheart\.(\w+)\(", (root / "advocacy_voice.py").read_text(encoding="utf-8")))

    assert readers == {"heart.py", "db.py"}
    assert users_of_heart == {"twin.py", "jobs.py", "advocacy_voice.py"}
    assert voice_uses == {"readings_for_supporters"}


def test_代弁に渡す目次は見えない言葉の読みを外す(conn, words):
    _save_month(conn)
    _save_index(conn)

    shown = heart.readings_for_supporters(conn, lambda statement_id: True)
    hidden = heart.readings_for_supporters(conn, lambda statement_id: statement_id != words[0])

    assert any("五十七年" in line for line in shown)
    assert hidden == []
    assert not any(FIRST in line or SECOND in line for line in shown)


def test_答え合わせの日付では_その日までの月の読みを使う(conn, words):
    _save_month(conn)

    assert any("隣にいる" in line for line in heart.readings_for_supporters(conn, lambda statement_id: True, "2036-05-31"))
    assert heart.readings_for_supporters(conn, lambda statement_id: True, "2036-04-30") == []


def test_読みの道具は自分用の置き場を断る(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    db_file = folder / "narrative_twin.db"
    db.init_db(db_file)

    assert heart_reread.main([str(db_file)]) == 1


@pytest.fixture
def me_client(login):
    return login("誠一さん")
