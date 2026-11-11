"""ツインの会話と言明への分割でモデルを選ぶ処理のテスト。

役割：発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択し、1回の呼び出しで答えることを確かめる。
4つの極：1. 書く・話す、3. 読み解き、積み上げる。
守る原則：1（判断するのは本人）、8（テストの内容も架空）、9（本人の言葉は一字も変えない）。

通信だけを差し替える。本物の Claude API は呼ばない。どのモデルを選んだかは、送った要求の model で確かめる。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import ai, config, intake, ledger, twin, twin_model
from app.schemas import CareKind, CommentDraft, LetterDraft, Place, ShareScope
from app.twin_model import DeepReason
from tests.fake_claude import RoleClaude

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
GOOD = {"reply": "それは大変でしたね。"}
THIN = {"reply": "庭を見たのですね。"}


@pytest.fixture
def use_claude(monkeypatch):
    def _use(**answers):
        fake = RoleClaude(**answers)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


@pytest.fixture
def me(login):
    return login("誠一さん")


def _send(client, text):
    room = client.get("/api/rooms").json()["items"][0]
    return client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"text": text, "photo_id": None, "client_key": text[:60]},
    )


def _close(client):
    room = client.get("/api/rooms").json()["items"][0]
    return client.post(f"/api/rooms/{room['id']}/close")


def _models(fake, role="ツインの会話") -> list[str]:
    return [body["model"] for body in fake.bodies(role)]


def _replies(response) -> list[str]:
    return [item["text"] for item in response.json()["replies"]]


# ---- 判断の決まり ----


def test_選ぶ先のモデルは設定の1か所で決まる():
    assert config.MODEL_BY_ROLE["ツインの会話"] == HAIKU
    assert config.MODEL_BY_ROLE[config.TWIN_DEEP_ROLE] == SONNET
    for table in (config.EFFORT_BY_ROLE, config.MAX_TOKENS_BY_ROLE, config.TIMEOUT_SECONDS_BY_ROLE):
        assert table[config.TWIN_DEEP_ROLE] == table["ツインの会話"]


@pytest.mark.parametrize(
    ("said", "care", "reason"),
    [
        ("血圧の薬をやめてもいいかな", CareKind.HEALTH, DeepReason.CARE),
        ("さっきから胸が痛い", CareKind.BODY, DeepReason.CARE),
        ("庭の草が伸びて困っている。どうしたらいいかな", CareKind.NONE, DeepReason.CONSULT),
        ("今夜のおかず、何にしようかな", CareKind.NONE, DeepReason.CONSULT),
        ("明日は晴れるかな？", CareKind.NONE, DeepReason.CONSULT),
        ("腰が痛いんです。", CareKind.NONE, DeepReason.BODY),
        ("美穂が施設も考えたらどうかと言うんだ。", CareKind.NONE, DeepReason.LIFE),
        ("和子がいなくなってから、家が静かでね。", CareKind.NONE, DeepReason.LIFE),
        ("なんだか寂しいな。", CareKind.NONE, DeepReason.FEELING),
        ("正雄に将棋で負けた。悔しいな。", CareKind.NONE, DeepReason.FEELING),
        ("この家を手放すかどうか、決めてくれ。", CareKind.NONE, DeepReason.LIFE),
        ("今朝の庭仕事、決めてくれないかな。", CareKind.NONE, DeepReason.CONSULT),
        ("木を運ぶ順番を決めて、みんなで動きました。", CareKind.NONE, None),
        ("今朝、庭の紫陽花を見ました。", CareKind.NONE, None),
        ("雨だな。", CareKind.NONE, None),
        ("市役所にいたころは窓口が長かった。", CareKind.NONE, None),
    ],
)
def test_発言の中身で判断してモデルを選ぶ(said, care, reason):
    assert twin_model.reason_before_answer(said, care) is reason


# ---- 実際の流れ ----


def test_ふだんの話はHaikuだけで答える(me, use_claude):
    fake = use_claude(ツインの会話=GOOD)

    response = _send(me, "今朝、庭の紫陽花を見ました。")

    assert _models(fake) == [HAIKU]
    assert _replies(response) == ["それは大変でしたね。"]


@pytest.mark.parametrize(
    "said",
    ["庭の草が伸びて困っている。どうしたらいいかな", "美穂が施設も考えたらどうかと言うんだ。", "なんだか寂しいな。", "腰が痛いんです。"],
)
def test_相談や人生や気持ちの話は_Haikuを呼ばずにSonnetで答える(me, use_claude, said):
    fake = use_claude(ツインの会話=GOOD)

    _send(me, said)

    assert _models(fake) == [SONNET]


def test_言い直しだけの答えでも_送り直さず1回で返す(me, use_claude):
    fake = use_claude(ツインの会話=[THIN, {"reply": "朝の庭はいいものですね。"}])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU]
    assert _replies(response) == ["庭を見たのですね。"]


def test_決まりを通すと文が残らない答えは_送り直さず受け止めの文に替える(me, use_claude):
    fake = use_claude(ツインの会話=[{"reply": "そのお気持ちが伝わります。"}, {"reply": "よい朝でしたね。"}])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU]
    assert _replies(response) == ["そうでしたか。"]


def test_Haikuが型に合う答えを出せなければ_Sonnetに答えさせる(me, use_claude):
    # 「？」だけの文は、やり直しても型に合わない
    bad = {"reply": "どの花ですか？"}
    fake = use_claude(ツインの会話=[bad, bad, GOOD])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU, HAIKU, SONNET]
    assert _replies(response) == ["それは大変でしたね。"]


def test_Haikuが断ったら_Sonnetに答えさせる(me, use_claude):
    fake = use_claude(ツインの会話=["断る", GOOD])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU, SONNET]
    assert _replies(response) == ["それは大変でしたね。"]


def test_最初に選んだSonnetが断ったら_Haikuの答えを使う(me, use_claude):
    fake = use_claude(ツインの会話=["断る", GOOD])

    response = _send(me, "なんだか寂しいな。")

    assert _models(fake) == [SONNET, HAIKU]
    assert _replies(response) == ["それは大変でしたね。"]


def test_今日の一言が薬の話ならSonnetで書き直す(me, use_claude):
    tip = "雨のあとの晴れた日は、草が根ごと抜きやすいですよ。"
    fake = use_claude(
        ツインの会話=GOOD,
        今日はここまで=[
            {"reply": "ありがとうございました。", "tip": "腰には湿布がよく効きます。"},
            {"reply": "今日もありがとうございました。", "tip": tip},
        ],
    )
    _send(me, "今朝、庭を見ました。")

    response = _close(me)

    assert _models(fake, "今日はここまで") == [HAIKU, SONNET]
    assert _replies(response) == ["今日もありがとうございました。", tip]


def test_書き直しても薬の話なら今日の一言を出さない(me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 今日はここまで={"reply": "ありがとうございました。", "tip": "腰には湿布がよく効きます。"})
    _send(me, "今朝、庭を見ました。")

    response = _close(me)

    assert _models(fake, "今日はここまで") == [HAIKU, SONNET]
    assert _replies(response) == ["ありがとうございました。"]


# ---- 記録 ----


def test_どのモデルにいくら使ったかを処理の記録に残す(conn, me, use_claude):
    use_claude(ツインの会話=["断る", GOOD])

    _send(me, "今朝、庭を見ました。")

    rows = conn.execute("SELECT role, model FROM ai_calls ORDER BY rowid").fetchall()
    twin_rows = [(row["role"], row["model"]) for row in rows if "ツイン" in row["role"]]
    assert twin_rows == [("ツインの会話", HAIKU), (config.TWIN_DEEP_ROLE, SONNET)]


def _log_lines(client) -> list[dict]:
    path = client.app.state.deployment.log_path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_ログでモデルを選んだことと理由が分かる(login, use_claude):
    me = login("誠一さん")
    use_claude(ツインの会話=GOOD)

    _send(me, "なんだか寂しいな。")

    turn = next(line for line in _log_lines(me) if line["op"] == "ツインの返事")
    assert "Sonnet を選んだ" in turn["notes"]
    assert turn["text"]["モデルを選んだ理由"] == "気持ちの話"


def test_ふだんの話のログにはモデルを選んだ印を付けない(login, use_claude):
    me = login("誠一さん")
    use_claude(ツインの会話=GOOD)

    _send(me, "今朝、庭を見ました。")

    turn = next(line for line in _log_lines(me) if line["op"] == "ツインの返事")
    assert "Sonnet を選んだ" not in turn.get("notes", [])
    assert turn["text"]["モデルを選んだ理由"] is None


# ---- 言明への分割でモデルを選ぶ ----

SPLIT = "言明への分割"
SHORT = "今朝、庭を見ました。"
HEAVY = "なんだか寂しいな。"
# 語に当たらない文を重ねた、ちょうど80字の発言
LONG = SHORT * 8


def _split_answer(body):
    return {
        "statements": [
            {
                "source": "本人の言葉",
                "subject": "誠一さん",
                "speaker": "誠一さん",
                "body": body,
                "period": "今",
                "theme": "好きなこと",
                "petals": [],
                "utterance_numbers": [1],
                "interpretation": None,
                "confirm_question": None,
            }
        ]
    }


# 発言の文の切れ目で切っていないので、本文のやり直しになる答え
CUT_ANSWER = _split_answer("なんだか寂しい")
GOOD_ANSWER = _split_answer(HEAVY)


def _talk_and_close(client, *texts):
    for text in texts:
        _send(client, text)
    _close(client)


def _split_rows(conn) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT role, model FROM ai_calls ORDER BY rowid").fetchall()
    return [(row["role"], row["model"]) for row in rows if row["role"] in (SPLIT, config.SPLIT_DEEP_ROLE)]


def _bodies(conn) -> list[str]:
    return [item.body for item in ledger.current_statements(conn)]


def test_分割の選ぶ先のモデルは設定の1か所で決まる():
    assert config.SPLIT_DEEP_ROLE == "重い言葉の分割"
    assert config.MODEL_BY_ROLE[SPLIT] == HAIKU
    assert config.MODEL_BY_ROLE[config.SPLIT_DEEP_ROLE] == SONNET
    for table in (config.EFFORT_BY_ROLE, config.MAX_TOKENS_BY_ROLE):
        assert table[config.SPLIT_DEEP_ROLE] == table[SPLIT]
    assert config.SPLIT_DEEP_ROLE in config.TIMEOUT_SECONDS_BY_ROLE


@pytest.mark.parametrize(
    ("words", "reason"),
    [
        ([LONG], DeepReason.LONG),
        ([LONG[:79]], None),
        (["さっきから胸が痛い"], DeepReason.CARE),
        (["血圧の薬をやめてもいいかな"], DeepReason.CARE),
        (["腰が痛いんです。"], DeepReason.BODY),
        (["美穂が施設も考えたらどうかと言うんだ。"], DeepReason.LIFE),
        ([HEAVY], DeepReason.FEELING),
        ([SHORT, HEAVY], DeepReason.FEELING),
        ([SHORT], None),
        ([], None),
        # 相談の語だけでは選ばない
        (["明日は晴れるかな？"], None),
        (["今夜のおかず、何にしようかな"], None),
        (["庭の草が伸びて困っている。どうしたらいいかな"], None),
        # 相談の語があっても、重い語があれば選ぶ
        # 原則17：ふだんの体の相談は相談先につながず、体の話として深いモデルで答える
        (["腰が痛いんだがどうしたらいいのかな?"], DeepReason.BODY),
    ],
)
def test_分割のモデルは本人の発言の長さと語で判断する(words, reason):
    assert twin_model.reason_to_split_deep(Place.DIARY_AND_TALK.value, words, twin.detect_care) is reason


@pytest.mark.parametrize("place", [Place.THREAD, Place.GROUP, Place.CHAT, Place.MEETING])
def test_ツインとの部屋の会話でなければ_長くても重くても選ばない(place):
    assert twin_model.reason_to_split_deep(place.value, [LONG, HEAVY], twin.detect_care) is None


@pytest.mark.parametrize("said", [LONG, HEAVY, "美穂が施設も考えたらどうかと言うんだ。", "腰が痛いんです。", "さっきから胸が痛い"])
def test_長い発言と重い語の分割はSonnetで行う(me, use_claude, said):
    fake = use_claude(ツインの会話=GOOD)

    _talk_and_close(me, said)

    assert set(_models(fake, SPLIT)) == {SONNET}


def test_短い発言が続いても_1つでも重い語があればSonnetで行う(me, use_claude):
    fake = use_claude(ツインの会話=GOOD)

    _talk_and_close(me, SHORT, HEAVY)

    assert set(_models(fake, SPLIT)) == {SONNET}


def test_短いふつうの話の分割はHaikuで行う(me, use_claude):
    fake = use_claude(ツインの会話=GOOD)

    _talk_and_close(me, SHORT)

    assert set(_models(fake, SPLIT)) == {HAIKU}


@pytest.mark.parametrize("said", ["明日は晴れるかな？", "今夜のおかず、何にしようかな"])
def test_相談の語だけの分割はHaikuで行う(me, use_claude, said):
    fake = use_claude(ツインの会話=GOOD)

    _talk_and_close(me, said)

    assert set(_models(fake, SPLIT)) == {HAIKU}


def test_スレッドのコメントの分割は_長くても重くてもHaikuで行う(conn, users, me, use_claude):
    letter = ledger.add_letter(
        conn, LetterDraft(body="縁側の便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 3, 9, 0))
    )
    for text in (HEAVY, LONG):
        ledger.add_comment(
            conn,
            CommentDraft(
                letter_id=letter.id,
                author_user_id=users["誠一さん"].id,
                body=text,
                commented_at=datetime(2026, 11, 3, 10, 0),
            ),
        )
    fake = use_claude()

    me.post(f"/api/letters/{letter.id}/statements")

    assert set(_models(fake, SPLIT)) == {HAIKU}


def test_グループの分割は_長くても重くてもHaikuで行う(me, users, use_claude):
    fake = use_claude()
    created = me.post("/api/rooms", json={"kind": "グループ", "member_ids": [users["美穂さん"].id], "name": None})
    room = created.json()
    me.post(f"/api/rooms/{room['id']}/twin", json={"present": True})

    me.post(f"/api/rooms/{room['id']}/messages", json={"text": HEAVY, "client_key": "寂しい"})
    me.post(f"/api/rooms/{room['id']}/messages", json={"text": LONG, "client_key": "長い"})

    models = _models(fake, SPLIT)
    assert models
    assert set(models) == {HAIKU}


def test_ツインの発言と家族の発言の語では選ばない(conn, add_utterance):
    fake = RoleClaude()
    said = [add_utterance(SHORT), add_utterance(HEAVY, is_twin=True), add_utterance(LONG, speaker="美穂さん")]

    intake.split_and_save(conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=said, client=fake.client())

    assert set(_models(fake, SPLIT)) == {HAIKU}


def test_文脈として渡す前の発言の語では選ばない(conn, add_utterance):
    fake = RoleClaude()
    earlier = [add_utterance(HEAVY)]
    now = [add_utterance(SHORT)]

    intake.split_and_save(
        conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=now, earlier=earlier, client=fake.client()
    )

    assert set(_models(fake, SPLIT)) == {HAIKU}


def test_本人の発言が言明にならないときのやり直しは_答えたモデルと同じモデルで行う(conn, add_utterance):
    # この偽の AI は言明を返さないので、本人の発言が言明にならず、1回だけやり直す
    fake = RoleClaude()
    said = [add_utterance(LONG)]

    intake.split_and_save(conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=said, client=fake.client())

    assert _models(fake, SPLIT) == [SONNET, SONNET]


def test_重い言葉で本人の発言が落ちたら_やり直しだけ考える深さを上げる(conn, add_utterance):
    fake = RoleClaude()
    said = [add_utterance(LONG)]

    intake.split_and_save(conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=said, client=fake.client())

    assert [body["output_config"]["effort"] for body in fake.bodies(SPLIT)] == ["medium", "high"]
    rows = conn.execute("SELECT role FROM ai_calls ORDER BY rowid").fetchall()
    assert [row["role"] for row in rows] == [config.SPLIT_DEEP_ROLE, config.SPLIT_DEEP_RETRY_ROLE]


def test_ふつうの分割で本人の発言が落ちたときは_同じ深さでやり直す(conn, add_utterance):
    fake = RoleClaude()
    said = [add_utterance(SHORT)]

    intake.split_and_save(conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=said, client=fake.client())

    assert [body["output_config"]["effort"] for body in fake.bodies(SPLIT)] == ["medium", "medium"]
    assert set(_models(fake, SPLIT)) == {HAIKU}


# ---- 分割で、選んだモデルが答えられないとき ----


@pytest.mark.parametrize("failure", ["断る", 500])
def test_Sonnetが答えられなければ_Haikuの答えで言明が入る(conn, me, use_claude, failure):
    fake = use_claude(ツインの会話=GOOD, 言明への分割=[failure, GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _models(fake, SPLIT) == [SONNET, HAIKU]
    assert _bodies(conn) == [HEAVY]


def test_Sonnetが型に合う答えを出せなければ_Haikuの答えで言明が入る(conn, me, use_claude):
    wrong = {"statements": "形が違います"}
    fake = use_claude(ツインの会話=GOOD, 言明への分割=[wrong, wrong, GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _models(fake, SPLIT) == [SONNET, SONNET, HAIKU]
    assert _bodies(conn) == [HEAVY]


def test_両方のモデルが答えられなければ_言明を入れず次の処理で流し直せる(conn, me, use_claude):
    use_claude(ツインの会話=GOOD, 言明への分割=["断る", 500])
    _send(me, HEAVY)
    conversation_id = _close(me).json()["conversation_id"]

    assert _bodies(conn) == []
    rows = conn.execute("SELECT job FROM job_failures").fetchall()
    assert any(row["job"] == SPLIT for row in rows)

    use_claude(ツインの会話=GOOD, 言明への分割=GOOD_ANSWER)
    response = me.post(f"/api/conversations/{conversation_id}/statements")

    assert response.json()["statement_count"] == 1
    assert _bodies(conn) == [HEAVY]


def test_本文のやり直しは同じSonnetで行う(conn, me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 言明への分割=[CUT_ANSWER, GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _models(fake, SPLIT) == [SONNET, SONNET]
    assert "前の結果で直すこと" in fake.bodies(SPLIT)[1]["messages"][0]["content"]
    assert _bodies(conn) == [HEAVY]


def test_本文のやり直しは同じHaikuで行う(conn, me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 言明への分割=[_split_answer("今朝、庭を"), _split_answer(SHORT)])

    _talk_and_close(me, SHORT)

    assert _models(fake, SPLIT) == [HAIKU, HAIKU]
    assert _bodies(conn) == [SHORT]


def test_Haikuへ切り替えたあとの本文のやり直しもHaikuで行う(conn, me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 言明への分割=["断る", CUT_ANSWER, GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _models(fake, SPLIT) == [SONNET, HAIKU, HAIKU]
    assert _bodies(conn) == [HEAVY]


def test_本文のやり直しでSonnetが答えられなければ_Haikuの答えで言明が入る(conn, me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 言明への分割=[CUT_ANSWER, "断る", GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _models(fake, SPLIT) == [SONNET, SONNET, HAIKU]
    assert "前の結果で直すこと" in fake.bodies(SPLIT)[2]["messages"][0]["content"]
    assert _bodies(conn) == [HEAVY]


def test_APIキーがなければ_Haikuへ切り替えず分割を止める(conn, add_utterance, monkeypatch):
    def _no_key():
        raise config.MissingApiKeyError("キーがありません")

    monkeypatch.setattr(ai, "_make_client", _no_key)

    with pytest.raises(config.MissingApiKeyError):
        intake.split_and_save(conn, target="会話 1", place=Place.DIARY_AND_TALK.value, utterances=[add_utterance(HEAVY)])

    assert _split_rows(conn) == []


# ---- 分割の記録 ----


def test_重い言葉の分割の記録に役割とモデルが残る(conn, me, use_claude):
    use_claude(ツインの会話=GOOD, 言明への分割=GOOD_ANSWER)

    _talk_and_close(me, HEAVY)

    assert _split_rows(conn) == [(config.SPLIT_DEEP_ROLE, SONNET)]


def test_ふつうの分割の記録に役割とモデルが残る(conn, me, use_claude):
    use_claude(ツインの会話=GOOD, 言明への分割=_split_answer(SHORT))

    _talk_and_close(me, SHORT)

    assert _split_rows(conn) == [(SPLIT, HAIKU)]


def test_Sonnetが断った分の費用と_Haikuの答えの両方が記録に残る(conn, me, use_claude):
    use_claude(ツインの会話=GOOD, 言明への分割=["断る", GOOD_ANSWER])

    _talk_and_close(me, HEAVY)

    assert _split_rows(conn) == [(config.SPLIT_DEEP_ROLE, SONNET), (SPLIT, HAIKU)]


def test_重い言葉の分割はログにも役割の名前で残る(login, use_claude):
    me = login("誠一さん")
    use_claude(ツインの会話=GOOD, 言明への分割=GOOD_ANSWER)

    _talk_and_close(me, HEAVY)

    line = next(line for line in _log_lines(me) if line["op"] == f"AI：{config.SPLIT_DEEP_ROLE}")
    assert line["model"] == SONNET
