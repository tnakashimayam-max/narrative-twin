"""デモの段階ごとに、架空ログを読み込む。

役割：2026年11月3日より前を最初の起動で、それより後を「10年後へ進む」で読み込む。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：8（人物と記録はすべて架空）、13（色は本人が確かめた分だけ）。

デモの日より前は確かめの行を入れない。ツインが鉛筆の下書きの姿から始まり、本人が確かめる場面を見せるため。
「10年後へ進む」のときに、その日までの分を物語の日付で確かめた行にする。その場で本人が確かめた言葉は、そのまま残す。

関連するテスト：tests/test_demo.py
"""

from __future__ import annotations

from datetime import date, datetime

from app import ledger
from fiction import load, plan

FUTURE_DAY = plan.FUTURE_DAY
_PROLOGUE = "prologue"
_FUTURE = "future"


def is_loaded(conn, stage: str) -> bool:
    return load.is_loaded(conn, stage)


def story_is_ready() -> bool:
    """10年分のログと固める処理の結果がそろっているかを返す。"""
    logs = load.read_logs()
    return bool(logs) and all(item.key in logs for item in plan.build_plan()) and bool(load.read_models())


def _prologue_items() -> list[plan.PlanItem]:
    return [item for item in plan.build_plan() if item.day < plan.DEMO_DAY]


def _future_items() -> list[plan.PlanItem]:
    return [item for item in plan.build_plan() if item.day > plan.DEMO_DAY]


def _split_runs(runs: list[dict]) -> tuple[list[dict], list[dict]]:
    """デモの日より前に閉じた月の結果と、それ以外に分ける。"""
    before, after = [], []
    for run in runs:
        (before if date.fromisoformat(run["end"]) < plan.DEMO_DAY else after).append(run)
    return before, after


def load_prologue(conn) -> bool:
    """最初の起動のときに、デモの日より前を読み込む。すでに入っていれば何もしない。"""
    if is_loaded(conn, _PROLOGUE):
        return False
    with ledger.atomic(conn):
        load.replay(conn, _prologue_items(), confirm=False)
        before, _ = _split_runs(load.read_models())
        load.load_model_runs(conn, before)
        load.mark_loaded(conn, _PROLOGUE, datetime.now().astimezone())
    return True


def _confirm_prologue(conn) -> None:
    """デモの日より前の話のうち、会話で「合っています」と答えた分を、物語の日付で確かめた行にする。"""
    logs = load.read_logs()
    self_user = load.db.self_user(conn)
    agreed = {
        item.key: item.day
        for item in _prologue_items()
        if logs.get(item.key, {}).get("confirmation") == "合っています"
    }
    rows = conn.execute("SELECT key, statement_id FROM story_keys WHERE key LIKE 'conv-%'").fetchall()
    for row in rows:
        day_key = row["key"].removeprefix("conv-").split("#")[0]
        if day_key in agreed:
            load._confirm(conn, row["statement_id"], agreed[day_key], self_user)


def load_future(conn, now: datetime) -> None:
    """デモの日より後を読み込む。全部入るか、全部入らないかにする。"""
    with ledger.atomic(conn):
        _confirm_prologue(conn)
        load.replay(conn, _future_items())
        _, after = _split_runs(load.read_models())
        load.load_model_runs(conn, after)
        load.load_observations(conn)
        load.mark_loaded(conn, _FUTURE, now)
