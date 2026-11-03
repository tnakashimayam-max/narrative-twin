"""開発のログのテスト。

役割：自分用のログに本文が混ざらないこと、架空用のログで処理を追えること、ログがアプリを止めないことを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（自分の記録は手元だけに置く）、15（人だけの部屋は AI に渡さない）。

本物の流れ（登録、写真、会話、閉じる、失敗）に目印の文字列を流し、ファイル全体に目印がないことを見る。
書く側の決まりではなく、書き出された結果を確かめるため。
ログの置き場は conftest.py の keep_real_logs_out で一時的な場所に向けている。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import ai, config, devlog, jobs
from app.main import create_app
from tests.fake_claude import RoleClaude
from tests.test_intake import _statement
from tests.test_photos import jpeg_bytes

ROOT = Path(__file__).resolve().parent.parent

# 目印は、ふだんの文章に出てこない形にする
SELF_NAME = "目印本人名アイウ"
PERSON_NAME = "目印家族名カキク"
SAID = "目印の発言サシス"
PHOTO_SAID = "目印の写真の言葉タチツ"
REPLY = "目印の返事ナニヌです。"
QUESTION = "目印の問いハヒフは何色ですか？"
PHOTO_DESCRIPTION = "目印の写真の説明マミム"
SUMMARY = "目印の要約ヤユヨ"
TIP = "目印の一言ラリル"
DROPPED_BODY = "目印の入らない言明ワヲン"
ROOM_TALK = "目印の人だけの部屋アカサ"
BAD_INPUT = "目印の誤った入力タナハ"
SECRETS = [
    SELF_NAME,
    PERSON_NAME,
    SAID,
    PHOTO_SAID,
    REPLY,
    QUESTION,
    PHOTO_DESCRIPTION,
    SUMMARY,
    TIP,
    DROPPED_BODY,
    ROOM_TALK,
    BAD_INPUT,
    # 部分の文字列で見る。切り詰められて混ざった場合も見つけるため
    "目印",
]

# ご本人が決めた、自分用のログに書いてよい項目：操作の種類、時刻、かかった時間、結果、エラーの種類、文字数、トークン数
ALLOWED_KEYS = {"at", "op", "ms", "status", "outcome", "notes", "error"}


def _is_allowed(key: str) -> bool:
    return key in ALLOWED_KEYS or key.endswith(("_chars", "_tokens"))


def _lines(client_or_path) -> list[dict]:
    path = client_or_path if isinstance(client_or_path, Path) else client_or_path.app.state.devlog.path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _raw(client) -> str:
    return client.app.state.devlog.path.read_text(encoding="utf-8")


def _turn(**more) -> dict:
    return {
        "reply": REPLY,
        "question": QUESTION,
        "question_theme": "好きなこと",
        "care": "なし",
        "previous_question_result": None,
        **more,
    }


def _photo_turn() -> dict:
    return _turn(photo_description=PHOTO_DESCRIPTION)


def _answers() -> dict:
    """AI の答えのすべてに目印を入れる。"""
    return {
        "ツインの会話": _turn(),
        "今日はここまで": {"reply": REPLY, "tip": TIP},
        "会話の要約": {"summary": SUMMARY},
        "言明への分割": {"statements": [_statement(DROPPED_BODY, subject=SELF_NAME, speaker=SELF_NAME)]},
    }


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude(**_answers())
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def own(tmp_path, claude):
    """自分用の起動で、本人を登録してログインしたブラウザ役。"""
    client = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))
    user_id = client.post("/api/self", json={"name": SELF_NAME, "called_name": f"{SELF_NAME}さん"}).json()["id"]
    assert client.post("/api/login", json={"user_id": user_id}).status_code == 200
    client.headers["X-NT-User"] = str(user_id)
    return client


def _twin_room(client) -> dict:
    return client.get("/api/rooms").json()["items"][0]


def _send(client, text, *, photo_id=None):
    room = _twin_room(client)
    return client.post(
        f"/api/rooms/{room['id']}/messages", json={"text": text, "photo_id": photo_id, "client_key": text[:60]}
    )


def _full_use(client, claude) -> None:
    """人の登録、写真、会話、今日はここまで、裏の処理までを流す。"""
    claude.answers["ツインの会話"] = [_photo_turn(), _turn()]
    assert client.post("/api/people", json={"name": PERSON_NAME, "role": "家族"}).status_code == 201
    photo_id = client.post("/api/photos?kind=会話", content=jpeg_bytes()).json()["id"]
    assert _send(client, PHOTO_SAID, photo_id=photo_id).status_code == 200
    assert _send(client, SAID).status_code == 200
    assert client.post(f"/api/rooms/{_twin_room(client)['id']}/close").status_code == 200


# ---- 自分用のログに本文を混ぜない ----


def test_自分用のログに本文が混ざらない(own, claude):
    _full_use(own, claude)

    raw = _raw(own)
    for secret in SECRETS:
        assert secret not in raw
    # 流れそのものが動いて、AI にも本文が渡っていることを確かめる。何も書かれずに通るのを防ぐため
    assert SAID in claude.sent_text()
    assert PHOTO_SAID in claude.sent_text()


def test_自分用のログに写真のデータが混ざらない(own, claude):
    claude.answers["ツインの会話"] = _photo_turn()
    photo_id = own.post("/api/photos?kind=会話", content=jpeg_bytes()).json()["id"]

    assert _send(own, PHOTO_SAID, photo_id=photo_id).status_code == 200

    assert "/9j/" not in _raw(own)  # JPEG を base64 にしたときの先頭


def test_自分用のログには決めた項目だけが入る(own, claude):
    _full_use(own, claude)

    for line in _lines(own):
        assert all(_is_allowed(key) for key in line), line


def test_自分用のログで_操作と時間と結果とトークン数は分かる(own, claude):
    _full_use(own, claude)

    lines = _lines(own)
    ops = {line["op"] for line in lines}
    assert "POST /api/rooms/{room_id}/messages" in ops
    assert "POST /api/rooms/{room_id}/close" in ops
    assert "AI：写真の問い" in ops
    assert "ツインの返事" in ops
    assert "言明への分割" in ops
    called = next(line for line in lines if line["op"] == "AI：ツインの会話")
    assert called["input_tokens"] == 100
    assert called["output_tokens"] == 50
    assert called["outcome"] == "成功"
    assert isinstance(called["ms"], int)
    first, second = [line for line in lines if line["op"] == "ツインの返事"]
    assert first["said_chars"] == len(PHOTO_SAID)
    assert first["reply_chars"] == len(REPLY)
    assert first["outcome"] == "問いあり"
    # 2回目は同じ問いなので落ちる。落ちたことは本文なしで分かる
    assert second["outcome"] == "問いなし"
    assert second["notes"] == ["問いを落とした"]


def test_自分用のログは入らなかった言明を結果だけで知らせる(own, claude):
    _full_use(own, claude)

    raw = _raw(own)
    assert "入らなかった言明あり" in raw
    assert DROPPED_BODY not in raw


@pytest.mark.parametrize(
    ("answer", "outcome", "error"),
    [
        (500, "届かない", "AIUnavailableError"),
        ("断る", "断られた", "AIRefusedError"),
        ({"reply": f"私は{BAD_INPUT}です。"}, "型が合わない", "AIResultError"),
    ],
)
def test_自分用のログはAIの失敗を種類だけで残す(own, claude, answer, outcome, error):
    claude.answers["ツインの会話"] = answer

    assert _send(own, SAID).status_code == 503

    raw = _raw(own)
    for secret in SECRETS:
        assert secret not in raw
    failed = next(line for line in _lines(own) if line["op"] == "AI：ツインの会話")
    assert failed["outcome"] == outcome
    assert failed["error"] == error
    assert [line["status"] for line in _lines(own) if line["op"] == "POST /api/rooms/{room_id}/messages"] == [503]


def test_例外の文は自分用のログに書かない(tmp_path):
    app = create_app(deployment=config.deployment(mine=True, data_dir=tmp_path))

    def boom():
        raise RuntimeError(BAD_INPUT)

    # 画面のファイルの道より前に置く
    app.router.routes.insert(0, APIRoute("/api/boom", boom))
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/boom").status_code == 500

    line = _lines(client)[-1]
    assert line["error"] == "RuntimeError"
    assert line["outcome"] == "失敗"
    assert BAD_INPUT not in _raw(client)
    assert "where" not in line


def test_裏の処理の失敗の文は自分用のログに書かない(tmp_path):
    app = create_app(deployment=config.deployment(mine=True, data_dir=tmp_path))

    def boom(_conn):
        raise ValueError(BAD_INPUT)

    jobs._guarded(app, "会話の要約", f"会話 {BAD_INPUT}", boom)

    raw = app.state.devlog.path.read_text(encoding="utf-8")
    assert BAD_INPUT not in raw
    assert "ValueError" in raw


def test_誤った入力とURLの中身は自分用のログに書かない(own):
    assert own.post("/api/self", json={"name": 123, "called_name": BAD_INPUT}).status_code == 422
    own.get(f"/api/{BAD_INPUT}")
    own.get(f"/api/users?q={BAD_INPUT}")

    raw = _raw(own)
    assert BAD_INPUT not in raw
    assert "（道なし）" in raw


def test_呼ぶ側が本文を渡しても自分用のログは通さない(tmp_path):
    log = devlog.DevLog(tmp_path / "mine.jsonl", mine=True)

    log.write(
        BAD_INPUT,
        text={"発言": BAD_INPUT},
        said=BAD_INPUT,
        outcome=BAD_INPUT,
        notes=[BAD_INPUT],
        error=BAD_INPUT,
        said_chars=BAD_INPUT,
        input_tokens=BAD_INPUT,
        status=BAD_INPUT,
        ms=BAD_INPUT,
        model="claude",
        target="会話 1",
    )

    line = _lines(tmp_path / "mine.jsonl")[0]
    assert set(line) == {"at", "op"}
    assert line["op"] == "不明"


def test_真偽値を数として書かない(tmp_path):
    log = devlog.DevLog(tmp_path / "mine.jsonl", mine=True)

    log.write(devlog.START, said_chars=True)

    assert "said_chars" not in _lines(tmp_path / "mine.jsonl")[0]


# ---- 架空用のログ ----


def test_架空用のログには入力の本文が残る(login, claude):
    me = login("誠一さん")

    _send(me, SAID)

    raw = _raw(me)
    assert SAID in raw
    assert REPLY in raw


def test_架空用のログにも写真のデータは入らない(login, claude):
    me = login("誠一さん")
    claude.answers["ツインの会話"] = _photo_turn()
    data = jpeg_bytes()
    photo_id = me.post("/api/photos?kind=会話", content=data).json()["id"]

    assert _send(me, PHOTO_SAID, photo_id=photo_id).status_code == 200

    raw = _raw(me)
    assert PHOTO_DESCRIPTION in raw
    assert base64.b64encode(data).decode("ascii")[:40] not in raw
    assert "/9j/" not in raw


def test_架空用のログにも人だけの部屋の本文は入らない(login, users, claude):
    me = login("誠一さん")
    room = me.post("/api/rooms", json={"kind": "1対1", "member_ids": [users["節子さん"].id]}).json()

    response = me.post(f"/api/rooms/{room['id']}/messages", json={"text": ROOM_TALK, "client_key": "k"})

    assert response.status_code == 200
    assert ROOM_TALK not in _raw(me)


def test_自分用のログにはモデルを選んだ印だけを書き_理由は書かない(own, claude):
    # 理由の「気持ちの話」や「体の話」は、話の内容が分かるため
    claude.answers["ツインの会話"] = _turn()

    _send(own, "なんだか寂しいな。")

    turn = next(line for line in _lines(own) if line["op"] == "ツインの返事")
    assert "Sonnet を選んだ" in turn["notes"]
    assert "気持ちの話" not in _raw(own)
    assert "モデルを選んだ理由" not in _raw(own)


def test_ログで受け止めを短くしたことが分かる(login, claude):
    me = login("誠一さん")
    claude.answers["ツインの会話"] = {"reply": "急須ですね。大切に使われてきたのが伝わります。"}

    _send(me, "急須です。")

    turn = next(line for line in _lines(me) if line["op"] == "ツインの返事")
    assert turn["notes"] == ["受け止めを短くした"]
    assert turn["text"]["返事の元"] == "急須ですね。大切に使われてきたのが伝わります。"
    assert turn["text"]["返事"] == "急須ですね。"


def test_ログで決まった文に替えたことが分かる(login, claude):
    me = login("誠一さん")
    claude.answers["ツインの会話"] = {"reply": "そのお気持ちが伝わります。"}

    _send(me, "迷惑はかけたくない。")

    assert "決まった文に替えた" in next(line for line in _lines(me) if line["op"] == "ツインの返事")["notes"]


def test_ログで前の話を読めているかが分かる(login, claude):
    me = login("誠一さん")
    _send(me, "一日目の話です。")
    me.post(f"/api/rooms/{_twin_room(me)['id']}/close")

    _send(me, "二日目の話です。")

    turns = [line for line in _lines(me) if line["op"] == "ツインの返事"]
    assert turns[0]["summaries_chars"] == 0
    assert turns[1]["summaries_chars"] > 0


def test_ログは操作を実際のURLではなく道の形で書く(login, claude):
    me = login("誠一さん")
    _send(me, SAID)

    line = next(line for line in _lines(me) if line["op"].startswith("POST /api/rooms"))
    assert line["op"] == "POST /api/rooms/{room_id}/messages"
    assert line["status"] == 200


def test_画面のファイルの読み込みはログに書かない(tmp_path):
    client = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path)))

    client.get("/")

    assert not client.app.state.devlog.path.exists()


# ---- 置き場と、アプリを止めないこと ----


def test_架空用と自分用のログが混ざらない(tmp_path, monkeypatch):
    fake = RoleClaude(**_answers())
    monkeypatch.setattr(ai, "_make_client", fake.client)
    fictional = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path / "a")))
    mine = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path / "b")))

    user_id = next(user["id"] for user in fictional.get("/api/users").json()["items"] if user["role"] == "本人")
    fictional.post("/api/login", json={"user_id": user_id})
    fictional.headers["X-NT-User"] = str(user_id)
    assert _send(fictional, SAID).status_code == 200
    mine.get("/api/mode")

    assert fictional.app.state.devlog.path.name == "fictional.jsonl"
    assert mine.app.state.devlog.path.name == "mine.jsonl"
    assert SAID in _raw(fictional)
    assert SAID not in _raw(mine)


def test_自分用のログは自分用の記録の置き場の外にある(tmp_path):
    deployment = config.deployment(mine=True, data_dir=tmp_path)

    assert deployment.log_path.parent == config.LOG_DIR
    assert deployment.db_path.parent not in deployment.log_path.parents


def test_ログのファイルはGitに入らない():
    assert "logs/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_ログが書けなくてもアプリは止まらない(tmp_path, monkeypatch):
    blocker = tmp_path / "ファイル"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "LOG_DIR", blocker)
    client = TestClient(create_app(deployment=config.deployment(data_dir=tmp_path / "d")))

    assert client.get("/api/mode").status_code == 200


def test_大きくなったログは1世代だけ残す(tmp_path, monkeypatch):
    monkeypatch.setattr(devlog, "_MAX_BYTES", 1)
    log = devlog.DevLog(tmp_path / "mine.jsonl", mine=True)

    log.write(devlog.START, ms=1)
    log.write(devlog.START, ms=2)
    log.write(devlog.START, ms=3)

    assert _lines(tmp_path / "mine.jsonl")[0]["ms"] == 3
    assert _lines(tmp_path / "mine.jsonl.1")[0]["ms"] == 2


def test_行き先が決まっていないときは何も書かない():
    devlog.record(devlog.START, text={"発言": BAD_INPUT})

    assert not config.LOG_DIR.exists()
