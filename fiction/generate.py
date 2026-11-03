"""10年分の架空ログを Claude で作り、言明に分ける。

役割：fiction/plan.py の予定から会話をバッチで作り、確かめて data/story/ に保存する。次に言明に分ける。
4つの極：3. 読み解き、積み上げる（10年分の材料を作る）。
守る原則：8（人物と記録はすべて架空）、9（決めた言葉は一字も変えない）。

使っている Claude API の機能：Message Batches API。すぐに答えが要らず、半額で済むため。
AI の結果は JSON のまま保存する。作り直すときに AI を呼ばずに済むようにするため（fiction/load.py）。

使い方（順に動かす。途中の状態は data/trial/story_state.json に残る）：
  python -m fiction.generate log-submit
  python -m fiction.generate log-collect --wait-minutes 15
  python -m fiction.generate split-submit
  python -m fiction.generate split-collect --wait-minutes 25

関連するテスト：tests/test_fiction.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app import ai, config, db, intake, prompts, splitter, verbatim
from app.schemas import LifeStage, SplitResult
from fiction import load, plan
from fiction.load import LogConversation

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "trial" / "story_state.json"
BUILD_DB = ROOT / "data" / "trial" / "story_build.db"
# 物語を作る分の上限。全体の上限（50ドル）の中で、固める処理と確かめの分を残す。
STORY_LIMIT_USD = 25.0

LOG_SYSTEM = """あなたは、デモ用の架空の会話ログを書く係です。人物はすべて架空で、実在の人に似せません。

## 人物
- 誠一さん：1952年生まれ。庭と縁側のある一戸建てで妻と二人暮らし。若いころ製材所に勤め、班長になった。
- 和子さん：妻。
- 美穂さん：長女。近くに住み、仕事をしている。
- 健太：孫。
- 正雄さん：将棋の会の友人。
- 節子さん：ご近所さん。庭の花を分け合う。
- ツイン（AI）：誠一さんの相棒のAI。本人になりきらず、誠一さんと呼ぶ。

## 会話の書き方
- ツインと誠一さんが交互に話す、短い会話を書きます。
- ツインの問いは1回に1つだけ。やさしい日本語で、文は短く。推測は「〜かもしれません。合っていますか？」と聞きます。
- 健康や病気の判断をしません。気持ちの相談に乗りません。助言や促しをしません。
- 誠一さんの言葉は、自然な話し言葉で書きます。句点で文を区切ります。
- 遠慮や建前の表現は、10回の会話に1回くらいにします。
- 話題は、指定された話題とテーマから離れません。庭と紫陽花の話に寄せません。
- 上の人物のほかに、名前のある新しい人物を出しません。
- 泊まり、ショートステイ、病名、診断の話は書きません。つらかった出来事は、指定があるときだけ書きます。
- 「一字も変えない言葉」の指定があるときは、誠一さんの発言の中に、その言葉をそのまま1つの文として入れます。前後の文とは句点で区切ります。

## 会話の終わり
- 最後から2番目に、ツインが「今日うかがったこと」を1文でまとめ「合っていますか？」と聞きます。
- 最後に、誠一さんが答えます。confirmation が「合っています」なら「合っています。」と答えます。
  「直した」なら、どこが違うかを誠一さんの言葉で短く直します。

## コメント
- 指定があるときだけ letter（便り）と comments を書きます。指定がなければ letter は null、comments は空の配列にします。
- コメントは、その人の立場で1〜2文で書きます。美穂さんは娘、和子さんは妻、正雄さんは友人、節子さんはご近所さんです。
- 「一字も変えないコメント」の指定があるときは、その文をそのまま入れます。"""


def log_user_text(item: plan.PlanItem) -> str:
    """1回の会話を書くための指示の文を作る。"""
    care = item.stage is LifeStage.CARE
    lines = [
        f"日付：{item.day.isoformat()}",
        f"誠一さんの年齢：{item.age}歳",
        f"この時期の暮らし：{item.setting}",
        f"話題：{item.topic}",
        f"テーマ：{item.theme.value}",
        f"confirmation：{'直した' if load.needs_correction(item) else '合っています'}",
        "長さ：全部で4〜6発言。誠一さんの言葉は短くします。" if care else "長さ：全部で6〜8発言。",
    ]
    if item.must_say:
        lines.append(f"一字も変えない言葉（誠一さん）：{item.must_say}")
    if item.private:
        lines.append("誠一さんが自分から話す場面です。ツインは聞き返さず、受け止めるだけにします。")
    if item.photo_note:
        lines.append("誠一さんが写真を見せた場面から始めます。trigger に、写真に写っているものを1文で書きます。人の名前は書きません。")
    if item.commenters:
        lines.append(f"この会話をもとにした便りを letter に1〜2文で書き、次の人がコメントします：{'、'.join(item.commenters)}")
    for speaker, words in item.must_comments:
        lines.append(f"一字も変えないコメント（{speaker}）：{words}")
    return "\n".join(lines)


def log_problems(item: plan.PlanItem, log: LogConversation) -> list[str]:
    """作った会話を確かめる。決めた言葉、書かない語、コメントの人。"""
    problems: list[str] = []
    self_turns = [turn.text for turn in log.turns if turn.speaker == "誠一さん"]
    if item.must_say and not any(verbatim.quote_problem(text, item.must_say) is None for text in self_turns):
        problems.append(f"決めた言葉が1つの文として入っていません：{item.must_say}")
    written = " ".join([log.trigger, log.letter or "", *(t.text for t in log.turns), *(c.text for c in log.comments)])
    for word in plan.FORBIDDEN_WORDS:
        if word in written:
            problems.append(f"書かない語があります：{word}")
    if len(self_turns) < 2:
        problems.append("誠一さんの発言が足りません")
    for speaker, words in item.must_comments:
        if not any(c.speaker == speaker and words in c.text for c in log.comments):
            problems.append(f"決めたコメントがありません：{words}")
    if set(item.commenters) - {comment.speaker for comment in log.comments}:
        problems.append("指定した人のコメントがありません")
    return problems


def _read_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _guard(state: dict, role: str, requests: int, input_tokens_each: int) -> None:
    """応答が上限いっぱいでも上限を超えないことを確かめてから送る。"""
    worst = config.cost_usd(
        config.MODEL_BY_ROLE[role], input_tokens_each * requests, config.MAX_TOKENS_BY_ROLE[role] * requests, batch=True
    )
    spent = state.get("spent_usd", 0.0)
    # 上限いっぱいの応答は起きにくいので、実績に近い4分の1で見て、超えないことを確かめる
    if spent + worst / 4 > STORY_LIMIT_USD:
        raise SystemExit(f"費用の上限を超える恐れがあるので送りません（使用 ${spent:.2f}、見積もり ${worst / 4:.2f}）")


def _record(state: dict, stage: str, results: list) -> float:
    total = 0.0
    for result in results:
        if result.model:
            total += config.cost_usd(result.model, result.input_tokens, result.output_tokens, batch=True)
    state.setdefault("usage", []).append({"stage": stage, "cost_usd": round(total, 4), "count": len(results)})
    state["spent_usd"] = round(state.get("spent_usd", 0.0) + total, 4)
    return total


def log_submit(_args) -> int:
    state = _read_state()
    done = load.read_logs()
    items = [item for item in plan.build_plan() if item.key not in done]
    if not items:
        print("作る会話はありません。")
        return 0
    _guard(state, "架空ログの作成", len(items), 1500)
    requests = [
        ai.batch_request(item.key, "架空ログの作成", system=LOG_SYSTEM, content=log_user_text(item), schema=LogConversation)
        for item in items
    ]
    state["log_batch_id"] = ai.submit_batch(requests)
    state["log_submitted_at"] = time.time()
    _write_state(state)
    print(f"架空ログのバッチを送りました（{len(requests)}件）。")
    return 0


def log_collect(args) -> int:
    state = _read_state()
    batch_id = state["log_batch_id"]
    if not ai.wait_for_batch(batch_id, timeout_seconds=args.wait_minutes * 60):
        print("まだ終わっていません。")
        return 3
    results = ai.batch_results(batch_id, LogConversation)
    cost = _record(state, "架空ログの作成", results)
    plans = {item.key: item for item in plan.build_plan()}
    logs = load.read_logs()
    rejected = []
    for result in results:
        problems = [result.problem] if result.problem else log_problems(plans[result.custom_id], result.value)
        if problems:
            rejected.append({"key": result.custom_id, "problems": problems})
        else:
            logs[result.custom_id] = result.value.model_dump(mode="json")
    load.write_logs(logs)
    state["log_rejected"] = rejected
    _write_state(state)
    print(f"会話 {len(logs)}/{len(plans)} 件。今回 ${cost:.2f}、合計 ${state['spent_usd']:.2f}。入らなかった {len(rejected)} 件。")
    for item in rejected[:20]:
        print(f"- {item['key']}：{item['problems']}")
    return 0


def split_submit(_args) -> int:
    state = _read_state()
    done = load.read_splits()
    BUILD_DB.unlink(missing_ok=True)
    db.init_db(BUILD_DB)
    conn = db.connect(BUILD_DB)
    units = load.load_logs(conn, plan.build_plan(), load.read_logs())
    users = db.list_users(conn)
    requests = []
    for custom_id, unit in units.items():
        if custom_id in done:
            continue
        utterances = splitter.load_utterances(
            conn, conversation_id=unit.get("conversation_id"), letter_id=unit.get("letter_id")
        )
        letter_body = None
        if unit.get("letter_id"):
            letter_body = conn.execute("SELECT body FROM letters WHERE id = ?", (unit["letter_id"],)).fetchone()["body"]
        transcript = splitter.format_transcript(
            unit["place"], splitter.first_spoken_on(utterances), utterances, users, letter_body
        )
        requests.append(
            ai.batch_request(
                custom_id,
                "言明への分割",
                system=prompts.split_system("誠一さん"),
                content=prompts.split_user_text(transcript, intake.NO_MODEL_SUMMARY),
                schema=SplitResult,
            )
        )
    conn.close()
    if not requests:
        print("分ける会話はありません。")
        return 0
    _guard(state, "言明への分割", len(requests), 6000)
    state["split_batch_id"] = ai.submit_batch(requests)
    state["split_submitted_at"] = time.time()
    _write_state(state)
    print(f"言明への分割のバッチを送りました（{len(requests)}件）。")
    return 0


def split_collect(args) -> int:
    state = _read_state()
    batch_id = state["split_batch_id"]
    if not ai.wait_for_batch(batch_id, timeout_seconds=args.wait_minutes * 60):
        print("まだ終わっていません。")
        return 3
    results = ai.batch_results(batch_id, SplitResult)
    cost = _record(state, "言明への分割", results)
    splits = load.read_splits()
    failed = []
    for result in results:
        if result.value is None:
            failed.append({"key": result.custom_id, "problem": result.problem})
        else:
            splits[result.custom_id] = result.value.model_dump(mode="json")
    load.write_splits(splits)
    state["split_failed"] = failed
    _write_state(state)
    print(f"分割 {len(splits)} 件。今回 ${cost:.2f}、合計 ${state['spent_usd']:.2f}。失敗 {len(failed)} 件。")
    for item in failed[:20]:
        print(f"- {item['key']}：{item['problem']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="10年分の架空ログを作ります。")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("log-submit").set_defaults(run=log_submit)
    collect = commands.add_parser("log-collect")
    collect.add_argument("--wait-minutes", type=float, default=15)
    collect.set_defaults(run=log_collect)
    commands.add_parser("split-submit").set_defaults(run=split_submit)
    collect = commands.add_parser("split-collect")
    collect.add_argument("--wait-minutes", type=float, default=25)
    collect.set_defaults(run=split_collect)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
