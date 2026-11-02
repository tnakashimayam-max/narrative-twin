"""言明に分ける処理を、本物の Claude API で確かめる。

役割：eval/smoke_conversations.json の架空の会話を一時のデータベースに入れ、言明に分けて結果を出す。
4つの極：3. 読み解き、積み上げる。
守る原則：8（送るのは架空の内容だけ）。

本物のデータベースには触れない。結果は data/trial/ に置き、Git には入れない。
使い方：.venv\\Scripts\\python -m scripts.try_split
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from app import config, db, intake, ledger
from app.schemas import (
    CommentDraft,
    ConversationDraft,
    LetterDraft,
    LifeStage,
    Place,
    ShareScope,
    UtteranceDraft,
)

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "smoke_conversations.json"
TRIAL_DIR = ROOT / "data" / "trial"


def load_case(conn, case: dict, users: dict) -> tuple[str, str, list]:
    """1つの会話かスレッドを、台帳の関数を通して入れる。"""
    day = date.fromisoformat(case["date"])
    if case["kind"] == "スレッド":
        letter = ledger.add_letter(
            conn,
            LetterDraft(
                body=case["letter"],
                share_with=ShareScope(case["share_with"]),
                shared_at=datetime(day.year, day.month, day.day, 9, 0),
            ),
        )
        for index, turn in enumerate(case["turns"]):
            ledger.add_comment(
                conn,
                CommentDraft(
                    letter_id=letter.id,
                    author_user_id=users[turn["speaker"]].id,
                    body=turn["text"],
                    commented_at=datetime(day.year, day.month, day.day, 10, index),
                ),
            )
        rows = conn.execute(
            "SELECT * FROM utterances WHERE letter_id = ? ORDER BY spoken_at, rowid", (letter.id,)
        ).fetchall()
        return f"便り {letter.id}", "スレッド", [db.row_to_model(row, ledger.Utterance) for row in rows]

    conversation = ledger.add_conversation(
        conn,
        ConversationDraft(conversation_date=day, trigger=case["trigger"], life_stage=LifeStage.ACTIVE),
    )
    utterances = []
    for index, turn in enumerate(case["turns"]):
        is_twin = turn["speaker"] == "ツイン"
        utterances.append(
            ledger.add_utterance(
                conn,
                UtteranceDraft(
                    conversation_id=conversation.id,
                    speaker_user_id=None if is_twin else users[turn["speaker"]].id,
                    speaker_is_twin=is_twin,
                    text=turn["text"],
                    spoken_at=datetime(day.year, day.month, day.day, 10, index),
                    place=Place.DIARY_AND_TALK,
                ),
            )
        )
    return f"会話 {conversation.id}", "日記と会話", utterances


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # 引数を受け付けるようにして、--help を付けても本物の API を呼ばないようにする
    argparse.ArgumentParser(description="架空の会話を本物の Claude API で言明に分ける").parse_args()
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_path = TRIAL_DIR / f"smoke_{stamp}.db"
    db.init_db(db_path)
    conn = db.connect(db_path)
    users = {user.name: user for user in db.list_users(conn)}
    names = {user.id: user.name for user in users.values()}

    summary = []
    for case in json.loads(CASES.read_text(encoding="utf-8"))["conversations"]:
        target, place, utterances = load_case(conn, case, users)
        started = time.monotonic()
        report = intake.split_and_save(
            conn, target=target, place=place, utterances=utterances, letter_body=case.get("letter")
        )
        seconds = round(time.monotonic() - started, 1)
        print(f"\n=== {case['name']}（{seconds}秒）===")
        rows = []
        for statement_id in report.statement_ids:
            item = ledger.get_statement(conn, statement_id)
            speaker = "ツイン" if item.speaker_is_twin else names[item.speaker_user_id]
            print(
                f"- [{item.source.value}] 話した人：{speaker}／誰について：{names[item.subject_user_id]}"
                f"／{item.period.value}／{item.theme.value}／{[p.value for p in item.petals]}\n  {item.body}"
            )
            interpretations = conn.execute(
                "SELECT perspective, confidence, caution, body FROM interpretations WHERE statement_id = ?",
                (statement_id,),
            ).fetchall()
            for row in interpretations:
                note = f"（注意：{row['caution']}）" if row["caution"] else ""
                print(f"    ・{row['perspective']} {row['confidence']}：{row['body']}{note}")
            rows.append({"source": item.source.value, "body": item.body})
        for dropped in report.dropped:
            print(f"- 入らなかった：{dropped.body} … {dropped.reason}")
        summary.append(
            {
                "name": case["name"],
                "seconds": seconds,
                "statements": rows,
                "dropped": [{"body": d.body, "reason": d.reason} for d in report.dropped],
                "questions": report.question_count,
            }
        )

    print("\n=== 確かめる問いの候補 ===")
    for row in conn.execute("SELECT question FROM question_logs").fetchall():
        print(f"- {row['question']}")

    total_cost = 0.0
    print("\n=== 使ったモデルとトークン数 ===")
    for row in conn.execute("SELECT * FROM ai_calls").fetchall():
        cost = config.cost_usd(row["model"], row["input_tokens"], row["output_tokens"])
        total_cost += cost
        print(f"{row['target']}：{row['model']} 入力{row['input_tokens']}／出力{row['output_tokens']} ${cost:.4f}")
    print(f"合計 ${total_cost:.4f}")

    (TRIAL_DIR / f"smoke_{stamp}.json").write_text(
        json.dumps({"cases": summary, "cost_usd": total_cost}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
