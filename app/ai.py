"""AIを呼ぶ層。

役割：Claude API を呼び、Pydantic で型を決めた JSON として結果を受け取る。
4つの極：3. 読み解き、積み上げる（これから作る処理の呼び出し口）。
守る原則：1（AIは結果を返すだけで、判断や記録の確定はしない）。

決まった手順で動く層（app/ledger.py、app/consent.py）とファイルを分けている。
照合と絞り込みをAIの出来に左右させないため。この分離は tests/test_ai.py で確かめる。

使っている Claude API の機能：
- JSON 出力と Pydantic での検証・やり直し：AIの結果を、決めた型に合うものだけ受け取るため。
  だめなら1回だけやり直す。何度もやり直すと費用と待ち時間が読めなくなるため。
- モデルの使い分け：役割ごとのモデルは app/config.py の1か所で決める。
  選んだ理由の全体の表は docs/tools.md にある。
- 拒否されたときの代替（server-side fallback）：安全の確認で拒否されたときに、
  API の側で別のモデルに切り替えてもらう。

どのモデルで作ったかを残すため、結果にはモデル名とトークン数を必ず付ける。

現時点でこの層を使うのは、python -m app.ai の接続確認だけ。接続確認は本物の API で実行した。
代替に切り替わる場面は、SDK の説明と、通信を差し替えたテストでだけ確かめている。
関連するテスト：tests/test_ai.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app import config

ResultT = TypeVar("ResultT", bound=BaseModel)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 型に合う答えをもらうまでの回数（最初の1回と、やり直しの1回）
_MAX_ATTEMPTS = 2


class AIResultError(RuntimeError):
    """やり直しても、決めた型に合う答えが得られなかったとき。"""


class AIRefusedError(RuntimeError):
    """Claude が答えを断ったとき。同じ内容で繰り返しても変わらないので、やり直さない。"""


@dataclass(frozen=True)
class AIResult(Generic[ResultT]):
    """検証を通った答えと、処理の記録に残す情報。"""

    value: ResultT
    model: str
    input_tokens: int
    output_tokens: int


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config.api_key())


def _answer_text(content: list) -> str | None:
    """答えの文を取り出す。

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


def call_structured(
    role: str,
    *,
    system: str,
    user_text: str,
    schema: type[ResultT],
    client: anthropic.Anthropic | None = None,
) -> AIResult[ResultT]:
    """Claude を呼び、schema の型に合う答えを返す。"""
    if role not in config.MODEL_BY_ROLE:
        raise ValueError(f"モデルが決まっていない役割です：{role}")
    claude = client or _make_client()
    output_format = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}

    problem = "答えが空でした"
    # やり直した分の費用も記録に残すため、トークン数は全部の試行を足す
    input_tokens = 0
    output_tokens = 0
    for _ in range(_MAX_ATTEMPTS):
        # SDK の parse は答えを受け取った時点で読み取りまで済ませるので、断られた応答でも
        # 読み取りの失敗になり、断られたことが分からない。先に終わり方を見るために、
        # create で受け取ってから自分で検証する。
        response = claude.beta.messages.create(
            model=config.MODEL_BY_ROLE[role],
            max_tokens=config.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"format": output_format},
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise AIRefusedError("Claude が答えを断りました")

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

    raise AIResultError(f"決めた型に合う答えが得られませんでした：{problem}")


class ConnectionCheck(BaseModel):
    """接続確認の答えの型。"""

    ok: bool


def check_connection(client: anthropic.Anthropic | None = None) -> AIResult[ConnectionCheck]:
    """API キーと通信を確かめる。人の情報は送らず、決まった文だけを送る。"""
    return call_structured(
        "接続確認",
        system="接続の確認です。",
        user_text="ok を true にして返してください。",
        schema=ConnectionCheck,
        client=client,
    )


def main() -> int:
    """python -m app.ai で接続を確かめる。"""
    try:
        result = check_connection()
    except config.MissingApiKeyError as error:
        print(error)
        return 1
    except anthropic.AuthenticationError:
        print("API キーが受け付けられませんでした。.env のキーを確かめてください。")
        return 1
    except anthropic.APIConnectionError:
        print("Claude につながりませんでした。ネットワークを確かめてください。")
        return 1
    except anthropic.APIStatusError as error:
        print(f"Claude API がエラーを返しました（番号 {error.status_code}）。")
        return 1
    except (AIResultError, AIRefusedError) as error:
        print(error)
        return 1
    print(
        f"接続できました。モデル：{result.model}、"
        f"トークン数：入力 {result.input_tokens}／出力 {result.output_tokens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
