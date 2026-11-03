"""ツインの会話でモデルを選ぶ処理のテスト。

役割：発言の中身と Haiku 5.5 の答えをプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択することを確かめる。
4つの極：1. 書く・話す。
守る原則：1（判断するのは本人）、8（テストの内容も架空）。

通信だけを差し替える。本物の Claude API は呼ばない。どのモデルを選んだかは、送った要求の model で確かめる。
"""

from __future__ import annotations

import json

import pytest

from app import ai, config, twin_model
from app.schemas import CareKind
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


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("腰が痛いのですね。", DeepReason.THIN),
        ("今朝、紫陽花を見たのですね。今年は色が濃かったのですね。", DeepReason.THIN),
        ("", DeepReason.NOTHING_LEFT),
        ("紫陽花の色が濃いと、朝の庭も楽しみですね。", None),
        ("市役所にいたのですね。人と向き合う毎日だったでしょうね。", None),
        ("そうでしたか。", None),
    ],
)
def test_Haikuの答えで判断してモデルを選ぶ(reply, reason):
    assert twin_model.reason_after_answer(reply) is reason


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


def test_言い直しだけの答えなら_Sonnetへ送り直してそちらの答えを使う(me, use_claude):
    fake = use_claude(ツインの会話=[THIN, {"reply": "朝の庭はいいものですね。"}])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU, SONNET]
    assert _replies(response) == ["朝の庭はいいものですね。"]


def test_決まりを通すと文が残らない答えなら_Sonnetへ送り直す(me, use_claude):
    fake = use_claude(ツインの会話=[{"reply": "そのお気持ちが伝わります。"}, {"reply": "よい朝でしたね。"}])

    response = _send(me, "今朝、庭を見ました。")

    assert _models(fake) == [HAIKU, SONNET]
    assert _replies(response) == ["よい朝でしたね。"]


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


def test_送り直したSonnetが答えられなければ_Haikuの答えを使う(me, use_claude):
    # 本人を待たせて何も返さないより、言い直しでも返すほうがよい
    fake = use_claude(ツインの会話=[THIN, 500])

    response = _send(me, "今朝、庭を見ました。")

    assert response.status_code == 200
    assert _models(fake) == [HAIKU, SONNET]
    assert _replies(response) == ["庭を見たのですね。"]


def test_最初に選んだSonnetが断ったら_Haikuの答えを使う(me, use_claude):
    fake = use_claude(ツインの会話=["断る", GOOD])

    response = _send(me, "なんだか寂しいな。")

    assert _models(fake) == [SONNET, HAIKU]
    assert _replies(response) == ["それは大変でしたね。"]


def test_今日の一言が健康の助言ならSonnetで書き直す(me, use_claude):
    tip = "雨のあとの晴れた日は、草が根ごと抜きやすいですよ。"
    fake = use_claude(
        ツインの会話=GOOD,
        今日はここまで=[
            {"reply": "ありがとうございました。", "tip": "体を温めてから動くと楽ですよ。"},
            {"reply": "今日もありがとうございました。", "tip": tip},
        ],
    )
    _send(me, "今朝、庭を見ました。")

    response = _close(me)

    assert _models(fake, "今日はここまで") == [HAIKU, SONNET]
    assert _replies(response) == ["今日もありがとうございました。", tip]


def test_書き直しても健康の助言なら今日の一言を出さない(me, use_claude):
    fake = use_claude(ツインの会話=GOOD, 今日はここまで={"reply": "ありがとうございました。", "tip": "体を温めてから動くと楽ですよ。"})
    _send(me, "今朝、庭を見ました。")

    response = _close(me)

    assert _models(fake, "今日はここまで") == [HAIKU, SONNET]
    assert _replies(response) == ["ありがとうございました。"]


# ---- 記録 ----


def test_どのモデルにいくら使ったかを処理の記録に残す(conn, me, use_claude):
    use_claude(ツインの会話=[THIN, GOOD])

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
