"""写真の問いと言明への分割の API のテスト。

役割：AI の結果の検証、やり直し、分かりやすいエラー、処理の記録を確かめる。
4つの極：1. 書く・話す、3. 読み解き、積み上げる。
守る原則：1（判断は人が行う）、9（一字も変えない）、10（共有範囲）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

from app import ai, intake, ledger
from app.prompts import TWIN_LABEL
from app.schemas import CommentDraft, LetterDraft, PhotoQuestion, ShareScope
from tests.fake_claude import FakeClaude, message, split_message

JPEG = b"\xff\xd8\xff\xe0fake-photo"


@pytest.fixture
def use_claude(monkeypatch):
    """差し替えた Claude を、アプリから使うようにする。"""

    def _use(*responses):
        fake = FakeClaude(*responses)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


def _post_photo(client, body=JPEG, content_type="image/jpeg"):
    return client.post("/api/photo-question", content=body, headers={"content-type": content_type})


def _statement(body, numbers=(1,), **changes):
    values = {
        "source": "本人の言葉",
        "subject": "誠一さん",
        "speaker": "誠一さん",
        "body": body,
        "period": "今",
        "theme": "好きなこと",
        "petals": ["くつろぎ"],
        "utterance_numbers": list(numbers),
        "interpretations": [],
        "confirm_question": None,
    }
    values.update(changes)
    return values


# ---- 写真の問い ----


def test_写真を渡すと問いを1つ返す(login, use_claude):
    fake = use_claude(message('{"question": "誠一さん、この縁側ではどんな時間を過ごしますか？"}'))

    response = _post_photo(login("誠一さん"))

    assert response.status_code == 200
    assert response.json() == {"question": "誠一さん、この縁側ではどんな時間を過ごしますか？"}
    image = fake.request_body()["messages"][0]["content"][0]
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(image["source"]["data"]) == JPEG


def test_問いが2つあればやり直す(login, use_claude):
    fake = use_claude(
        message('{"question": "庭ですね。何を育てていますか？"}'),
        message('{"question": "誠一さん、庭で何を育てていますか？"}'),
    )

    response = _post_photo(login("誠一さん"))

    assert response.json()["question"] == "誠一さん、庭で何を育てていますか？"
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "text",
    ["庭ですね。", "庭ですか？ 花ですか？", "何を育てていますか？ぜひ。", "", "？", "庭ですね\n何を育てていますか？", "庭ですね…何を育てていますか？"],
    ids=["問いでない", "問いが2つ", "問いのあとに続く", "空", "記号だけ", "改行で2文", "三点で2文"],
)
def test_問いの形でない文は型で止める(text):
    with pytest.raises(ValueError):
        PhotoQuestion(question=text)


def test_やり直してもだめなら分かりやすい言葉で返す(login, use_claude):
    use_claude(message('{"question": "庭ですね。"}'), message('{"question": "庭ですね。"}'))

    response = _post_photo(login("誠一さん"))

    assert response.status_code == 503
    assert response.json()["detail"] == intake.AI_FAILED_MESSAGE


def test_APIのエラーでも分かりやすい言葉で返す(login, use_claude):
    use_claude(500)

    response = _post_photo(login("誠一さん"))

    assert response.status_code == 503
    assert response.json()["detail"] == intake.AI_FAILED_MESSAGE


def test_キーがなくても分かりやすい言葉で返す(login):
    response = _post_photo(login("誠一さん"))

    assert response.status_code == 503
    assert response.json()["detail"] == intake.AI_FAILED_MESSAGE


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (JPEG, "application/pdf"),
        (b"", "image/jpeg"),
        (b"x" * (intake.MAX_PHOTO_BYTES + 1), "image/jpeg"),
    ],
    ids=["形式が違う", "空", "大きすぎる"],
)
def test_読めない写真はAIに送らず言葉で返す(login, use_claude, body, content_type):
    fake = use_claude()

    response = _post_photo(login("誠一さん"), body, content_type)

    assert response.status_code == 400
    assert response.json()["detail"]
    assert fake.requests == []


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_写真の問いは本人だけが使える(login, use_claude, name):
    fake = use_claude()

    assert _post_photo(login(name)).status_code == 404
    assert fake.requests == []


def test_写真の問いのモデルとトークン数を残す(conn, login, use_claude):
    use_claude(message('{"question": "誠一さん、庭で何を育てていますか？"}'))

    _post_photo(login("誠一さん"))

    row = conn.execute("SELECT * FROM ai_calls").fetchone()
    assert row["role"] == "写真の問い"
    assert row["model"] == "claude-sonnet-5-5"
    assert (row["input_tokens"], row["output_tokens"]) == (100, 50)


# ---- 言明への分割 ----


def _split(client, conversation_id):
    return client.post(f"/api/conversations/{conversation_id}/statements")


def test_会話を言明に分けて保存する(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。")))

    response = _split(login("誠一さん"), conversation_id)

    assert response.status_code == 200
    assert response.json()["statement_count"] == 1
    assert response.json()["dropped"] == []
    assert ledger.current_statements(conn)[0].share_scope is ShareScope.SELF_ONLY


def test_入力に人物の一覧と発言の番号と要約が入る(login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。")))

    _split(login("誠一さん"), conversation_id)

    text = fake.request_body()["messages"][0]["content"]
    assert "美穂さん（家族）" in text
    assert TWIN_LABEL in text
    assert "[1] 誠一さん（本人）：毎朝この庭を見るのが楽しみなんです。" in text
    assert "今の意思モデルの要約" in text


def test_本文に問題があれば理由を伝えて1回やり直す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("家で暮らしたいとは思いません。")
    fake = use_claude(
        split_message(_statement("家で暮らしたい")),
        split_message(_statement("家で暮らしたいとは思いません。")),
    )

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert "文の途中" in fake.request_body(1)["messages"][0]["content"]
    assert response.json()["statement_count"] == 1
    assert ledger.current_statements(conn)[0].body == "家で暮らしたいとは思いません。"


def test_やり直しても問題が残る言明は捨てて数える(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("家で暮らしたいとは思いません。")
    fake = use_claude(
        split_message(_statement("家で暮らしたい")), split_message(_statement("家で暮らしたい"))
    )

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert response.status_code == 200
    assert response.json()["statement_count"] == 0
    assert response.json()["dropped"][0]["body"] == "家で暮らしたい"
    assert ledger.current_statements(conn) == []


def test_呼び出しごとにモデルとトークン数を残す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("家で暮らしたいとは思いません。")
    use_claude(
        split_message(_statement("家で暮らしたい")),
        split_message(_statement("家で暮らしたいとは思いません。")),
    )

    _split(login("誠一さん"), conversation_id)

    rows = conn.execute("SELECT * FROM ai_calls").fetchall()
    assert [row["role"] for row in rows] == ["言明への分割", "言明への分割"]
    assert rows[0]["target"] == f"会話 {conversation_id}"


def test_同じ会話は1回だけ処理する(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。")))
    client = login("誠一さん")

    _split(client, conversation_id)
    second = _split(client, conversation_id)

    assert second.status_code == 409
    assert len(fake.requests) == 1
    assert len(ledger.current_statements(conn)) == 1


def test_処理に失敗したら分かりやすい言葉で返し何も入れない(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    use_claude(message("だめ"), message("まだだめ"))

    response = _split(login("誠一さん"), conversation_id)

    assert response.status_code == 503
    assert response.json()["detail"] == intake.AI_FAILED_MESSAGE
    assert ledger.current_statements(conn) == []


def test_失敗した会話はもう一度処理できる(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    use_claude(message("だめ"), message("まだだめ"))
    client = login("誠一さん")
    _split(client, conversation_id)

    use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。")))

    assert _split(client, conversation_id).status_code == 200


def test_やり直しが失敗しても_もう一度処理できる(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("家で暮らしたいとは思いません。")
    use_claude(split_message(_statement("家で暮らしたい")), message("だめ"), message("まだだめ"))
    client = login("誠一さん")

    assert _split(client, conversation_id).status_code == 503

    use_claude(split_message(_statement("家で暮らしたいとは思いません。")))

    assert _split(client, conversation_id).status_code == 200


def test_失敗した呼び出しのトークン数も残す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("庭が好きです。")
    use_claude(message("だめ"), message("まだだめ"))

    _split(login("誠一さん"), conversation_id)

    row = conn.execute("SELECT * FROM ai_calls").fetchone()
    assert (row["input_tokens"], row["output_tokens"]) == (200, 100)


def test_写真の失敗した呼び出しのトークン数も残す(conn, login, use_claude):
    use_claude(message('{"question": "庭ですね。"}'), message('{"question": "庭ですね。"}'))

    _post_photo(login("誠一さん"))

    row = conn.execute("SELECT * FROM ai_calls").fetchone()
    assert row["role"] == "写真の問い"
    assert row["input_tokens"] == 200


def test_送るモデルと考える深さは設定の値を使う(login, use_claude, add_utterance, conversation_id):
    from app import config

    add_utterance("庭が好きです。")
    fake = use_claude(split_message(_statement("庭が好きです。")))

    _split(login("誠一さん"), conversation_id)

    body = fake.request_body()
    assert body["model"] == config.MODEL_BY_ROLE["言明への分割"]
    assert body["output_config"]["effort"] == config.EFFORT_BY_ROLE["言明への分割"]
    assert body["max_tokens"] == config.MAX_TOKENS_BY_ROLE["言明への分割"]


def test_スレッドでは便りの本文を文脈として渡す(conn, users, login, use_claude):
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="縁側の便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 3, 9, 0)),
    )
    ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter.id,
            author_user_id=users["美穂さん"].id,
            body="父は縁側が好きでした。",
            commented_at=datetime(2026, 11, 3, 10, 0),
        ),
    )
    fake = use_claude(split_message())

    login("誠一さん").post(f"/api/letters/{letter.id}/statements")

    assert "縁側の便りです。" in fake.request_body()["messages"][0]["content"]


def test_発言がない会話は見つからない(login, use_claude, conversation_id):
    fake = use_claude()

    assert _split(login("誠一さん"), conversation_id).status_code == 404
    assert fake.requests == []


@pytest.mark.parametrize("name", ["美穂さん", "節子さん", "田中さん"])
def test_言明への分割は本人だけが動かせる(login, use_claude, add_utterance, conversation_id, name):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude()

    assert _split(login(name), conversation_id).status_code == 404
    assert fake.requests == []


def test_スレッドのコメントは書いた人の役割の証言になる(conn, users, login, use_claude):
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="縁側の便りです。", share_with=ShareScope.CONNECTED, shared_at=datetime(2026, 11, 3, 9, 0)),
    )
    for name, body, hour in [("美穂さん", "父は縁側が好きでした。", 10), ("節子さん", "よくお茶をいただきました。", 11)]:
        ledger.add_comment(
            conn,
            CommentDraft(
                letter_id=letter.id,
                author_user_id=users[name].id,
                body=body,
                commented_at=datetime(2026, 11, 3, hour, 0),
            ),
        )
    use_claude(
        split_message(
            _statement("縁側が好きだったようだ", numbers=(1,), source="家族の証言", speaker="美穂さん"),
            _statement("お茶を出してくれた", numbers=(2,), source="縁のある人の証言", speaker="節子さん"),
        )
    )

    response = login("誠一さん").post(f"/api/letters/{letter.id}/statements")

    assert response.json()["statement_count"] == 2
    sources = {item.source.value for item in ledger.current_statements(conn)}
    assert sources == {"家族の証言", "縁のある人の証言"}


def test_問いの候補と解釈の数を返す(login, use_claude, add_utterance, conversation_id):
    add_utterance("迷惑はかけたくないです。")
    use_claude(
        split_message(
            _statement(
                "迷惑はかけたくないです。",
                interpretations=[
                    {
                        "perspective": "本当のニーズ",
                        "viewpoint_role": "本人",
                        "body": "負担を減らしたいのかもしれません",
                        "confidence": 0.6,
                        "caution": None,
                    }
                ],
            ),
            _statement(
                "本当は手を借りたいのかもしれません",
                source="AIの推測",
                speaker=TWIN_LABEL,
                confirm_question="手を借りたいと思うこともあるかもしれません。合っていますか？",
            ),
        )
    )

    body = _split(login("誠一さん"), conversation_id).json()

    assert body["statement_count"] == 2
    assert body["interpretation_count"] == 1
    assert body["question_count"] == 1


def test_AIの結果に余計な項目があれば型で止めてやり直す(login, use_claude, add_utterance, conversation_id):
    """共有範囲を AI から受け取らないことも、この形で守られる。"""
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    bad = _statement("毎朝この庭を見るのが楽しみなんです。", share_scope="縁のある人まで")
    good = _statement("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude(
        message(json.dumps({"statements": [bad]}, ensure_ascii=False)),
        split_message(good),
    )

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert response.json()["statement_count"] == 1


# ---- 画面 ----


def test_本人の画面に写真の問いの部品がある(db_path, conn):
    from fastapi.testclient import TestClient

    from app.main import create_app

    page = TestClient(create_app(db_path)).get("/").text

    assert 'id="photo-input"' in page
    assert "ツイン（AI）" in page
