"""10年分を古い順に再生して、セルフの盤面を育てる道具。

役割：段1b で本人のいる部屋を読み、月ごとに段2、段3、年ごとに段5、段6 を流す。費用の上限で止まる。途中から続けられる。
4つの極：3. 読み解き、積み上げる。
守る原則：5（言明と解釈の表は変えない。足すだけ）、8（架空データだけ。実データは開かない）、15（本人のいない部屋を読まない）。

流すのは控えの写し。人だけの部屋を読むと相手の言葉も API に送られ、取り消せないので、自分用の置き場は断る。

使い方：python -m fiction.selfhood_replay データベース --run 名前 [--from 2026-10] [--to 2036-05] [--budget 30] [--state 状態のファイル]
関連するテスト：tests/test_selfhood.py
理由と実測：docs/decisions.md の「セルフ生成エンジン」、結果は docs/verification/selfhood/
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from pathlib import Path

from app import config, db, selfhood, selfhood_stages
from app.selfhood_stages import BudgetExceeded, Spend
from app.schemas import PieceStatus

_START_DATE = "2026-01-01"


def months_between(first: str, last: str) -> list[str]:
    """月の一覧を返す。"""
    year, month = (int(part) for part in first.split("-"))
    end_year, end_month = (int(part) for part in last.split("-"))
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def last_day_of(period: str, stop: date | None = None) -> str:
    year, month = (int(part) for part in period.split("-"))
    day = date(year, month, calendar.monthrange(year, month)[1])
    return (stop if stop and stop < day else day).isoformat()


def setup_board(conn, run: str) -> int:
    """最初の盤面を置く。すでにあれば何もしない。"""
    if conn.execute("SELECT 1 FROM selfhood_pieces WHERE run = ? LIMIT 1", (run,)).fetchone():
        return 0
    pieces = selfhood.starting_pieces(conn)
    for piece in pieces:
        selfhood.add_piece_version(
            conn,
            run=run,
            key=piece["key"],
            section=piece["section"],
            name=piece["name"],
            person=piece["person"],
            hypotheses=[],
            standing=selfhood.Standing(PieceStatus.EMPTY, 0.5, 0.0, 0.0),
            replay_date=_START_DATE,
        )
    conn.commit()
    return len(pieces)


def replay(conn, run: str, first: str, last: str, spend: Spend, state_path: Path, *, stop: date | None = None, client=None) -> dict:
    """月順に流す。終わった月を状態のファイルに残し、止まっても続きから流せるようにする。"""
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"done": [], "log": []}
    setup_board(conn, run)
    months = months_between(first, last)

    def log(line: dict) -> None:
        state["log"].append(line)
        print(json.dumps(line, ensure_ascii=False), flush=True)

    for period in months:
        if period in state["done"]:
            continue
        last_day = last_day_of(period, stop)
        selfhood_stages.replay_month(conn, run, period, last_day, spend, log, client)
        if period.endswith("-12") or period == months[-1]:
            selfhood_stages.illuminate(conn, run, last_day, spend, client)
            selfhood_stages.write_selfhood(conn, run, last_day, spend, client)
        state["done"].append(period)
        state["by_stage"] = {key: round(value, 4) for key, value in spend.by_stage.items()}
        state["failures"] = spend.failures
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    selfhood_stages.write_questions(conn, run, last_day_of(months[-1], stop), spend, client)
    state["by_stage"] = {key: round(value, 4) for key, value in spend.by_stage.items()}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return state


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="10年分を再生して、セルフの盤面を育てる。")
    parser.add_argument("database", type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--from", dest="first", default="2026-10")
    parser.add_argument("--to", dest="last", default="2036-05")
    parser.add_argument("--stop", help="この日までで止める。2036-05-05 のように書く")
    parser.add_argument("--budget", type=float, default=config.SELFHOOD_BUDGET_USD)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--dialogues-only", action="store_true", help="段1b だけを流す")
    parser.add_argument("--rewrite", nargs="*", help="再生せず、指定の日付のセルフだけを書き直す。チューニングで使う")
    args = parser.parse_args(argv)
    try:
        selfhood.refuse_real_records(args.database)
    except selfhood.RealRecordsRefused as error:
        print(str(error), file=sys.stderr)
        return 1
    db.init_db(args.database, seed_demo=False)
    conn = db.connect(args.database)
    spend = Spend(args.budget)
    try:
        if args.rewrite:
            for day in args.rewrite:
                selfhood_stages.write_selfhood(conn, args.run, day, spend)
                print(f"{day} を書き直しました。${spend.total:.3f}", flush=True)
            return 0
        saved = selfhood_stages.read_dialogues(conn, spend)
        print(f"段1b：{saved}件を読みました。${spend.total:.3f}", flush=True)
        if args.dialogues_only:
            print(json.dumps({key: round(value, 4) for key, value in spend.by_stage.items()}, ensure_ascii=False))
            return 0
        state_path = args.state or args.database.with_name(f"selfhood_{args.run}.json")
        stop = date.fromisoformat(args.stop) if args.stop else None
        state = replay(conn, args.run, args.first, args.last, spend, state_path, stop=stop)
        print(f"終わり。${spend.total:.3f}。段ごと：{state.get('by_stage')}。失敗：{spend.failures}")
        return 0
    except BudgetExceeded as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
