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
