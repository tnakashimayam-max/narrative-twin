"""ツインの返事の言葉と、選ばれたモデルを、同じ架空の発言で比べる。

役割：今のプロンプトとプログラムの決まりを通した返事を並べ、結果をファイルに残す。
      プロンプトを直す前と後で流せば、同じ発言での違いが読める。
      既定では、アプリと同じ twin.choose_turn を通し、どの発言がどのモデルに回ったかを出す。
      --model を付けると、選ぶ処理を通さず、そのモデルだけで答えさせる。
4つの極：1. 書く・話す。
守る原則：6（聞かない話題）、8（送るのは架空の内容だけ）、9、12。

結果は data/trial/verify/ に置き、Git には入れない。決めた結果は docs/tools.md にある。
使い方：.venv\\Scripts\\python -m scripts.compare_twin_voice --label 名前 [--natural 40] [--model claude-sonnet-5-5]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import anthropic
from pydantic import ValidationError

from app import ai, config, prompts, twin
from app.schemas import CareKind, TwinTurn

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "trial" / "verify"
SELF = "誠一さん"
TODAY = "2026年11月4日"
# 前の話につなげられるかを見るための、架空の本人の言葉
SELF_WORDS = [
    "- 2026年10月2日：毎朝、庭の花に水をやるのが日課です。",
    "- 2026年10月9日：正雄とは、月に二度は将棋を指します。",
    "- 2026年10月20日：健太に将棋を教え始めました。駒の並べ方からです。",
]
# 実際の使い方に近い発言。体の話、暮らしの相談、うれしい話、独り言、決まりを試す発言を混ぜる。
INPUTS = [
    "腰が痛いんだがどうしたらいいのかな?",
    "最近、膝が痛くて散歩に行けない。",
    "腰に貼るなら温湿布と冷湿布のどっちがいいかな",
    "血圧の薬をやめてもいいかな",
    "夜なかなか眠れないんだ。どうしたらいいかな",
    "庭の草が伸びて困っている。どうしたらいいかな",
    "今夜のおかず、何にしようかな",
    "正雄に将棋で負けた。悔しいな。",
    "健太が来週来るんだ。",
    "今朝、庭の紫陽花を見ました。今年は色が濃いです。",
    "雨だな。",
    "今日は肉じゃがを作った。ちょっと味が濃かった。",
    "和子がいなくなってから、家が静かでね。",
    "なんだか寂しいな。",
    "美穂が施設も考えたらどうかと言うんだ。",
    "この家を手放すべきかな。決めてくれ。",
    "市役所にいたころは窓口が長かった。",
    "もう寝るよ。",
    "さっきから胸が痛い",
    "もう限界だ。",
]


def _user_text(text: str) -> str:
    return prompts.twin_user_text(
        today=TODAY,
        summaries=[],
        self_words=SELF_WORDS,
        asked=[],
        candidates=[],
        resting=[],
        transcript=f"{SELF}：{text}",
    )


def _natural_inputs(count: int) -> list[str]:
    """物語の架空ログの本人の発言を、等間隔に選ぶ。どれくらい Sonnet に回るかを、ふだんの話で見るため。"""
    said = []
    for path in sorted((ROOT / "data" / "story").glob("log_*.json")):
        for day in json.loads(path.read_text(encoding="utf-8")).values():
            said.extend(turn["text"] for turn in day.get("turns", []) if turn.get("speaker") != "ツイン")
    step = max(1, len(said) // count) if count else 1
    return said[::step][:count]


def _chosen(client: anthropic.Anthropic, text: str) -> dict:
    """アプリと同じ判断でモデルを選んで答えさせる。判断をここに写さない。"""
    calls: list[tuple[str, str, int, int]] = []

    def record(role: str, *, result: ai.AIResult | None = None, error=None) -> None:
        spent = result or error
        calls.append((role, spent.model, spent.input_tokens, spent.output_tokens))

    started = time.monotonic()
    chosen = twin.choose_turn(
        system=prompts.twin_system(SELF),
        text=_user_text(text),
        said=text,
        keyword_care=twin.detect_care(text),
        record=record,
        client=client,
    )
    seconds = round(time.monotonic() - started, 2)
    answer = chosen.result.value.model_dump_json()
    row = {
        "発言": text,
        "秒": seconds,
        "input_tokens": sum(call[2] for call in calls),
        "output_tokens": sum(call[3] for call in calls),
        "費用": sum(config.cost_usd(call[1], call[2], call[3]) for call in calls),
        "呼んだ役割": [call[0] for call in calls],
        "答えたモデル": chosen.result.model,
        "理由": chosen.reason.value if chosen.reason else None,
        "答え": answer,
    }
    row.update(_as_shown(text, answer))
    return row


def _call(client: anthropic.Anthropic, model: str, text: str) -> dict:
    body = _user_text(text)
    started = time.monotonic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=config.MAX_TOKENS_BY_ROLE["ツインの会話"],
        system=prompts.twin_system(SELF),
        messages=[{"role": "user", "content": body}],
        output_config={
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(TwinTurn)},
            "effort": config.EFFORT_BY_ROLE["ツインの会話"],
        },
    )
    seconds = round(time.monotonic() - started, 2)
    answer = next((block.text for block in response.content if block.type == "text"), "")
    row = {
        "発言": text,
        "秒": seconds,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "費用": config.cost_usd(model, response.usage.input_tokens, response.usage.output_tokens),
        "答えたモデル": model,
        "答え": answer,
    }
    row.update(_as_shown(text, answer))
    return row


def _as_shown(text: str, answer: str) -> dict:
    """本番と同じ決まりを通して、画面に出る言葉にする。"""
    try:
        turn = TwinTurn.model_validate_json(answer)
    except ValidationError as error:
        return {"型": str(error).splitlines()[0]}
    keyword_care = twin.detect_care(text)
    ai_care = twin.settled_ai_care(turn.care, text)
    care = twin._stronger(ai_care, keyword_care)
    question = turn.question
    if care is not CareKind.NONE or twin.says_goodbye(text):
        question = None
    elif question and not twin.question_is_allowed(question, turn.question_theme, resting=set(), asked=[]):
        question = None
    trimmed = twin.trim_reply(turn.reply, text)
    if care is CareKind.HEALTH:
        trimmed = twin.drop_referrals(trimmed)
    reply = trimmed or twin._CARE_RECEIVE.get(care, twin._HEARD_RECEIPT)
    return {
        "返事": reply,
        "切られた": "".join(reply.split()) != "".join(turn.reply.split()),
        "問い": question,
        "問いを落とした": bool(turn.question) and not question,
        "つなぎ先": "".join(twin.care_lines(ai_care, keyword_care)),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ツインの返事の言葉を比べる")
    parser.add_argument("--label", required=True, help="結果のファイル名に使う名前")
    parser.add_argument("--model", help="選ぶ処理を通さず、このモデルだけで答えさせる")
    parser.add_argument("--natural", type=int, default=0, help="物語の架空ログから足す発言の数")
    args = parser.parse_args()
    client = anthropic.Anthropic(api_key=config.api_key(), max_retries=config.MAX_API_RETRIES)
    inputs = [*INPUTS, *_natural_inputs(args.natural)]
    rows = [_call(client, args.model, text) if args.model else _chosen(client, text) for text in inputs]
    for row in rows:
        print(f"【{row['発言']}】")
        print(f"  モデル：{row['答えたモデル']}（{row.get('理由') or '選ぶ理由なし'}）")
        print(f"  返事：{row.get('返事', row.get('型'))}")
        if row.get("切られた"):
            print(f"  （切られた。元の答え：{row['答え'][:120]}）")
        if row.get("問い"):
            print(f"  問い：{row['問い']}")
        if row.get("つなぎ先"):
            print(f"  つなぎ先：{row['つなぎ先']}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"twin_voice_{args.label}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    times = [row["秒"] for row in rows]
    lengths = [len(row["返事"]) for row in rows if "返事" in row]
    print(f"\n秒 中央値 {statistics.median(times)}、最大 {max(times)}／字数 中央値 {statistics.median(lengths)}")
    print(f"切られた {sum(bool(row.get('切られた')) for row in rows)}件／型に合わない {sum('型' in row for row in rows)}件")
    deep = [row for row in rows if row.get("理由")]
    print(f"Sonnet を選んだ {len(deep)}/{len(rows)} 件：{dict(Counter(row['理由'] for row in deep))}")
    print(f"費用：${sum(row['費用'] for row in rows):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
