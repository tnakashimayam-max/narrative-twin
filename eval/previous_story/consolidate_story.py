"""10年分の架空ログを固める。

役割：作業用のデータベースに架空ログを入れ、月、年、人生の段階の順にバッチで固めて、eval/previous_story/story/model_YYYY.json に残す。
4つの極：3. 読み解き、積み上げる。
守る原則：8（人物と記録はすべて架空）。

使っている Claude API の機能：Message Batches API。月は Sonnet 5.5、年と人生の段階は Opus 5.5（app/config.py）。
年ごとに順に流すのは、前の年までの要素を「今の要素」として渡し、変化を見つけられるようにするため。
結果は鍵で言明を指す。番号は乱数で、作り直すたびに変わるため。途中で止まっても、終わった年から続けられる。

使い方：python -m eval.previous_story.consolidate_story
関連するテスト：tests/test_fiction.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from app import ai, config, consolidate, db, ledger
from app.schemas import ConsolidationResult, ConsolidationUnit, LifeStage
from eval.previous_story import load, plan

ROOT = Path(__file__).resolve().parents[2]
BUILD_DB = ROOT / "data" / "trial" / "story_model.db"
STATE_PATH = ROOT / "data" / "trial" / "story_model_state.json"
# 固める処理の分の上限。全体の上限（50ドル）の中で、確かめの分を残す。
LIMIT_USD = 15.0
# 人生の段階を固める年。その段階の最後の年の処理のあとで流す
STAGE_ENDS = {2030: LifeStage.ACTIVE, 2033: LifeStage.SUPPORT, 2036: LifeStage.CARE}
_UNIT_MARKS = {ConsolidationUnit.MONTH: "month", ConsolidationUnit.YEAR: "year", ConsolidationUnit.LIFE_STAGE: "stage"}


def _state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"spent_usd": 0.0, "runs": []}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def run_id(request: consolidate.Request) -> str:
    """バッチの番号。使えるのは英数字とハイフンと下線だけなので、単位を英字にする。"""
    return f"{_UNIT_MARKS[request.unit]}-{request.start.isoformat()}"


def _export(conn, request: consolidate.Request, saved, result, model, input_tokens, output_tokens) -> dict:
    """保存した要素を、鍵で言明を指す形にする。"""
    statement_keys = load.key_of_statement(conn)
    element_keys = load.key_of_element(conn)
    identifier = run_id(request)
    elements = []
    for index, element in enumerate(saved):
        load.remember_element(conn, f"{identifier}#{index}", element.id)
        elements.append(
            {
                "kind": element.kind.value,
                "body": element.body,
                "condition": element.condition,
                "petals": [petal.value for petal in element.petals],
                "confidence": element.confidence,
                "evidence_keys": [statement_keys[number] for number in element.evidence_statement_ids if number in statement_keys],
                "previous_key": element_keys.get(element.previous_version_id),
            }
        )
    return {
        "id": identifier,
        "unit": request.unit.value,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_keys": [statement_keys[number] for number in request.input_ids(conn) if number in statement_keys],
        "elements": elements,
    }


def _run_batch(conn, requests: list[consolidate.Request], state: dict, wait_minutes: float) -> list[dict]:
    """要求をまとめてバッチで流し、結果を保存して、残す形で返す。"""
    if not requests:
        return []
    spent = state["spent_usd"]
    if spent > LIMIT_USD:
        raise SystemExit(f"費用の上限を超えました（${spent:.2f}）。止めます。")
    batch = [
        ai.batch_request(run_id(item), item.role, system=item.system, content=item.text, schema=ConsolidationResult)
        for item in requests
    ]
    started = time.monotonic()
    batch_id = ai.submit_batch(batch)
    if not ai.wait_for_batch(batch_id, timeout_seconds=wait_minutes * 60):
        raise SystemExit(f"バッチが終わりませんでした：{batch_id}")
    results = {item.custom_id: item for item in ai.batch_results(batch_id, ConsolidationResult)}
    seconds = time.monotonic() - started
    exported = []
    now = datetime.now().astimezone()
    for request in requests:
        result = results[run_id(request)]
        if result.model:
            cost = config.cost_usd(result.model, result.input_tokens, result.output_tokens, batch=True)
            state["spent_usd"] = round(state["spent_usd"] + cost, 4)
            db.log_ai_call(
                conn,
                role=request.role,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                target=run_id(request),
                is_batch=True,
            )
        if result.value is None:
            state.setdefault("failed", []).append({"id": run_id(request), "problem": result.problem})
            print(f"失敗 {run_id(request)}：{result.problem}")
            continue
        saved = consolidate.save_result(
            conn,
            request,
            result.value,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            now=now,
        )
        exported.append(_export(conn, request, saved, result, result.model, result.input_tokens, result.output_tokens))
    conn.commit()
    state["runs"].append({"batch": batch_id, "count": len(requests), "seconds": round(seconds, 1)})
    _save_state(state)
    print(f"{requests[0].unit.value} {len(requests)}件 {seconds:.0f}秒 合計 ${state['spent_usd']:.2f}")
    return exported


def build_database() -> object:
    """作業用のデータベースを作り直し、架空ログと、終わった年の結果を入れる。"""
    BUILD_DB.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        BUILD_DB.with_name(BUILD_DB.name + suffix).unlink(missing_ok=True)
    db.init_db(BUILD_DB)
    conn = db.connect(BUILD_DB)
    load.replay(conn, plan.build_plan())
    load.load_model_runs(conn, load.read_models())
    return conn


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="10年分の架空ログを固めます。")
    parser.add_argument("--wait-minutes", type=float, default=60)
    parser.add_argument("--first-year", type=int, default=2026)
    parser.add_argument("--last-year", type=int, default=2036)
    args = parser.parse_args(argv)
    state = _state()
    started = time.monotonic()
    conn = build_database()
    print(f"読み込み {time.monotonic() - started:.1f}秒")
    done_years = {int(path.stem.removeprefix("model_")) for path in load.STORY_DIR.glob("model_*.json")}
    try:
        for year in range(args.first_year, args.last_year + 1):
            if year in done_years:
                continue
            requests = [consolidate.build_month(conn, year, month) for month in range(1, 13)]
            months = [request for request in requests if request is not None]
            runs = _run_batch(conn, months, state, args.wait_minutes)
            upper = consolidate.build_year(conn, year)
            runs += _run_batch(conn, [upper] if upper else [], state, args.wait_minutes)
            if year in STAGE_ENDS:
                stage = consolidate.build_stage(conn, STAGE_ENDS[year])
                runs += _run_batch(conn, [stage] if stage else [], state, args.wait_minutes)
            load.write_models(year, runs)
    finally:
        conn.close()
    print(f"終わりました。合計 ${state['spent_usd']:.2f}、{time.monotonic() - started:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
