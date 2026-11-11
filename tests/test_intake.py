"""言明への分割の API のテスト。

役割：AI の結果の検証、やり直し、分かりやすいエラー、処理の記録を確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：1（判断は人が行う）、9（一字も変えない）、10（共有範囲）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import ai, intake, ledger, splitter
from app.prompts import TWIN_LABEL
from app.schemas import CommentDraft, LetterDraft, ShareScope
from tests.fake_claude import FakeClaude, message, split_message


@pytest.fixture
def use_claude(monkeypatch):
    """差し替えた Claude を、アプリから使うようにする。"""

    def _use(*responses):
        fake = FakeClaude(*responses)
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


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
        "interpretation": None,
        "confirm_question": None,
    }
    values.update(changes)
    return values


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
    assert TWIN_LABEL in text
    assert "[1] 誠一さん（本人）：毎朝この庭を見るのが楽しみなんです。" in text
    assert "今の意思モデルの要約" in text


def test_話に出てこない人の名前はAIに渡さない(login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。")))

    _split(login("誠一さん"), conversation_id)

    assert "美穂さん" not in fake.request_body()["messages"][0]["content"]


def test_話に名前が出てくる人はAIに渡す(login, use_claude, add_utterance, conversation_id):
    add_utterance("美穂さんが来てくれました。")
    fake = use_claude(split_message(_statement("美穂さんが来てくれました。")))

    _split(login("誠一さん"), conversation_id)

    assert "美穂さん（家族）" in fake.request_body()["messages"][0]["content"]


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


def test_本人の発言が言明にならなければ_1回やり直す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("墓に行ってきた。菊と線香。")
    add_utterance("和子は家に帰りたがっとった。帰してやれんかった。")
    fake = use_claude(
        split_message(_statement("墓に行ってきた。菊と線香。", (1,))),
        split_message(_statement("墓に行ってきた。菊と線香。", (1,)), _statement("和子は家に帰りたがっとった。帰してやれんかった。", (2,))),
    )

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert "和子は家に帰りたがっとった" in fake.request_body(1)["messages"][0]["content"].split("言明がありません")[0]
    assert response.json()["statement_count"] == 2
    assert response.json()["dropped"] == []


def test_本人の発言の一部だけが言明なら_1回やり直す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("墓に行った。元気かどうかは俺が決めることじゃなか。")
    fake = use_claude(
        split_message(_statement("墓に行った。", (1,))),
        split_message(_statement("墓に行った。", (1,)), _statement("元気かどうかは俺が決めることじゃなか。", (1,))),
    )

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert "「元気かどうかは俺が決めることじゃなか。」が言明にありません" in fake.request_body(1)["messages"][0]["content"]
    assert response.json()["statement_count"] == 2
    assert response.json()["dropped"] == []


def test_短い文が落ちただけならやり直さない(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("雨。庭の金木犀が咲いとる。")
    fake = use_claude(split_message(_statement("庭の金木犀が咲いとる。", (1,))))

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 1
    assert response.json()["dropped"] == []


def test_やり直しても一部の文が入らなければ_入った言明を残して記録に残す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("墓に行った。元気かどうかは俺が決めることじゃなか。")
    only_first = split_message(_statement("墓に行った。", (1,)))
    fake = use_claude(only_first, only_first)

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert response.json()["statement_count"] == 1
    assert [item["reason"] for item in response.json()["dropped"]] == [splitter.MISSING_SELF_SENTENCE]


def test_やり直しても本人の発言が言明にならなければ_ほかの言明を入れて記録に残す(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("墓に行ってきた。菊と線香。")
    add_utterance("和子は家に帰りたがっとった。帰してやれんかった。")
    only_first = split_message(_statement("墓に行ってきた。菊と線香。", (1,)))
    fake = use_claude(only_first, only_first)

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 2
    assert response.json()["statement_count"] == 1
    assert [item["reason"] for item in response.json()["dropped"]] == ["本人の発言が言明になりませんでした"]


@pytest.mark.parametrize("short", ["おはよう。", "そうたい。", "よか。"])
def test_あいさつのような短い本人の発言では_やり直さない(conn, login, use_claude, add_utterance, conversation_id, short):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    add_utterance(short)
    fake = use_claude(split_message(_statement("毎朝この庭を見るのが楽しみなんです。", (1,))))

    response = _split(login("誠一さん"), conversation_id)

    assert len(fake.requests) == 1
    assert response.json()["dropped"] == []


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

    assert second.status_code == 200
    assert second.json()["statement_count"] == 0
    assert len(fake.requests) == 1
    assert len(ledger.current_statements(conn)) == 1


def test_新しい発言だけを番号つきで渡し_前の発言は文脈にする(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("毎朝この庭を見るのが楽しみなんです。")
    fake = use_claude(
        split_message(_statement("毎朝この庭を見るのが楽しみなんです。")),
        split_message(_statement("紫陽花も好きです。")),
    )
    client = login("誠一さん")
    _split(client, conversation_id)
    add_utterance("紫陽花も好きです。")

    response = _split(client, conversation_id)

    text = fake.request_body(1)["messages"][0]["content"]
    assert response.json()["statement_count"] == 1
    assert "[1] 誠一さん（本人）：紫陽花も好きです。" in text
    assert "[2]" not in text
    assert "誠一さん（本人）：毎朝この庭を見るのが楽しみなんです。" in text
    assert "[1] 誠一さん（本人）：毎朝" not in text
    assert len(ledger.current_statements(conn)) == 2


def test_ツインの発言しか増えていなければAIを呼ばない(conn, login, use_claude, add_utterance, conversation_id):
    add_utterance("庭が好きです。")
    fake = use_claude(split_message(_statement("庭が好きです。")))
    client = login("誠一さん")
    _split(client, conversation_id)
    add_utterance("どの花がお好きですか？", is_twin=True)

    assert _split(client, conversation_id).json()["statement_count"] == 0
    assert len(fake.requests) == 1


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


def test_発言がない会話ではAIを呼ばない(login, use_claude, conversation_id):
    fake = use_claude()

    response = _split(login("誠一さん"), conversation_id)

    assert response.status_code == 200
    assert response.json()["statement_count"] == 0
    assert fake.requests == []


def test_ない会話とない便りは見つからない(login, use_claude):
    use_claude()
    client = login("誠一さん")

    assert _split(client, 123456).status_code == 404
    assert client.post("/api/letters/123456/statements").status_code == 404


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
                interpretation={
                    "viewpoint_role": "本人",
                    "body": "負担を減らしたいのかもしれません",
                    "confidence": 0.6,
                    "caution": None,
                },
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
