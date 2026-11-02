"""言明への分割を、Haiku と Sonnet で比べる。

役割：eval/smoke_conversations.json の架空の会話を、2つのモデルで言明に分け、結果を並べる。
4つの極：3. 読み解き、積み上げる。
守る原則：8（送るのは架空の内容だけ）。

本物のデータベースには触れない。結果は data/trial/ に置き、Git には入れない。
比べた結果は docs/tools.md にある。
使い方：.venv\\Scripts\\python -m scripts.compare_split
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from app import config, db, intake, ledger
from scripts.try_split import CASES, TRIAL_DIR, load_case

MODELS = {"Haiku 5.5": "claude-haiku-5-5", "Sonnet 5.5": "claude-sonnet-5-5"}
ROLE = "言明への分割"


def _run(label: str, model: str, stamp: str) -> dict:
    """1つのモデルで3つの会話を分け、結果を返す。"""
    config.MODEL_BY_ROLE[ROLE] = model
    db_path = TRIAL_DIR / f"compare_{stamp}_{model}.db"
    db.init_db(db_path)
    conn = db.connect(db_path)
    users = {user.name: user for user in db.list_users(conn)}
    names = {user.id: user.name for user in users.values()}
    cases = []
    for case in json.loads(CASES.read_text(encoding="utf-8"))["conversations"]:
        target, place, utterances = load_case(conn, case, users)
        started = time.monotonic()
        report = intake.split_and_save(
            conn, target=target, place=place, utterances=utterances, letter_body=case.get("letter")
        )
        seconds = round(time.monotonic() - started, 1)
        statements = []
        for statement_id in report.statement_ids:
            item = ledger.get_statement(conn, statement_id)
            interpretations = conn.execute(
                "SELECT perspective, viewpoint_role, confidence, caution, body "
                "FROM interpretations WHERE statement_id = ?",
                (statement_id,),
            ).fetchall()
            statements.append(
                {
                    "source": item.source.value,
                    "speaker": "ツイン" if item.speaker_is_twin else names[item.speaker_user_id],
                    "subject": names[item.subject_user_id],
                    "period": item.period.value,
                    "theme": item.theme.value,
                    "petals": [petal.value for petal in item.petals],
                    "body": item.body,
                    "interpretations": [dict(row) for row in interpretations],
                }
            )
        cases.append(
            {
                "name": case["name"],
                "seconds": seconds,
                "statements": statements,
                "dropped": [{"body": d.body, "reason": d.reason} for d in report.dropped],
                "questions": report.question_count,
                "dropped_questions": report.dropped_question_count,
            }
        )
    calls = conn.execute(
        "SELECT model, input_tokens, output_tokens FROM ai_calls WHERE role = ?", (ROLE,)
    ).fetchall()
    input_tokens = sum(row["input_tokens"] for row in calls)
    output_tokens = sum(row["output_tokens"] for row in calls)
    cost = sum(config.cost_usd(row["model"], row["input_tokens"], row["output_tokens"]) for row in calls)
    conn.close()
    return {
        "label": label,
        "model": model,
        "calls": len(calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 4),
        "seconds": round(sum(case["seconds"] for case in cases), 1),
        "cases": cases,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # 引数を受け付けるようにして、--help を付けても本物の API を呼ばないようにする
    argparse.ArgumentParser(description="言明への分割を Haiku と Sonnet で比べる").parse_args()
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = [_run(label, model, stamp) for label, model in MODELS.items()]
    path = TRIAL_DIR / f"compare_{stamp}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(
            f"\n##### {result['label']}：呼び出し{result['calls']}回、入力{result['input_tokens']}／"
            f"出力{result['output_tokens']}、${result['cost_usd']}、{result['seconds']}秒"
        )
        for case in result["cases"]:
            print(f"\n=== {case['name']}（{case['seconds']}秒、入らなかった {len(case['dropped'])}、問い {case['questions']}）")
            for item in case["statements"]:
                print(f"- [{item['source']}] {item['speaker']}→{item['subject']}／{item['period']}／{item['theme']}／{item['petals']}")
                print(f"  {item['body']}")
                for row in item["interpretations"]:
                    print(f"    ・{row['perspective']}（{row['viewpoint_role']}、{row['confidence']}）：{row['body']}")
            for dropped in case["dropped"]:
                print(f"- 入らなかった：{dropped['body']} … {dropped['reason']}")
    print(f"\n結果：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
