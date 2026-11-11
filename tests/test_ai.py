"""AIを呼ぶ層のテスト。

役割：検証とやり直しの動きと、AIの層と決まった手順の層が分かれていることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：1（AIの層は台帳や絞り込みに手を出さない）。

本物の SDK を使い、通信の部分だけを差し替える。自作の偽物だと、SDK の動きと
ずれていても気づけないため。ネットには出ず、費用もかからない。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app import ai, config

APP_DIR = Path(__file__).resolve().parent.parent / "app"


class Answer(BaseModel):
    text: str


def _message(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 34},
    }


class Server:
    """決まった順番で答える、Claude API の代わり。"""

    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.responses.pop(0))

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(self)),
            max_retries=0,
        )


def _call(server: Server):
    return ai.call_structured(
        "接続確認", system="確認", content="確認", schema=Answer, client=server.client()
    )


def test_型に合う答えを受け取り_モデル名とトークン数を残す():
    server = Server(_message('{"text": "はい"}'))

    result = _call(server)

    assert result.value == Answer(text="はい")
    assert result.model == "claude-opus-5-5"
    assert (result.input_tokens, result.output_tokens) == (12, 34)
    assert len(server.requests) == 1


def _fallback_response(partial: str, final: str) -> dict:
    """1つ目のモデルが途中で断り、2つ目のモデルが答えた応答。"""
    message = _message(final)
    iteration = {
        "input_tokens": 5,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    message["content"] = [
        {"type": "text", "text": partial},
        {
            "type": "fallback",
            "from": {"model": "claude-opus-5-5"},
            "to": {"model": "claude-opus-4-8"},
            "trigger": {"type": "refusal", "category": None},
        },
        {"type": "text", "text": final},
    ]
    message["usage"]["iterations"] = [
        {"type": "message", "model": "claude-opus-5-5", **iteration},
        {"type": "fallback_message", "model": "claude-opus-4-8", **iteration},
    ]
    return message


def test_代替のモデルが答えたら_そのモデル名を残す():
    server = Server(_fallback_response('{"text": "途中"}', '{"text": "はい"}'))

    assert _call(server).model == "claude-opus-4-8"


def test_断ったモデルの途中の文は答えにしない():
    server = Server(_fallback_response('{"text": "途中"}', '{"text": "はい"}'))

    assert _call(server).value == Answer(text="はい")
    assert len(server.requests) == 1


def test_代替がなければ応答のモデル名を残す():
    server = Server(_message('{"text": "はい"}', model="claude-opus-5"))

    assert _call(server).model == "claude-opus-5"


def test_やり直した分のトークン数も足す():
    server = Server(_message("だめ"), _message('{"text": "はい"}'))

    result = _call(server)

    assert (result.input_tokens, result.output_tokens) == (24, 68)


def test_モデル名は設定の値を使い_JSONの型を送る():
    server = Server(_message('{"text": "はい"}'))

    _call(server)

    body = json.loads(server.requests[0].content)
    assert body["model"] == config.MODEL_BY_ROLE["接続確認"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "text" in body["output_config"]["format"]["schema"]["properties"]


def test_Sonnetでは断られたときの代替を頼む():
    server = Server(_message('{"text": "はい"}'))

    ai.call_structured("写真の問い", system="確認", content="確認", schema=Answer, client=server.client())

    assert json.loads(server.requests[0].content)["fallbacks"] == "default"
    assert "server-side-fallback" in server.requests[0].headers["anthropic-beta"]


def test_Haikuには断られたときの代替を送らない():
    server = Server(_message('{"text": "はい"}'))

    _call(server)

    assert config.MODEL_BY_ROLE["接続確認"] == "claude-haiku-5-5"
    assert "fallbacks" not in json.loads(server.requests[0].content)
    assert "server-side-fallback" not in server.requests[0].headers.get("anthropic-beta", "")


def test_Haikuで断られた応答は_やり直さず断られたものとして扱う():
    server = Server(_message("", stop_reason="refusal", model="claude-haiku-5-5"))

    with pytest.raises(ai.AIRefusedError):
        _call(server)

    assert len(server.requests) == 1


def test_応答の先頭が考える処理でも本文を読める():
    response = _message('{"text": "はい"}', model="claude-haiku-5-5")
    response["content"].insert(0, {"type": "thinking", "thinking": "", "signature": "abc"})

    assert _call(Server(response)).value == Answer(text="はい")


def test_考える処理だけで終わった応答は_答えがないものとして1回やり直す():
    thinking_only = _message("", stop_reason="max_tokens", model="claude-haiku-5-5")
    thinking_only["content"] = [{"type": "thinking", "thinking": "", "signature": "abc"}]
    server = Server(thinking_only, _message('{"text": "はい"}'))

    assert _call(server).value == Answer(text="はい")
    assert len(server.requests) == 2


def test_Haikuの費用はプロンプトが10万トークンを超えると高い値段で計算する():
    assert config.cost_usd("claude-haiku-5-5", 100_000, 1000) == pytest.approx(0.0105)
    assert config.cost_usd("claude-haiku-5-5", 100_001, 1000) == pytest.approx(0.0525, abs=1e-4)
    assert config.cost_usd("claude-haiku-5-5", 1000, 1000, batch=True) == pytest.approx(0.0003)


def test_写真を読むのはSonnetでほかの動く処理はHaiku():
    assert config.MODEL_BY_ROLE["写真の問い"] == "claude-sonnet-5-5"
    for role in ("ツインの会話", "スレッドの問い", "グループのツイン", "便りの下書き", "言明への分割", "人の候補", "架空ログの作成", "接続確認"):
        assert config.MODEL_BY_ROLE[role] == "claude-haiku-5-5", role


def test_JSONとして読めない答えは1回やり直す():
    server = Server(_message("これはJSONではありません"), _message('{"text": "はい"}'))

    assert _call(server).value == Answer(text="はい")
    assert len(server.requests) == 2


def test_型に合わない答えは1回やり直す():
    server = Server(_message('{"other": "はい"}'), _message('{"text": "はい"}'))

    assert _call(server).value == Answer(text="はい")
    assert len(server.requests) == 2


def test_やり直してもだめならエラーにして_それ以上は呼ばない():
    server = Server(_message("だめ"), _message("まだだめ"), _message('{"text": "はい"}'))

    with pytest.raises(ai.AIResultError):
        _call(server)

    assert len(server.requests) == 2


def test_断られたらやり直さない():
    server = Server(_message("", stop_reason="refusal"), _message('{"text": "はい"}'))

    with pytest.raises(ai.AIRefusedError):
        _call(server)

    assert len(server.requests) == 1


def test_モデルが決まっていない役割は呼ばない():
    server = Server(_message('{"text": "はい"}'))

    with pytest.raises(ValueError):
        ai.call_structured(
            "決まっていない役割", system="確認", content="確認", schema=Answer, client=server.client()
        )

    assert server.requests == []


# ---- API キー ----


def test_APIキーは_envから読む(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=key-from-env-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env_file)

    assert config.api_key() == "key-from-env-file"


def test_APIキーがなければ分かる言葉で止まる():
    with pytest.raises(config.MissingApiKeyError) as caught:
        config.api_key()

    assert "API キーが見つかりません" in str(caught.value)


def test_接続確認はキーがなければ通信しない(capsys):
    assert ai.main() == 1
    assert "API キーが見つかりません" in capsys.readouterr().out


def test_APIキーを持つ_envはGitに入らない():
    gitignore = (APP_DIR.parent / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in gitignore


# ---- 層の分け方 ----


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # from . import ai のような相対の書き方も、app の中の名前に直して比べる
            module = node.module or ""
            if node.level:
                module = f"app.{module}" if module else "app"
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "filename",
    # 次のファイルは AI の層と決まった手順の層をつなぐ場所なので、AI の層を読み込む。
    # 照合（verbatim）、台帳（ledger）、同意（consent）、写真（photos）、部屋の表（room_store）などはここに入らない。
    sorted(
        path.name
        for path in APP_DIR.glob("*.py")
        if path.name not in {"ai.py", "intake.py", "twin.py", "jobs.py", "rooms.py", "timeline.py", "consolidate.py", "selfhood_stages.py", "advocacy.py", "advocacy_voice.py", "advocacy_sheet.py", "advocacy_plan.py", "advocacy_letter.py", "professional_records.py", "life_plan.py", "care_form_records.py", "self_letters.py", "demo.py"}
    ),
)
def test_決まった手順の層はAIを呼ばない(filename):
    imported = _imported_modules(APP_DIR / filename)

    assert not any(name.startswith("anthropic") for name in imported)
    assert "app.ai" not in imported


def test_AIの層は台帳と絞り込みに触らない():
    imported = _imported_modules(APP_DIR / "ai.py")

    assert not {"app.ledger", "app.consent", "app.db", "app.records"} & imported


def test_AIを呼ぶコードはAIの層だけにある():
    users_of_sdk = [
        path.name for path in APP_DIR.glob("*.py") if "anthropic" in _imported_modules(path)
    ]

    assert users_of_sdk == ["ai.py"]
