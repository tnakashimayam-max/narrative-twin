"""10年分の本人の言葉を読み直し、心の目次を作る道具。

役割：Message Batches API で月ごとの読みを作り、人とテーマの目次に固めて、指定のデータベースへ追記する。
4つの極：3. 読み解き、積み上げる。
守る原則：5（言明は変えない）、8（架空のデータだけを扱う）、15（招かれた部屋だけを読む）。

大量の処理なので Message Batches API を使う。半額で、本人を待たせない処理のため。
自分用の置き場は断る。開発者の記録を読まないため。

使い方：python -m fiction.heart_reread データベース [--months 2036-03 2036-04] [--limit-usd 15]
関連するテスト：tests/test_heart.py
理由と実測：docs/decisions.md の「ツインとの会話エンジン」
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from app import ai, config, db, heart
from app.schemas import HeartLevel, HeartMerge, HeartMonth

_WAIT_SECONDS = 4 * 60 * 60


def _run(conn, requests: list, role: str, schema, custom_id, save, level: HeartLevel, state: dict) -> None:
    """要求をまとめて流し、結果を保存する。費用の上限を超えたら流さない。"""
    if not requests:
        return
    if state["spent"] > state["limit"]:
        raise SystemExit(f"費用の上限を超えました（${state['spent']:.2f}）。止めます。")
    batch = [ai.batch_request(custom_id(item), role, system=item.system, content=item.text, schema=schema) for item in requests]
    batch_id = ai.submit_batch(batch)
    print(f"{role}：{len(batch)}件を送りました（{batch_id}）", flush=True)
    if not ai.wait_for_batch(batch_id, timeout_seconds=_WAIT_SECONDS):
        raise SystemExit(f"バッチが終わりませんでした：{batch_id}")
    results = {item.custom_id: item for item in ai.batch_results(batch_id, schema)}
    moment = datetime.now().astimezone()
    generation = heart.next_generation(conn, level)
    saved = 0
    for item in requests:
        result = results.get(custom_id(item))
        if result is None or result.value is None:
            state["failed"].append((custom_id(item), result.problem if result else "結果がありません"))
            continue
        state["spent"] += config.cost_usd(result.model, result.input_tokens, result.output_tokens, batch=True)
        db.log_ai_call(
            conn,
            role=role,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            target=custom_id(item),
            is_batch=True,
        )
        saved += save(
            conn,
            item,
            result.value,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            generation=generation,
            moment=moment,
        )
    conn.commit()
    print(f"{role}：{saved}行を保存。ここまで ${state['spent']:.3f}", flush=True)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本人の言葉を読み直し、心の目次を作る。")
    parser.add_argument("database", type=Path)
    parser.add_argument("--months", nargs="*", help="読み直す月。省くとすべて")
    parser.add_argument("--limit-usd", type=float, default=15.0)
    args = parser.parse_args(argv)
    if "mine" in {part.lower() for part in args.database.resolve().parts}:
        print("自分用の記録は扱いません。", file=sys.stderr)
        return 1
    conn = db.connect(args.database)
    try:
        db.init_db(args.database, seed_demo=False)
        state = {"spent": 0.0, "limit": args.limit_usd, "failed": []}
        months = args.months or heart.months_to_read(conn)
        requests = [item for item in (heart.month_request(conn, period) for period in months) if item]
        _run(conn, requests, heart.ROLE_MONTH, HeartMonth, lambda item: f"m-{item.period}", heart.save_month, HeartLevel.MONTH, state)
        _run(conn, heart.merge_requests(conn), heart.ROLE_MERGE, HeartMerge, lambda item: item.key, heart.save_merge, HeartLevel.INDEX, state)
        print(f"合計 ${state['spent']:.3f}。失敗 {state['failed']}")
        return 1 if state["failed"] else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
