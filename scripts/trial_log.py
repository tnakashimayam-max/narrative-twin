"""架空ログの小さな試し（2026年の1年分）。

役割：架空の会話を Message Batches API で作り、ledger を通して入れ、言明に分けて、費用と時間を測る。
4つの極：3. 読み解き、積み上げる。
守る原則：8（人物と記録はすべて架空。実在の人に似せない）、9（本人の言葉は一字も変えない）。

できたデータは data/trial/ に置き、Git には入れない。測った結果は docs/tools.md に書く。
AI の結果を直接表に書かず、必ず ledger と splitter を通す。決まりに合わない行は入らず、数を記録する。

使い方（順に動かす。各段階の結果は data/trial/trial_state.json に残る）：
  python -m scripts.trial_log submit-log
  python -m scripts.trial_log collect-log --wait-minutes 8
  python -m scripts.trial_log submit-split
  python -m scripts.trial_log collect-split --wait-minutes 8
  python -m scripts.trial_log report
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app import ai, config, db, ledger, prompts, splitter
from app.schemas import (
    CommentDraft,
    ConversationDraft,
    LetterDraft,
    LifeStage,
    Place,
    ShareScope,
    Theme,
    Utterance,
    UtteranceDraft,
)

ROOT = Path(__file__).resolve().parent.parent
TRIAL_DIR = ROOT / "data" / "trial"
STATE_PATH = TRIAL_DIR / "trial_state.json"
LOG_PATH = TRIAL_DIR / "log_2026.json"
DB_PATH = TRIAL_DIR / "trial_2026.db"
REPORT_PATH = TRIAL_DIR / "report_2026.json"

CONVERSATION_COUNT = 40
YEARS_FOR_ESTIMATE = 10
CONVERSATIONS_PER_YEAR_FOR_ESTIMATE = 40
TONIGHT_LIMIT_USD = 5.0

FIRST_DAY = date(2026, 4, 12)
LAST_DAY = date(2026, 12, 20)
# デモでその場で行う日。ログには入れない。
DEMO_DAY = date(2026, 11, 3)

# 決めた日に必ず入る本人の言葉
FIXED_SELF_WORDS = {
    date(2026, 4, 12): "毎朝この庭を見るのが楽しみなんです",
    date(2026, 5, 10): "最初の職場は製材所だった",
}
# 秋の家族の証言
AUTUMN_DAY = date(2026, 9, 20)
AUTUMN_COMMENT = "夏休みに、父と縁側でスイカを食べた"

# 「つらかったこと」は本人がお休みにしているので、話題にしない
THEMES = [theme for theme in Theme if theme is not Theme.HARDSHIP]


LOG_SYSTEM = """あなたは、デモ用の架空の会話ログを書く係です。人物はすべて架空で、実在の人に似せません。

## 人物
- 誠一さん：2026年に74歳。庭と縁側のある一戸建てで妻と二人暮らし。若いころ材木屋の手伝いを経て製材所に勤め、働き盛りは班長。退職後は庭づくりと紫陽花が楽しみ。元気な時期です。
- 和子さん：妻。72歳。縁側での朝のお茶が二人の日課。
- 美穂さん：長女。離れて暮らし、仕事と子育てで忙しい。
- 節子さん：ご近所の友人。同じ町内で、庭の花を分け合う仲。
- ツイン（AI）：誠一さんの相棒のAI。本人になりきらず、誠一さんと呼ぶ。

## 会話の書き方
- ツインと誠一さんが交互に話す、短い会話を書きます。全部で6〜8発言です。
- ツインの問いは1回に1つだけ。やさしい日本語で、文は短く。推測は「〜かもしれません。合っていますか？」と聞きます。
- 健康や病気の判断をしません。気持ちの相談に乗りません。
- 誠一さんの言葉は、自然な話し言葉で書きます。句点で文を区切ります。
- 遠慮や建前の表現（「迷惑はかけたくない」「もういいよ」など）は、10回の会話に1回くらいにします。毎回は入れません。
- つらかった出来事、子ども時代の家族、若いころの住まいは話題にしません。
- 話題は、指定されたテーマから離れません。庭と紫陽花の話に寄せません。
- ツインは助言や促しをしません。本人を理解するための問いにとどめます。
- 上の人物のほかに、新しい人物を出しません。
- 一字も変えない言葉の指定があるときは、その言葉で1文を終えます。

## 会話の終わり
- 最後から2番目に、ツインが「今日うかがったこと」を1文でまとめ「合っていますか？」と聞きます。
- 最後に、誠一さんが答えます。confirmation が「合っています」なら「合っています。」と答えます。
  「直した」なら、どこが違うかを誠一さんの言葉で短く直します。

## コメント
- 指定があるときだけ comments を書きます。指定がなければ空の配列にします。
- 美穂さんは娘の立場、節子さんは友人の立場で、1〜2文で書きます。"""


class LogTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["ツイン", "誠一さん"]
    text: str


class LogComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["美穂さん", "節子さん"]
    text: str


class LogConversation(BaseModel):
    """架空の会話1回分。"""

    model_config = ConfigDict(extra="forbid")

    trigger: str
    turns: list[LogTurn]
    confirmation: Literal["合っています", "直した"]
    letter: str | None = None
    comments: list[LogComment] = []


@dataclass(frozen=True)
class PlanItem:
    """1回の会話の予定。"""

    index: int
    day: str
    theme: str
    confirmation: str
    must_say: str | None
    commenters: list[str]
    must_comment: str | None


def plan_conversations(count: int = CONVERSATION_COUNT) -> list[PlanItem]:
    """1年分の会話の予定を作る。毎回同じ結果になるよう、乱数を使わない。"""
    fixed_days = [*FIXED_SELF_WORDS, AUTUMN_DAY]
    span = (LAST_DAY - FIRST_DAY).days
    days = set(fixed_days)
    others = count - len(fixed_days)
    for step in range(others):
        day = FIRST_DAY + timedelta(days=round(step * span / (others - 1)))
        # 決めた日やデモの日と重なったら、次の日にずらす
        while day in days or day == DEMO_DAY:
            day += timedelta(days=1)
        days.add(day)
    items = []
    for index, day in enumerate(sorted(days)):
        commenters: list[str] = []
        if day == AUTUMN_DAY:
            commenters = ["美穂さん"]
        elif index % 6 == 5:
            commenters = ["美穂さん", "節子さん"] if index % 12 == 11 else ["節子さん"]
        items.append(
            PlanItem(
                index=index,
                day=day.isoformat(),
                theme=THEMES[index % len(THEMES)].value,
                # 確認はほとんど「合っています」で、ときどき「直した」にする
                confirmation="直した" if index % 9 == 8 else "合っています",
                must_say=FIXED_SELF_WORDS.get(day),
                commenters=commenters,
                must_comment=AUTUMN_COMMENT if day == AUTUMN_DAY else None,
            )
        )
    return items


def log_user_text(item: PlanItem) -> str:
    """1回の会話を書くための指示の文を作る。"""
    lines = [
        f"日付：{item.day}",
        f"話題の中心のテーマ：{item.theme}",
        f"confirmation：{item.confirmation}",
    ]
    if item.must_say:
        lines.append(f"誠一さんは、次の言葉を一字も変えずに言います：「{item.must_say}」")
    if item.commenters:
        lines.append(f"この会話をもとにした便りを letter に1〜2文で書き、次の人がコメントします：{'、'.join(item.commenters)}")
    if item.must_comment:
        lines.append(f"美穂さんのコメントには、次の言葉を一字も変えずに入れます：「{item.must_comment}」")
    return "\n".join(lines)


def _speaker_args(name: str, users: dict) -> dict:
    if name == "ツイン":
        return {"speaker_user_id": None, "speaker_is_twin": True}
    return {"speaker_user_id": users[name].id, "speaker_is_twin": False}


def plan_problems(item: PlanItem, log: LogConversation) -> list[str]:
    """決めた言葉が入っているかを確かめる。"""
    problems = []
    if item.must_say and not any(
        turn.speaker == "誠一さん" and item.must_say in turn.text for turn in log.turns
    ):
        problems.append(f"決めた言葉がありません：{item.must_say}")
    if item.must_comment and not any(item.must_comment in comment.text for comment in log.comments):
        problems.append(f"決めたコメントがありません：{item.must_comment}")
    return problems


def load_log(conn, item: PlanItem, log: LogConversation) -> dict[str, dict]:
    """1回の会話を ledger を通して入れ、言明に分ける単位を返す。"""
    users = {user.name: user for user in db.list_users(conn)}
    day = date.fromisoformat(item.day)
    conversation = ledger.add_conversation(
        conn,
        ConversationDraft(
            conversation_date=day,
            trigger=log.trigger,
            theme=Theme(item.theme),
            life_stage=LifeStage.ACTIVE,
            self_confirmation=log.confirmation,
        ),
    )
    for number, turn in enumerate(log.turns):
        ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation.id,
                text=turn.text,
                spoken_at=datetime(day.year, day.month, day.day, 9, number),
                place=Place.DIARY_AND_TALK,
                **_speaker_args(turn.speaker, users),
            ),
        )
    units = {f"conv-{item.index}": {"conversation_id": conversation.id, "place": Place.DIARY_AND_TALK.value}}
    if log.comments:
        # 節子さんのコメントがあるときは縁のある人まで、なければ家族の便りにする
        share = (
            ShareScope.CONNECTED
            if any(comment.speaker == "節子さん" for comment in log.comments)
            else ShareScope.FAMILY
        )
        letter = ledger.add_letter(
            conn,
            LetterDraft(
                conversation_id=conversation.id,
                body=log.letter or log.trigger,
                share_with=share,
                shared_at=datetime(day.year, day.month, day.day, 12, 0),
            ),
        )
        for number, comment in enumerate(log.comments):
            ledger.add_comment(
                conn,
                CommentDraft(
                    letter_id=letter.id,
                    author_user_id=users[comment.speaker].id,
                    body=comment.text,
                    commented_at=datetime(day.year, day.month, day.day, 18, number),
                ),
            )
        units[f"letter-{item.index}"] = {"letter_id": letter.id, "place": Place.THREAD.value}
    return units


def estimate_forecast(conversations: int, usage: list[dict], seconds: float) -> dict:
    """試しの結果から、10年分の費用と時間の見込みを出す。回数に比例するとして計算する。"""
    total = sum(item["cost_usd"] for item in usage)
    target = YEARS_FOR_ESTIMATE * CONVERSATIONS_PER_YEAR_FOR_ESTIMATE
    scale = target / conversations
    return {
        "conversations_in_trial": conversations,
        "conversations_in_estimate": target,
        "trial_cost_usd": round(total, 4),
        "estimate_cost_usd": round(total * scale, 2),
        "trial_batch_seconds": round(seconds, 1),
    }


def _read_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def _write_state(state: dict) -> None:
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _worst_case_usd(role: str, requests: int, input_tokens_each: int) -> float:
    """1つのバッチの費用の上限を見積もる。応答が上限いっぱいでもこの額を超えない。"""
    output = config.MAX_TOKENS_BY_ROLE[role]
    return config.cost_usd(config.MODEL_BY_ROLE[role], input_tokens_each * requests, output * requests, batch=True)


def _guard_budget(state: dict, worst_case: float) -> None:
    spent = state.get("spent_usd", 0.0)
    if spent + worst_case > TONIGHT_LIMIT_USD:
        raise SystemExit(f"費用の上限を超える恐れがあるので送りません（使用 ${spent:.2f}、上限見積もり ${worst_case:.2f}）")


def submit_log(args) -> int:
    state = _read_state()
    state.setdefault("spent_usd", args.spent)
    items = plan_conversations()
    _guard_budget(state, _worst_case_usd("架空ログの作成", len(items), 1500))
    requests = [
        ai.batch_request(
            f"log-{item.index}",
            "架空ログの作成",
            system=LOG_SYSTEM,
            content=log_user_text(item),
            schema=LogConversation,
        )
        for item in items
    ]
    state["log_batch_id"] = ai.submit_batch(requests)
    state["log_submitted_at"] = time.time()
    _write_state(state)
    print(f"架空ログのバッチを送りました（{len(requests)}件）。")
    return 0


def _record_usage(state: dict, stage: str, items: list) -> None:
    usage: dict[str, dict] = {}
    for item in items:
        if item.model:
            entry = usage.setdefault(item.model, {"input_tokens": 0, "output_tokens": 0})
            entry["input_tokens"] += item.input_tokens
            entry["output_tokens"] += item.output_tokens
    rows = [
        {
            "stage": stage,
            "model": model,
            **entry,
            "cost_usd": config.cost_usd(model, entry["input_tokens"], entry["output_tokens"], batch=True),
        }
        for model, entry in usage.items()
    ]
    state.setdefault("usage", []).extend(rows)
    state["spent_usd"] = state.get("spent_usd", 0.0) + sum(row["cost_usd"] for row in rows)


def collect_log(args) -> int:
    state = _read_state()
    batch_id = state["log_batch_id"]
    if not ai.wait_for_batch(batch_id, timeout_seconds=args.wait_minutes * 60):
        print("まだ終わっていません。")
        return 3
    state["log_seconds"] = time.time() - state["log_submitted_at"]
    results = ai.batch_results(batch_id, LogConversation)
    _record_usage(state, "架空ログの作成", results)
    plans = {f"log-{item.index}": item for item in plan_conversations()}

    logs, rejected = [], []
    for result in results:
        problems = [result.problem] if result.problem else plan_problems(plans[result.custom_id], result.value)
        if problems:
            rejected.append({"custom_id": result.custom_id, "problems": problems})
            continue
        logs.append({"plan": asdict(plans[result.custom_id]), "log": result.value.model_dump(mode="json")})
    logs.sort(key=lambda entry: entry["plan"]["index"])
    LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=1), encoding="utf-8")
    state["log_ok"] = len(logs)
    state["log_rejected"] = rejected
    _write_state(state)
    print(f"会話 {len(logs)} 件、入らなかった {len(rejected)} 件。")
    for item in rejected:
        print(f"- {item['custom_id']}：{item['problems']}")
    return 0


def submit_split(args) -> int:
    state = _read_state()
    logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    db.init_db(DB_PATH)
    conn = db.connect(DB_PATH)
    users = db.list_users(conn)
    units: dict[str, dict] = {}
    load_failures = []
    for entry in logs:
        item = PlanItem(**entry["plan"])
        try:
            units.update(load_log(conn, item, LogConversation.model_validate(entry["log"])))
        except (ValueError, ledger.LedgerError) as error:
            load_failures.append({"index": item.index, "reason": str(error)})

    requests = []
    for custom_id, unit in units.items():
        utterances = splitter.load_utterances(
            conn, conversation_id=unit.get("conversation_id"), letter_id=unit.get("letter_id")
        )
        letter_body = None
        if unit.get("letter_id"):
            letter_body = conn.execute(
                "SELECT body FROM letters WHERE id = ?", (unit["letter_id"],)
            ).fetchone()["body"]
        transcript = splitter.format_transcript(
            unit["place"], splitter.first_spoken_on(utterances), utterances, users, letter_body
        )
        requests.append(
            ai.batch_request(
                custom_id,
                "言明への分割",
                system=prompts.split_system("誠一さん"),
                content=prompts.split_user_text(transcript, "まだありません。"),
                schema=ai.SplitResult,
            )
        )
    conn.close()
    _guard_budget(state, _worst_case_usd("言明への分割", len(requests), 6000))
    state["split_batch_id"] = ai.submit_batch(requests)
    state["split_submitted_at"] = time.time()
    state["units"] = units
    state["load_failures"] = load_failures
    _write_state(state)
    print(f"言明への分割のバッチを送りました（{len(requests)}件）。読み込めなかった会話 {len(load_failures)} 件。")
    return 0


def collect_split(args) -> int:
    state = _read_state()
    batch_id = state["split_batch_id"]
    if not ai.wait_for_batch(batch_id, timeout_seconds=args.wait_minutes * 60):
        print("まだ終わっていません。")
        return 3
    state["split_seconds"] = time.time() - state["split_submitted_at"]
    results = ai.batch_results(batch_id, ai.SplitResult)
    _record_usage(state, "言明への分割", results)

    conn = db.connect(DB_PATH)
    users = db.list_users(conn)
    saved = interpretations = questions = 0
    dropped: dict[str, int] = {}
    examples: list[dict] = []
    failed_units = []
    for result in results:
        unit = state["units"][result.custom_id]
        if result.value is None:
            failed_units.append({"custom_id": result.custom_id, "problem": result.problem})
            continue
        db.log_ai_call(
            conn,
            role="言明への分割",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=result.custom_id,
            is_batch=True,
        )
        utterances = splitter.load_utterances(
            conn, conversation_id=unit.get("conversation_id"), letter_id=unit.get("letter_id")
        )
        report = splitter.save_split(conn, result.value, utterances, users)
        saved += len(report.statement_ids)
        interpretations += report.interpretation_count
        questions += report.question_count
        for item in report.dropped:
            dropped[item.reason] = dropped.get(item.reason, 0) + 1
            if len(examples) < 10:
                examples.append({"body": item.body, "reason": item.reason})
    conn.close()
    state["split_result"] = {
        "saved_statements": saved,
        "interpretations": interpretations,
        "questions": questions,
        "dropped": dropped,
        "dropped_examples": examples,
        "failed_units": failed_units,
    }
    _write_state(state)
    print(json.dumps(state["split_result"], ensure_ascii=False, indent=1))
    return 0


def report(_args) -> int:
    state = _read_state()
    seconds = state.get("log_seconds", 0) + state.get("split_seconds", 0)
    forecast = estimate_forecast(state.get("log_ok", 0), state.get("usage", []), seconds)
    body = {
        "conversations": state.get("log_ok", 0),
        "log_rejected": state.get("log_rejected", []),
        "load_failures": state.get("load_failures", []),
        "split_result": state.get("split_result", {}),
        "log_seconds": round(state.get("log_seconds", 0), 1),
        "split_seconds": round(state.get("split_seconds", 0), 1),
        "usage": state.get("usage", []),
        "spent_usd": round(state.get("spent_usd", 0.0), 4),
        "forecast": forecast,
    }
    REPORT_PATH.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="架空ログの小さな試し")
    commands = parser.add_subparsers(dest="command", required=True)
    log = commands.add_parser("submit-log")
    log.add_argument("--spent", type=float, default=0.0, help="今夜すでに使った費用（USD）")
    commands.add_parser("submit-split")
    for name in ("collect-log", "collect-split"):
        collect = commands.add_parser(name)
        collect.add_argument("--wait-minutes", type=float, default=8)
    commands.add_parser("report")
    args = parser.parse_args()
    handlers = {
        "submit-log": submit_log,
        "collect-log": collect_log,
        "submit-split": submit_split,
        "collect-split": collect_split,
        "report": report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
