"""AIを呼ぶ層。

役割：Claude API を呼び、Pydantic で型を決めた JSON として結果を受け取る。
4つの極：1. 書く・話す（写真の問い）、3. 読み解き、積み上げる（言明への分割）。
守る原則：1（AIは結果を返すだけで、判断や記録の確定はしない）。

決まった手順で動く層（app/ledger.py、app/consent.py）とファイルを分けている。
照合と絞り込みをAIの出来に左右させないため。この分離は tests/test_ai.py で確かめる。

使っている Claude API の機能：
- JSON 出力と Pydantic での検証・やり直し：AIの結果を、決めた型に合うものだけ受け取るため。
  だめなら1回だけやり直す。何度もやり直すと費用と待ち時間が読めなくなるため。
- モデルの使い分け：役割ごとのモデルは app/config.py の1か所で決める。
  選んだ理由の全体の表は docs/tools.md にある。
- 拒否されたときの代替（server-side fallback）：安全の確認で拒否されたときに、
  API の側で別のモデルに切り替えてもらう。Opus と Sonnet だけで使う。Haiku 5.5 にはこの機能が
  ないので送らず、断られた応答（stop_reason が refusal）を AIRefusedError として扱う。
- 画像を読む力：写真の問いのため。
- Message Batches API：大量の処理を半額でまとめて行うため。

どのモデルで作ったかを残すため、結果にはモデル名とトークン数を必ず付ける。失敗したときも、
使った分を例外に持たせて記録できるようにする。

1回の処理で呼ぶ回数：型のやり直しは1回まで。言明への分割では本文のやり直しも1回までなので、
Claude の呼び出しは最悪4回になる。通信の失敗の繰り返し（config.MAX_API_RETRIES）を含めると、
HTTP の要求は最悪12回になる。

接続確認は本物の API で実行した。代替に切り替わる場面は、SDK の説明と、通信を差し替えた
テストでだけ確かめている。
関連するテスト：tests/test_ai.py、tests/test_intake.py
"""

from __future__ import annotations

import base64
import sys
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app import config, prompts
from app.schemas import PhotoQuestion, SplitResult

ResultT = TypeVar("ResultT", bound=BaseModel)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 型に合う答えをもらうまでの回数（最初の1回と、やり直しの1回）
_MAX_ATTEMPTS = 2


class _SpentTokensError(RuntimeError):
    """答えは得られなかったが、費用はかかったときの例外。使った分を記録できるように持つ。"""

    def __init__(self, message: str, model: str, input_tokens: int, output_tokens: int) -> None:
        super().__init__(message)
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class AIResultError(_SpentTokensError):
    """やり直しても、決めた型に合う答えが得られなかったとき。"""


class AIRefusedError(_SpentTokensError):
    """Claude が答えを断ったとき。同じ内容で繰り返しても変わらないので、やり直さない。"""


class AIUnavailableError(RuntimeError):
    """Claude API に届かない、または API がエラーを返したとき。"""


def _describe_api_error(error: anthropic.APIError) -> str:
    """API のエラーを、分かる言葉にする。キーの値は含めない。"""
    if isinstance(error, anthropic.AuthenticationError):
        return "API キーが受け付けられませんでした。.env のキーを確かめてください。"
    if isinstance(error, anthropic.APIConnectionError):
        return "Claude につながりませんでした。ネットワークを確かめてください。"
    if isinstance(error, anthropic.APIStatusError):
        return f"Claude API がエラーを返しました（番号 {error.status_code}）。"
    return "Claude API の呼び出しに失敗しました。"


@dataclass(frozen=True)
class AIResult(Generic[ResultT]):
    """検証を通った答えと、処理の記録に残す情報。"""

    value: ResultT
    model: str
    input_tokens: int
    output_tokens: int


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config.api_key(), max_retries=config.MAX_API_RETRIES)


def _answer_text(content: list) -> str | None:
    """答えの文を取り出す。

    位置ではなく種類で選ぶ。Haiku 5.5 は考える処理が最初から有効で、先頭が thinking のことがあるため。
    fallback の区切りより前は、断ったモデルの途中の出力なので答えとして使わない。
    """
    text = None
    for block in content:
        if block.type == "fallback":
            text = None
        elif block.type == "text" and text is None:
            text = block.text
    return text


def _answering_model(response) -> str:
    """実際に答えたモデルの名前を返す。

    SDK の説明では、代替のモデルが答えた印は usage.iterations の fallback_message にある。
    印がなければ、応答の model の値を使う。
    """
    for iteration in reversed(response.usage.iterations or []):
        if iteration.type == "fallback_message":
            return iteration.model
    return response.model


def _request_params(role: str, system: str, content: str | list, schema: type[BaseModel]) -> dict:
    """1回の呼び出しの中身を作る。すぐに呼ぶときとバッチで同じものを使う。"""
    if role not in config.MODEL_BY_ROLE:
        raise ValueError(f"モデルが決まっていない役割です：{role}")
    output_config: dict = {
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
    }
    if role in config.EFFORT_BY_ROLE:
        output_config["effort"] = config.EFFORT_BY_ROLE[role]
    return {
        "model": config.MODEL_BY_ROLE[role],
        "max_tokens": config.MAX_TOKENS_BY_ROLE.get(role, config.MAX_TOKENS),
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_config": output_config,
    }


def call_structured(
    role: str,
    *,
    system: str,
    content: str | list,
    schema: type[ResultT],
    client: anthropic.Anthropic | None = None,
) -> AIResult[ResultT]:
    """Claude を呼び、schema の型に合う答えを返す。"""
    params = _request_params(role, system, content, schema)
    claude = client or _make_client()
    # 代替の指定は、機能のあるモデルにだけ送る。Haiku 5.5 に送るとエラーになるため。
    options: dict = {}
    if params["model"] in config.MODELS_WITH_FALLBACK:
        options.update(betas=[_FALLBACK_BETA], fallbacks="default")
    if role in config.TIMEOUT_SECONDS_BY_ROLE:
        options["timeout"] = config.TIMEOUT_SECONDS_BY_ROLE[role]

    problem = "答えが空でした"
    # やり直した分の費用も記録に残すため、トークン数は全部の試行を足す
    input_tokens = 0
    output_tokens = 0
    for _ in range(_MAX_ATTEMPTS):
        # SDK の parse は答えを受け取った時点で読み取りまで済ませるので、断られた応答でも
        # 読み取りの失敗になり、断られたことが分からない。先に終わり方を見るために、
        # create で受け取ってから自分で検証する。
        try:
            response = claude.beta.messages.create(**params, **options)
        except anthropic.APIError as error:
            raise AIUnavailableError(_describe_api_error(error)) from error
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise AIRefusedError(
                "Claude が答えを断りました", response.model, input_tokens, output_tokens
            )

        text = _answer_text(response.content)
        if text is None:
            problem = f"答えの文がありませんでした（終わり方：{response.stop_reason}）"
            continue
        try:
            value = schema.model_validate_json(text)
        except ValidationError as error:
            # JSON として読めない、または型に合わないとき
            problem = f"型に合いません（{error.error_count()}か所）"
            continue
        return AIResult(
            value=value,
            model=_answering_model(response),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    raise AIResultError(
        f"決めた型に合う答えが得られませんでした：{problem}",
        params["model"],
        input_tokens,
        output_tokens,
    )


class ConnectionCheck(BaseModel):
    """接続確認の答えの型。"""

    ok: bool


def check_connection(client: anthropic.Anthropic | None = None) -> AIResult[ConnectionCheck]:
    """API キーと通信を確かめる。人の情報は送らず、決まった文だけを送る。"""
    return call_structured(
        "接続確認",
        system="接続の確認です。",
        content="ok を true にして返してください。",
        schema=ConnectionCheck,
        client=client,
    )


def ask_about_photo(
    image: bytes, media_type: str, client: anthropic.Anthropic | None = None
) -> AIResult[PhotoQuestion]:
    """写真を1枚渡し、本人に問いかける文を1つだけ受け取る。画像を読む力を使う。"""
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(image).decode("ascii"),
            },
        },
        {"type": "text", "text": prompts.PHOTO_USER},
    ]
    return call_structured(
        "写真の問い",
        system=prompts.PHOTO_SYSTEM,
        content=content,
        schema=PhotoQuestion,
        client=client,
    )


def split_utterances(
    transcript: str,
    model_summary: str,
    feedback: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> AIResult[SplitResult]:
    """会話やスレッドの発言を言明に分ける。結果の確かめと保存は app/splitter.py が行う。"""
    return call_structured(
        "言明への分割",
        system=prompts.SPLIT_SYSTEM,
        content=prompts.split_user_text(transcript, model_summary, feedback),
        schema=SplitResult,
        client=client,
    )


# ---- Message Batches API ----
# 大量の処理を半額でまとめて行うために使う。すぐに答えが要らない処理だけに使う。
# バッチでは拒否の代替を指定しない。断られた行は「入らなかった行」として数える。


@dataclass(frozen=True)
class BatchItem(Generic[ResultT]):
    """バッチの1行の結果。うまくいかなかった行は value が None で、problem に理由が入る。"""

    custom_id: str
    value: ResultT | None
    problem: str | None
    model: str | None
    input_tokens: int
    output_tokens: int


def batch_request(custom_id: str, role: str, *, system: str, content: str | list, schema) -> dict:
    """バッチの1行を作る。"""
    return {"custom_id": custom_id, "params": _request_params(role, system, content, schema)}


def submit_batch(requests: list[dict], client: anthropic.Anthropic | None = None) -> str:
    """バッチを送り、番号を返す。"""
    claude = client or _make_client()
    try:
        return claude.messages.batches.create(requests=requests).id
    except anthropic.APIError as error:
        raise AIUnavailableError(_describe_api_error(error)) from error


def wait_for_batch(
    batch_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float = 30,
    client: anthropic.Anthropic | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """バッチの終わりを待つ。時間内に終われば True を返す。"""
    claude = client or _make_client()
    deadline = clock() + timeout_seconds
    while True:
        try:
            status = claude.messages.batches.retrieve(batch_id).processing_status
        except anthropic.APIError as error:
            raise AIUnavailableError(_describe_api_error(error)) from error
        if status == "ended":
            return True
        if clock() >= deadline:
            return False
        sleep(poll_seconds)


def batch_results(
    batch_id: str, schema: type[ResultT], client: anthropic.Anthropic | None = None
) -> list[BatchItem[ResultT]]:
    """バッチの結果を読み、型を確かめる。結果の順番は決まっていないので custom_id で見分ける。"""
    claude = client or _make_client()
    items: list[BatchItem[ResultT]] = []
    try:
        results = list(claude.messages.batches.results(batch_id))
    except anthropic.APIError as error:
        raise AIUnavailableError(_describe_api_error(error)) from error
    for entry in results:
        if entry.result.type != "succeeded":
            items.append(BatchItem(entry.custom_id, None, f"バッチの結果：{entry.result.type}", None, 0, 0))
            continue
        message = entry.result.message
        value, problem = None, None
        text = _answer_text(message.content)
        if message.stop_reason == "refusal":
            problem = "Claude が答えを断りました"
        elif text is None:
            problem = f"答えの文がありませんでした（終わり方：{message.stop_reason}）"
        else:
            try:
                value = schema.model_validate_json(text)
            except ValidationError as error:
                problem = f"型に合いません（{error.error_count()}か所）"
        items.append(
            BatchItem(
                entry.custom_id,
                value,
                problem,
                message.model,
                message.usage.input_tokens,
                message.usage.output_tokens,
            )
        )
    return items


def main() -> int:
    """python -m app.ai で接続を確かめる。"""
    try:
        result = check_connection()
    except (config.MissingApiKeyError, AIUnavailableError, AIResultError, AIRefusedError) as error:
        print(error)
        return 1
    print(
        f"接続できました。モデル：{result.model}、"
        f"トークン数：入力 {result.input_tokens}／出力 {result.output_tokens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
