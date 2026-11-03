"""テスト用の Claude API の代わり。

役割：本物の SDK を使い、通信の部分だけを差し替える。ネットには出ず、費用もかからない。
4つの極：特定の極に属さない土台（テストの道具）。
守る原則：8（テストの内容も架空）。
"""

from __future__ import annotations

import json

import anthropic
import httpx2


def message(text: str, stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def split_message(*statements: dict) -> dict:
    return message(json.dumps({"statements": list(statements)}, ensure_ascii=False))


class FakeClaude:
    """決まった順番で答える。"""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, int):
            return httpx2.Response(response, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        return httpx2.Response(200, json=response)

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(self)),
            max_retries=0,
        )

    def request_body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


# プロンプトの書き出しから、どの役割の呼び出しかを見分ける
_ROLE_MARKS = {
    "固める処理": "意思モデルを作る係",
    "ツインの答え": "要素の番号を選ぶ係",
    "推定意思": "推定を書く係",
    "言明への分割": "記録の整理係",
    "人の候補": "話に出てきた人を抜き出します",
    "会話の要約": "の会話を要約します",
    "便りの下書き": "便りの下書きを作ります",
    "スレッドの問い": "便りのスレッドに",
    "グループのツイン": "グループの部屋にいます",
    "今日はここまで": "今日の話を終えました",
    "ツインの会話": "毎日の話を聞き",
}


def role_of(body: dict) -> str:
    system = body.get("system", "")
    # プロンプトキャッシュを使うときは、ブロックの配列で届く
    if isinstance(system, list):
        system = "\n".join(block["text"] for block in system)
    for role, mark in _ROLE_MARKS.items():
        if mark in system:
            return role
    return "不明"


class RoleClaude:
    """役割ごとに決まった答えを返す。答えを決めていない役割は、中身のない答えを返す。

    言明、要約、候補を裏で動かす処理のテストで、呼ばれた順番に左右されないようにするため。
    送られた本文をすべて覚えるので、AI に渡らないことの確かめにも使える。
    """

    def __init__(self, **answers) -> None:
        self.answers = {
            "言明への分割": {"statements": []},
            "人の候補": {"people": []},
            "会話の要約": {"summary": "要約です。"},
            "便りの下書き": {"body": "下書きです。"},
            "スレッドの問い": {"question": None},
            "グループのツイン": {"kind": "書かない", "text": ""},
            "今日はここまで": {"reply": "今日もお話をありがとうございました。", "tip": "夕方は少し冷えそうです。"},
            "ツインの会話": {"reply": "そうなのですね。"},
            "固める処理": {"elements": []},
            "ツインの答え": {"direct_numbers": [], "related_numbers": [], "request_kind": "通常", "lead": None},
            "推定意思": {
                "estimate": "推定です。",
                "evidence_numbers": [],
                "counter_numbers": [],
                "confidence": 0.3,
                "how_to_confirm": "ご本人にうかがってください。",
            },
        }
        self.answers.update({key.replace("_", ""): value for key, value in answers.items()})
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        answer = self.answers[role_of(body)]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, int):
            return httpx2.Response(answer, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        if isinstance(answer, str) and answer == "断る":
            return httpx2.Response(200, json=message("", stop_reason="refusal", model=body["model"]))
        return httpx2.Response(200, json=message(json.dumps(answer, ensure_ascii=False), model=body["model"]))

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(self)),
            max_retries=0,
        )

    def bodies(self, role: str | None = None) -> list[dict]:
        bodies = [json.loads(request.content) for request in self.requests]
        return [body for body in bodies if role is None or role_of(body) == role]

    def sent_text(self) -> str:
        """送られた本文を1つの文字列にする。AI に渡らないことの確かめに使う。"""
        return "\n".join(request.content.decode("utf-8") for request in self.requests)
