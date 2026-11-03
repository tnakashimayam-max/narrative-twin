"""試しの40回と10年分で、1回の問いに読む量を比べる。

役割：同じ問いを、40回分の記録と10年分の記録で田中さんとして聞き、読んだ字数とトークン数を並べる。
4つの極：4. 10年後に代弁する。
守る原則：8（架空のデータだけを使う）。記録が増えても読む量を増やさないことを、実測で確かめる。

一時のデータベースを作って測り、本物の data/narrative_twin.db には触れない。
本物の API を呼ぶ（1回あたり数セント）。自分用の記録は読まない。結果は docs/tools.md に写す。

使い方：.venv\\Scripts\\python -m scripts.measure_reading
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db, ledger, model_store
from app.main import create_app
from app.schemas import Theme
from fiction import load, plan

QUESTIONS = (
    "誠一さんはどこで暮らしたいと考えていましたか？",
    "ふだんの暮らしで大切にしてきたことは何ですか？",
    "泊まりを使うことを、どう考えていましたか？",
)
FIRST_CONVERSATIONS = 40


def _build(folder: Path, items: list[plan.PlanItem], runs: list[dict]) -> tuple:
    """一時のデータベースに記録を入れ、田中さんを招く。"""
    deployment = config.deployment(data_dir=folder)
    app = create_app(deployment=deployment)
    connection = db.connect(deployment.db_path)
    people = {user.name: user for user in db.list_users(connection)}
    me = TestClient(app, headers={"X-NT-User": str(people["誠一さん"].id)})
    themes = [theme.value for theme in Theme if theme is not Theme.HARDSHIP]
    me.put("/api/handover", json={"themes": themes, "delegate_ids": [people["美穂さん"].id]}).raise_for_status()
    load.replay(connection, items)
    load.load_model_runs(connection, runs)
    load.load_observations(connection)
    app.state.day_offset = timedelta(days=(plan.FUTURE_DAY - date.today()).days)
    daughter = TestClient(app, headers={"X-NT-User": str(people["美穂さん"].id)})
    invited = daughter.post("/api/people/professional", json={"name": "田中さん", "relation": "ケアマネジャー"})
    invited.raise_for_status()
    return app, connection, TestClient(app, headers={"X-NT-User": str(invited.json()["id"])})


def _count(connection, name: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]


def measure(label: str, folder: Path, items: list[plan.PlanItem], runs: list[dict]) -> dict:
    """1つの大きさの記録で測る。"""
    _, connection, tanaka = _build(folder, items, runs)
    try:
        heads = ledger.head_map(connection)
        tanaka_user = next(user for user in db.list_users(connection) if user.name == "田中さん")
        visible = model_store.visible_elements(connection, tanaka_user)
        reading = model_store.for_reading(visible, heads)
        lines = [model_store.describe(element, number, heads) for number, element in enumerate(reading, start=1)]
        bodies = [row["body"] for row in connection.execute("SELECT body FROM statements")]
        answers = []
        for question in QUESTIONS:
            before = _count(connection, "ai_calls")
            response = tanaka.post("/api/advocacy/ask", json={"question": question, "client_key": uuid.uuid4().hex})
            response.raise_for_status()
            turn = response.json()["turn"]
            calls = connection.execute(
                """
                SELECT a.role, a.input_tokens, a.output_tokens, a.seconds,
                       COALESCE(c.cache_read_tokens, 0) AS cache_read, COALESCE(c.cache_write_tokens, 0) AS cache_write
                FROM ai_calls a LEFT JOIN ai_call_cache c ON c.ai_call_id = a.id
                ORDER BY a.rowid LIMIT -1 OFFSET ?
                """,
                (before,),
            ).fetchall()
            answers.append(
                {
                    "question": question,
                    "self_words": len(turn["self_words"]),
                    "not_yet": turn["not_yet"],
                    "calls": [dict(call) for call in calls],
                }
            )
        return {
            "label": label,
            "conversations": _count(connection, "conversations"),
            "utterances": _count(connection, "utterances"),
            "statements": _count(connection, "statements"),
            "statement_chars": sum(len(body) for body in bodies),
            "elements_active": len(model_store.active_elements(connection)),
            "elements_visible": len(visible),
            "elements_read": len(reading),
            "model_chars": sum(len(line) for line in lines),
            "answers": answers,
        }
    finally:
        connection.close()


def as_markdown(results: list[dict]) -> str:
    lines = [
        "| 記録 | 会話 | 発言 | 言明の行 | 言明の字数 | 有効な要素 | 読む要素 | 読む字数 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['label']} | {item['conversations']} | {item['utterances']} | {item['statements']} | "
            f"{item['statement_chars']:,} | {item['elements_active']} | {item['elements_read']} | {item['model_chars']:,} |"
        )
    lines += [
        "",
        "| 記録 | 問い | 役割 | 入力 | キャッシュ読み | キャッシュ書き | 出力 | 秒 | 本人の言葉 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        for answer in item["answers"]:
            for call in answer["calls"]:
                seconds = f"{call['seconds']:.1f}" if call["seconds"] is not None else "—"
                lines.append(
                    f"| {item['label']} | {answer['question']} | {call['role']} | {call['input_tokens']:,} | "
                    f"{call['cache_read']:,} | {call['cache_write']:,} | {call['output_tokens']:,} | {seconds} | "
                    f"{answer['self_words']}件 |"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="1回の問いに読む量を比べます。")
    parser.add_argument("--json", type=Path, help="結果を JSON でも書き出す先")
    args = parser.parse_args(argv)
    items = plan.build_plan()
    logs = load.read_logs()
    items = [item for item in items if item.key in logs]
    runs = load.read_models()
    last_day = items[FIRST_CONVERSATIONS - 1].day
    first_runs = [run for run in runs if date.fromisoformat(run["end"]) <= date(last_day.year, 12, 31)]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as small, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as large:
        results = [
            measure(f"試しの{FIRST_CONVERSATIONS}回", Path(small), items[:FIRST_CONVERSATIONS], first_runs),
            measure("10年分", Path(large), items, runs),
        ]
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(as_markdown(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
