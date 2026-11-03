"""今の方式と、BM25 で探す簡単な RAG を、同じ問いで比べる。

役割：田中さん（2036年に初めて担当するケアマネジャー）の問いに、どの言葉が届くかを、本物の API で並べる。
4つの極：4. 10年後に代弁する（比べるためだけの道具）。
守る原則：8（架空のデータだけを使う）、9（届ける本文はどの腕も記録の本文）、10（探す先は同じ同意の絞り込みのあと）。

変えるのは「本人の言葉の候補の作り方」だけ。証言と推測の候補、言葉を選ぶ係のプロンプトとモデル、番号を本文に戻す処理、
見える範囲の絞り込みは、すべての腕で同じにする。推定意思は呼ばない。届いた言葉の差だけを見るため。

腕：
- 今の方式：意思モデルの要素を選び、その根拠を候補にする（app/advocacy.py の answer と同じ）
- BM25：問いに語が近い本人の言葉の上位30件を候補にする
- BM25（注意文あり）：同じ候補を「関係はありそうな記録」の節に置き、「問いの事柄そのものが本文に出てくる言葉だけを選ぶ」注意文を出す。
  今の方式は直接答える要素がないとこの注意文が出るので、BM25 でも出したときの差を見る
- 今の方式＋BM25：今の方式の候補の後ろを削り、BM25 の上位を入れる。本人の言葉の候補の合計は同じ30件
- BM25上位8件（選ぶ係なし）：AI を呼ばず、上位8件をそのまま届ける。選ぶ係の働きを見るため

一時のデータベースに10年分を入れて測り、本物の data/narrative_twin.db には触れない。自分用の記録は読まない。
問いと正解は eval/rag_compare_questions.json に固定してから流す。結果は docs/tools.md の「BM25 の RAG との比べ」に書く。

使い方：
  python -m scripts.compare_rag sample    本人の言葉を層ごとに抜く（AI を呼ばない）
  python -m scripts.compare_rag gold      正解を付ける（本人の言葉を全部読む）
  python -m scripts.compare_rag run       腕を流す。--rep 1 か 2
  python -m scripts.compare_rag judge     届いた言葉を目隠しで判定する
  python -m scripts.compare_rag repeat    同じ記録で今の方式を2回流し、揺れを測る
  python -m scripts.compare_rag probe     定型句の影響と、入れ直しによる要素の並びの違いを測る（AI を呼ばない）
  python -m scripts.compare_rag report    表を書き出す（AI を呼ばない）

関連するテスト：tests/test_compare_rag.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from app import advocacy, ai, config, consent, db, ledger, model_store, prompts
from app.advocacy import WordPool
from app.schemas import ConfirmStatus, ElementKind, ModelElement, Statement, User
from fiction import load, plan
from scripts.bm25 import BM25
from scripts.measure_reading import _build

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "rag_compare_questions.json"
RESULTS = ROOT / "docs" / "verification" / "rag_compare.json"
WORK = ROOT / "data" / "trial" / "compare_rag"

ARM_MODEL = "今の方式"
ARM_BM25 = "BM25"
ARM_BM25_CAUTION = "BM25（注意文あり）"
ARM_HYBRID = "今の方式＋BM25"
ARM_BM25_TOP = "BM25上位8件（選ぶ係なし）"
ARMS = (ARM_MODEL, ARM_BM25, ARM_BM25_CAUTION, ARM_HYBRID, ARM_BM25_TOP)

SEED = 20261103
SAMPLE_PER_STRATUM = 20
# 短い言葉は前の話がないと意味が分からず、問いを作れないので、層から抜くときだけ除く
MIN_SAMPLE_CHARS = 15
# 今の方式＋BM25 で BM25 に空ける枠。多くすると今の方式の根拠が落ち、少ないと穴に届かない。比べる前に決めた値
HYBRID_RESERVE = 10
TOP_WITHOUT_PICKER = 8
SELF_LIMIT = advocacy.LIMITS["word_pool_self"]
QUOTE_LIMIT = advocacy.LIMITS["quotes_total"]
WORKERS = 5
# 同じ記録で2回流す問いの数。揺れの割合を見るだけなので、全部は流さない
REPEAT_QUESTIONS = 30
# 比べ全体で使ってよい費用（ドル）。これを超えそうなら止める
BUDGET_USD = 16.0


class GoldPick(BaseModel):
    """正解付けの答え。番号だけを受け取る。"""

    numbers: list[int] = Field(default_factory=list)


class JudgeItem(BaseModel):
    label: str
    answers: str = Field(description="分かる、一部、分からない のどれか")
    swapped: bool = Field(description="聞いた事柄ではなく、近い話題の言葉にすり替わっているか")


class JudgeResult(BaseModel):
    items: list[JudgeItem]


# ---- 候補を作る（AI を呼ばない。テストはここを確かめる） ----


@dataclass
class World:
    """1回の測定で使う、10年分の記録と田中さんの見える範囲。"""

    conn: sqlite3.Connection
    viewer: User
    self_name: str
    heads: dict[int, Statement]
    scopes: frozenset
    reading: list[ModelElement]
    model_text: str
    self_statements: list[Statement]
    index: BM25


def visible_self_words(conn: sqlite3.Connection, viewer: User) -> list[Statement]:
    """この人に見える本人の言葉。今の方式と同じ同意の絞り込みのあとから作る（原則10）。お休みの言葉は入れない（原則6）。"""
    return [
        item
        for item in consent.visible_statements(conn, viewer)
        if item.source in advocacy._SELF_SOURCES and item.confirm_status is not ConfirmStatus.RESTING
    ]


def open_world(conn: sqlite3.Connection, viewer: User) -> World:
    self_user = db.self_user(conn)
    self_name = self_user.display_name if self_user else "本人"
    heads = ledger.head_map(conn)
    reading = model_store.for_reading(model_store.visible_elements(conn, viewer), heads)
    lines = [model_store.describe(element, number, heads) for number, element in enumerate(reading, start=1)]
    statements = visible_self_words(conn, viewer)
    return World(
        conn=conn,
        viewer=viewer,
        self_name=self_name,
        heads=heads,
        scopes=consent.visible_scopes(conn, viewer),
        reading=reading,
        model_text=prompts.twin_answer_model_text(lines),
        self_statements=statements,
        index=BM25([item.body for item in statements]),
    )


def bm25_hits(world: World, question: str, limit: int, exclude: set[int] | None = None) -> list[Statement]:
    """問いに語が近い本人の言葉を、点数の高い順に返す。"""
    exclude = exclude or set()
    hits: list[Statement] = []
    for number, _ in world.index.rank(question):
        item = world.self_statements[number]
        if item.id not in exclude:
            hits.append(item)
            if len(hits) >= limit:
                break
    return hits


def _assemble(conn: sqlite3.Connection, direct: list[Statement], related: list[Statement], others: list[Statement]) -> WordPool:
    """通し番号の候補を作る。今の方式の _build_word_pool と同じ形にする。"""
    statements: list[Statement] = []

    def lines_for(items: list[Statement]) -> list[str]:
        lines = []
        for item in items:
            statements.append(item)
            lines.append(advocacy._word_line(conn, len(statements), item))
        return lines

    direct_lines = lines_for(direct)
    related_lines = lines_for(related)
    other_lines = lines_for(others)
    return WordPool(statements, direct_lines, related_lines, other_lines)


def _parts(pool: WordPool) -> tuple[list[Statement], list[Statement], list[Statement]]:
    first = len(pool.direct)
    second = first + len(pool.related)
    return pool.statements[:first], pool.statements[first:second], pool.statements[second:]


def others_of(pool: WordPool) -> list[Statement]:
    """今の方式の候補のうち、本人の言葉でない行。BM25 の腕でも同じものを渡す（変えるのは本人の言葉の候補だけ）。"""
    return [item for item in pool.statements if item.source not in advocacy._SELF_SOURCES]


def bm25_pool(world: World, base: WordPool, question: str) -> WordPool:
    """上位30件を「問いに直接答えそうな記録」に置く。ふつうの RAG が検索結果を渡す形にする。"""
    return _assemble(world.conn, bm25_hits(world, question, SELF_LIMIT), [], others_of(base))


def bm25_caution_pool(world: World, base: WordPool, question: str) -> WordPool:
    """同じ候補を「関係はありそうな記録」に置く。直接答える記録がないので、プロンプトに注意文が出る。"""
    return _assemble(world.conn, [], bm25_hits(world, question, SELF_LIMIT), others_of(base))


def hybrid_pool(world: World, base: WordPool, question: str) -> WordPool:
    """今の方式の候補の後ろを削り、BM25 の上位を「関係はありそうな記録」に足す。本人の言葉の合計は上限のまま。"""
    direct, related, others = _parts(base)
    self_items = [item for item in [*direct, *related] if item.source in advocacy._SELF_SOURCES]
    seen = {item.id for item in self_items}
    room = max(SELF_LIMIT - len(self_items), HYBRID_RESERVE)
    hits = bm25_hits(world, question, room, seen)
    kept = {item.id for item in self_items[: SELF_LIMIT - len(hits)]}

    def keep(items: list[Statement]) -> list[Statement]:
        return [item for item in items if item.source not in advocacy._SELF_SOURCES or item.id in kept]

    return _assemble(world.conn, keep(direct), [*keep(related), *hits], others)


def elements_from_pick(world: World, pick) -> tuple[list[ModelElement], list[ModelElement]]:
    """要素の番号を要素に戻す。advocacy.answer と同じ決まりで切る。"""

    def chosen(numbers: list[int]) -> list[ModelElement]:
        seen: dict[int, ModelElement] = {}
        for number in numbers:
            if 1 <= number <= len(world.reading):
                seen.setdefault(world.reading[number - 1].id, world.reading[number - 1])
        return list(seen.values())[: advocacy.LIMITS["selected_elements"]]

    direct = chosen(pick.direct_numbers)
    related = [element for element in chosen(pick.related_numbers) if element.id not in {item.id for item in direct}]
    return direct, related


def delivered(world: World, picked: list[Statement]) -> dict:
    """番号から戻した言葉を、画面と同じ処理で出どころごとに分ける。本文は記録のまま。"""
    sections = advocacy._sections(world.conn, picked, world.self_name)
    return {
        "self_words": [item["body"] for item in sections["self_words"]],
        "testimony": [item["body"] for item in sections["testimony"]],
        "guesses": [item["body"] for item in sections["guesses"]],
    }


# ---- 数える（AI を呼ばない） ----


def recall(words: list[str], gold: list[str]) -> float | None:
    """正解のうち、届いた割合。正解がない問いは数えない。"""
    if not gold:
        return None
    return len(set(words) & set(gold)) / len(set(gold))


def capped_recall(words: list[str], gold: list[str], limit: int) -> float | None:
    """届く言葉の上限で割った割合。正解が40件ある広い問いでは、上限の8件を全部当てても2割にしかならないため。"""
    if not gold:
        return None
    return min(1.0, len(set(words) & set(gold)) / min(len(set(gold)), limit))


def precision(words: list[str], gold: list[str]) -> float | None:
    """届いた言葉のうち、正解の割合。届かなかった問いは数えない。"""
    if not words:
        return None
    return len(set(words) & set(gold)) / len(set(words))


def latest_delivered(words: list[str], gold_dates: dict[str, str]) -> bool | None:
    """正解が2つ以上の年にまたがるとき、いちばん新しい正解が届いたかを返す（原則2）。またがらない問いは数えない。"""
    if len({day[:4] for day in gold_dates.values()}) < 2:
        return None
    newest = max(gold_dates.values())
    return any(gold_dates[word] == newest for word in words if word in gold_dates)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---- 記録を開く ----


def _open(folder: Path) -> tuple:
    items = plan.build_plan()
    logs = load.read_logs()
    items = [item for item in items if item.key in logs]
    app, conn, tanaka = _build(folder, items, load.read_models())
    viewer = next(user for user in db.list_users(conn) if user.name == "田中さん")
    return app, conn, tanaka, viewer


def _read_questions() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))


def _usd(result) -> float:
    return config.cost_usd(result.model, result.input_tokens, result.output_tokens) + config.cache_cost_usd(
        result.model, result.cache_read_tokens, result.cache_write_tokens
    )


def _spent_file() -> Path:
    return WORK / "spent.json"


def _spent() -> float:
    path = _spent_file()
    return json.loads(path.read_text(encoding="utf-8"))["usd"] if path.exists() else 0.0


def _add_spent(amount: float) -> float:
    total = _spent() + amount
    _spent_file().write_text(json.dumps({"usd": total}), encoding="utf-8")
    return total


def _check_budget(expected: float) -> None:
    if _spent() + expected > BUDGET_USD:
        raise SystemExit(f"費用の枠を超えそうです。ここまで {_spent():.2f} ドル、見込み {expected:.2f} ドル、枠 {BUDGET_USD} ドル")


# ---- sample ----


def stratify(world: World) -> tuple[list[Statement], list[Statement]]:
    """本人の言葉を、要素の根拠からたどれるものと、たどれないものに分ける。"""
    reachable = {
        world.heads[number].id
        for element in world.reading
        for number in element.evidence_statement_ids
        if number in world.heads
    }
    return (
        [item for item in world.self_statements if item.id in reachable],
        [item for item in world.self_statements if item.id not in reachable],
    )


def draw_sample(items: list[Statement], count: int, seed: int) -> list[Statement]:
    """同じ種からは同じ言葉を抜く。短い言葉と、本文が同じ言葉を除く。"""
    unique: dict[str, Statement] = {}
    for item in sorted(items, key=lambda entry: (entry.effective_date, entry.body)):
        if len(item.body) >= MIN_SAMPLE_CHARS:
            unique.setdefault(item.body, item)
    pool = list(unique.values())
    return random.Random(seed).sample(pool, min(count, len(pool)))


def cmd_sample() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        _, conn, _, viewer = _open(Path(folder))
        try:
            world = open_world(conn, viewer)
            reachable, unreachable = stratify(world)
            elements = [item for item in world.reading if item.kind is ElementKind.CHANGE]
            sample = {
                "seed": SEED,
                "visible_self_words": len(world.self_statements),
                "reachable": len(reachable),
                "unreachable": len(unreachable),
                "reading_elements": len(world.reading),
                "change_elements": len(elements),
                "要素から辿れる": [
                    {"body": item.body, "date": item.effective_date.isoformat()}
                    for item in draw_sample(reachable, SAMPLE_PER_STRATUM, SEED)
                ],
                "要素から辿れない": [
                    {"body": item.body, "date": item.effective_date.isoformat()}
                    for item in draw_sample(unreachable, SAMPLE_PER_STRATUM, SEED + 1)
                ],
            }
        finally:
            conn.close()
    (WORK / "sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"見える本人の言葉 {sample['visible_self_words']}、辿れる {sample['reachable']}、辿れない {sample['unreachable']}")
    return 0


# ---- gold ----


def gold_system(self_name: str) -> str:
    """正解付けの決まり。どの腕の仕組みも知らせない。"""
    return f"""あなたは記録の整理を手伝います。{self_name}が10年間に話した言葉の一覧と、初めて担当するケアマネジャーの問いがあります。
問いの答えになる言葉の番号を、一覧から全部挙げます。番号だけを返します。

- 問いの事柄そのものを{self_name}が語っている言葉だけを入れます。近い話題の言葉は入れません。
- 言い方が違っても同じ事柄なら入れます（入浴と風呂、物忘れと忘れっぽさなど）。
- 「願い」「大切にしてきたこと」のような広い問いでは、答えとして読める言葉を入れます。多いときは、よく表している順に40個までにします。
- 同じ事柄で時期の違う言葉は、どちらも入れます。
- 答えになる言葉がなければ空にします。無理に選びません。
- 前の話がないと何の話か分からない言葉は、入れません。"""


def _gold_block(statements: list[Statement]) -> str:
    return "\n".join(f"[{number}] {item.effective_date.isoformat()}：{item.body}" for number, item in enumerate(statements, start=1))


def cmd_gold() -> int:
    questions = _read_questions()
    _check_budget(6.0)
    with tempfile.TemporaryDirectory() as folder:
        _, conn, _, viewer = _open(Path(folder))
        try:
            world = open_world(conn, viewer)
            ordered = sorted(world.self_statements, key=lambda item: (item.effective_date, item.body))
            block = _gold_block(ordered)
            system = gold_system(world.self_name)

            def label(role: str, case: dict):
                return ai.call_structured(role, system=system, cached_system=block, content=f"問い：{case['question']}", schema=GoldPick)

            def bodies(result) -> list[str]:
                return sorted({ordered[number - 1].body for number in result.value.numbers if 1 <= number <= len(ordered)})

            print(f"一覧 {len(block):,} 字、問い {len(questions)} 問")
            # 最初の1問でキャッシュを書き、残りはキャッシュを読む
            first = label("比べの正解付け", questions[0])
            with ThreadPoolExecutor(WORKERS) as pool:
                rest = list(pool.map(lambda case: label("比べの正解付け", case), questions[1:]))
            results = [first, *rest]
            total = 0.0
            for case, result in zip(questions, results, strict=True):
                case["gold"] = bodies(result)
                case["gold_dates"] = {
                    item.body: item.effective_date.isoformat() for item in ordered if item.body in set(case["gold"])
                }
                total += _usd(result)
            # 正解付けの揺れを見るため、別のモデルで同じ問いの一部に付け直す
            chosen = random.Random(SEED + 2).sample(range(len(questions)), min(20, len(questions)))
            check_first = label("比べの正解の確かめ", questions[chosen[0]])
            with ThreadPoolExecutor(WORKERS) as pool:
                check_rest = list(pool.map(lambda number: label("比べの正解の確かめ", questions[number]), chosen[1:]))
            agreement = []
            for number, result in zip(chosen, [check_first, *check_rest], strict=True):
                other = set(bodies(result))
                mine = set(questions[number]["gold"])
                both = mine | other
                questions[number]["gold_check"] = sorted(other)
                agreement.append(len(mine & other) / len(both) if both else 1.0)
                total += _usd(result)
        finally:
            conn.close()
    QUESTIONS.write_text(json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    spent = _add_spent(total)
    print(f"正解付け {total:.2f} ドル（合計 {spent:.2f}）。別のモデルとの一致（重なりの割合の平均）{mean(agreement):.2f}、{len(agreement)}問")
    (WORK / "gold_agreement.json").write_text(json.dumps({"mean": mean(agreement), "values": agreement}), encoding="utf-8")
    return 0


# ---- run ----


def _call_summary(result) -> dict:
    return {
        "model": result.model,
        "input": result.input_tokens,
        "output": result.output_tokens,
        "cache_read": result.cache_read_tokens,
        "cache_write": result.cache_write_tokens,
        "seconds": result.seconds,
        "usd": _usd(result),
    }


def _pool_chars(pool: WordPool) -> int:
    return sum(len(line) for line in [*pool.direct, *pool.related, *pool.others])


def _pool_self(pool: WordPool) -> int:
    return sum(1 for item in pool.statements if item.source in advocacy._SELF_SOURCES)


def _pick_words(world: World, question: str, pool: WordPool):
    return ai.pick_words(
        system=prompts.word_pick_system(world.self_name),
        text=prompts.word_pick_user_text(question, pool.direct, pool.related, pool.others),
    )


def run_question(world: World, question: str, pick_result, pool_runner) -> dict:
    """1つの問いを全部の腕で流す。AI を呼ぶ部分は pool_runner に任せる。"""
    direct, related = elements_from_pick(world, pick_result.value)
    base = advocacy._build_word_pool(world.conn, world.viewer, direct, related, world.heads, world.scopes, question)
    started = time.monotonic()
    top = bm25_hits(world, question, TOP_WITHOUT_PICKER)
    search_ms = (time.monotonic() - started) * 1000
    pools = {
        ARM_MODEL: base,
        ARM_BM25: bm25_pool(world, base, question),
        ARM_BM25_CAUTION: bm25_caution_pool(world, base, question),
        ARM_HYBRID: hybrid_pool(world, base, question),
    }
    results = pool_runner(question, pools)
    # 要素の根拠から順に取り出す道は、今の方式で AI が失敗したときだけ使う（advocacy.answer と同じ）
    fallback = advocacy._take_evenly(advocacy._evidence_groups(direct, world.heads, world.scopes, question))
    arms: dict[str, dict] = {}
    for arm, pool in pools.items():
        result = results.get(arm)
        record: dict = {"pool_self": _pool_self(pool), "pool_chars": _pool_chars(pool), "called": pool.statements != []}
        if isinstance(result, Exception):
            record["error"] = type(result).__name__
            picked = fallback if arm == ARM_MODEL else []
        elif result is None:
            # 候補がなければ呼ばない。今の方式では「まだ聞けていません」になる
            picked = fallback if arm == ARM_MODEL else []
        else:
            picked = advocacy._chosen_statements(result.value, pool)
            record["call"] = _call_summary(result)
        record.update(delivered(world, picked))
        arms[arm] = record
    arms[ARM_BM25_TOP] = {
        "pool_self": len(top),
        "pool_chars": 0,
        "called": False,
        **delivered(world, top),
    }
    return {
        "question": question,
        "direct_elements": len(direct),
        "related_elements": len(related),
        "element_call": _call_summary(pick_result),
        "search_ms": search_ms,
        "arms": arms,
    }


def cmd_run(rep: int) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / f"run_{rep}.json"
    if target.exists():
        raise SystemExit(f"{target.name} はもうあります。流し直すなら場所を移してください。")
    questions = _read_questions()
    if any("gold" not in case for case in questions):
        raise SystemExit("正解がまだ付いていない問いがあります。先に gold を流してください。")
    _check_budget(len(questions) * 0.06)
    with tempfile.TemporaryDirectory() as folder:
        _, conn, _, viewer = _open(Path(folder))
        try:
            world = open_world(conn, viewer)
            system = prompts.twin_answer_system(world.self_name)

            def elements_for(case: dict):
                return ai.pick_elements(system=system, model_text=world.model_text, question=case["question"])

            print(f"読む要素 {len(world.reading)} 個、{len(world.model_text):,} 字。本人の言葉 {len(world.self_statements)} 件")
            first = elements_for(questions[0])
            with ThreadPoolExecutor(WORKERS) as pool:
                picks = [first, *pool.map(elements_for, questions[1:])]

            executor = ThreadPoolExecutor(WORKERS)

            def run_pools(question: str, pools: dict[str, WordPool]) -> dict:
                def one(arm: str):
                    if not pools[arm].statements:
                        return None
                    try:
                        return _pick_words(world, question, pools[arm])
                    except advocacy._AI_ERRORS as error:
                        return error

                return dict(zip(pools, executor.map(one, pools), strict=True))

            rows = []
            total = 0.0
            for number, (case, pick) in enumerate(zip(questions, picks, strict=True), start=1):
                row = run_question(world, case["question"], pick, run_pools)
                total += row["element_call"]["usd"] + sum(arm.get("call", {}).get("usd", 0.0) for arm in row["arms"].values())
                rows.append(row)
                print(f"{number}/{len(questions)} {total:.2f}ドル {case['question'][:24]}", flush=True)
                if _spent() + total > BUDGET_USD:
                    _add_spent(total)
                    raise SystemExit("費用の枠を超えそうなので止めました。")
            executor.shutdown()
        finally:
            conn.close()
    target.write_text(json.dumps({"rep": rep, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{rep}回目 {total:.2f} ドル（合計 {_add_spent(total):.2f}）")
    return 0


# ---- judge ----

JUDGE_SYSTEM = """あなたは記録の読み手です。初めて担当するケアマネジャーの問いと、記録から届いた本人の言葉の組がいくつかあります。
どの組がどの作り方から出たかは分かりません。問いと言葉だけを見て、組ごとに判定します。

- answers：聞いた事柄がこの言葉から分かるなら「分かる」、一部だけなら「一部」、分からないなら「分からない」。
- swapped：聞いた事柄ではなく、近い話題の言葉にすり替わっている言葉が1つでも混ざっていれば true。

言葉の良し悪しや、その人の考えの当否は判定しません。"""


def judge_options(words_by_arm: dict[str, list[str]], seed: int) -> tuple[list[tuple[str, list[str]]], dict[str, str]]:
    """同じ言葉の組は1つにまとめ、順を乱数で入れ替え、腕の名前を隠して記号を付ける。言葉が届いていない腕は判定に回さない。"""
    unique: dict[tuple[str, ...], list[str]] = {}
    for words in words_by_arm.values():
        if words:
            unique.setdefault(tuple(sorted(words)), words)
    keys = list(unique)
    random.Random(seed).shuffle(keys)
    labels = "ABCDEFGH"
    options = [(labels[index], list(unique[key])) for index, key in enumerate(keys)]
    by_key = {key: labels[index] for index, key in enumerate(keys)}
    mapping = {arm: by_key[tuple(sorted(words))] for arm, words in words_by_arm.items() if words}
    return options, mapping


def cmd_judge() -> int:
    questions = _read_questions()
    run = json.loads((WORK / "run_1.json").read_text(encoding="utf-8"))
    _check_budget(len(questions) * 0.03)

    def judge(pair: tuple[int, dict]) -> dict:
        number, row = pair
        words_by_arm = {arm: data["self_words"] for arm, data in row["arms"].items()}
        options, mapping = judge_options(words_by_arm, SEED + number)
        if not options:
            return {"question": row["question"], "mapping": {}, "items": {}, "usd": 0.0}
        text = "\n\n".join(f"## 組 {label}\n" + "\n".join(f"- {word}" for word in words) for label, words in options)
        result = ai.call_structured(
            "比べの判定", system=JUDGE_SYSTEM, content=f"問い：{row['question']}\n\n{text}", schema=JudgeResult
        )
        return {
            "question": row["question"],
            "mapping": mapping,
            "items": {item.label: {"answers": item.answers, "swapped": item.swapped} for item in result.value.items},
            "usd": _usd(result),
        }

    with ThreadPoolExecutor(WORKERS) as pool:
        judged = list(pool.map(judge, enumerate(run["rows"])))
    total = sum(item["usd"] for item in judged)
    (WORK / "judged.json").write_text(json.dumps(judged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"判定 {total:.2f} ドル（合計 {_add_spent(total):.2f}）")
    return 0


# ---- repeat ----


def cmd_repeat() -> int:
    """同じ記録で今の方式を2回流す。記録を入れ直すと要素の並びが変わるので、AI の揺れだけを見るために分ける。"""
    questions = _read_questions()
    first = json.loads((WORK / "run_1.json").read_text(encoding="utf-8"))["rows"]
    second = json.loads((WORK / "run_2.json").read_text(encoding="utf-8"))["rows"]
    eligible = [
        number
        for number, (one, two) in enumerate(zip(first, second, strict=True))
        if one["arms"][ARM_MODEL]["self_words"] or two["arms"][ARM_MODEL]["self_words"]
    ]
    chosen = sorted(random.Random(SEED + 3).sample(eligible, min(REPEAT_QUESTIONS, len(eligible))))
    _check_budget(len(chosen) * 2 * 0.02)
    rows = []
    total = 0.0
    with tempfile.TemporaryDirectory() as folder:
        _, conn, _, viewer = _open(Path(folder))
        try:
            world = open_world(conn, viewer)
            system = prompts.twin_answer_system(world.self_name)

            def once(question: str) -> tuple[set[int], list[int], set[str], float]:
                pick = ai.pick_elements(system=system, model_text=world.model_text, question=question)
                direct, related = elements_from_pick(world, pick.value)
                pool = advocacy._build_word_pool(conn, viewer, direct, related, world.heads, world.scopes, question)
                spent = _usd(pick)
                picked: list[Statement] = []
                if pool.statements:
                    words = _pick_words(world, question, pool)
                    spent += _usd(words)
                    picked = advocacy._chosen_statements(words.value, pool)
                return (
                    {item.id for item in [*direct, *related]},
                    [item.id for item in pool.statements],
                    set(delivered(world, picked)["self_words"]),
                    spent,
                )

            for number in chosen:
                question = questions[number]["question"]
                one = once(question)
                two = once(question)
                total += one[3] + two[3]
                rows.append(
                    {
                        "question": question,
                        "elements_same": one[0] == two[0],
                        "pool_same": one[1] == two[1],
                        "words_same": one[2] == two[2],
                    }
                )
                print(f"{len(rows)}/{len(chosen)} {total:.2f}ドル", flush=True)
        finally:
            conn.close()
    (WORK / "repeat.json").write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"同じ記録の繰り返し {total:.2f} ドル（合計 {_add_spent(total):.2f}）")
    return 0


# ---- probe ----

_BOILERPLATE = re.compile(r"(について)?、?(ご本人は)?どう(話して|感じて|考えて|思って)(い)?(ました|います)か?[？。]?$")


def strip_boilerplate(question: str) -> str:
    """問いの終わりの定型句を外す。どの問いにも付くので、BM25 が短い文を上位に出す原因になるかを見るため。"""
    return _BOILERPLATE.sub("", question)


def top_potential(world: World, question: str, gold: list[str], top: int = SELF_LIMIT) -> float:
    """BM25 の上位に入る正解の割合。選ぶ係を通す前に、候補の時点でどこまで届く見込みがあるかを見る。"""
    bodies = {world.self_statements[number].body for number, _ in world.index.rank(question)[:top]}
    return min(1.0, len(bodies & set(gold)) / min(len(set(gold)), QUOTE_LIMIT))


def cmd_probe() -> int:
    """AI を呼ばずに、結果を読むための2つの数字を測る。"""
    questions = _read_questions()
    answered = [case for case in questions if case["gold"]]
    orders: list[list[str]] = []
    result: dict = {}
    for attempt in range(2):
        with tempfile.TemporaryDirectory() as folder:
            _, conn, _, viewer = _open(Path(folder))
            try:
                world = open_world(conn, viewer)
                orders.append([element.body for element in world.reading])
                if attempt == 0:
                    result["boilerplate_questions"] = sum(strip_boilerplate(case["question"]) != case["question"] for case in answered)
                    result["answered_questions"] = len(answered)
                    result["top30_as_asked"] = mean([top_potential(world, case["question"], case["gold"]) for case in answered])
                    result["top30_stripped"] = mean([top_potential(world, strip_boilerplate(case["question"]), case["gold"]) for case in answered])
                    result["top8_as_asked"] = mean([top_potential(world, case["question"], case["gold"], TOP_WITHOUT_PICKER) for case in answered])
                    result["top8_stripped"] = mean([top_potential(world, strip_boilerplate(case["question"]), case["gold"], TOP_WITHOUT_PICKER) for case in answered])
                    changed = [case for case in questions if case["group"] in ("要素から辿れる言葉", "要素から辿れない言葉")]

                    def in_top(question: str, body: str) -> bool:
                        return body in {world.self_statements[number].body for number, _ in world.index.rank(question)[:SELF_LIMIT]}

                    result["changed_words_questions"] = len(changed)
                    result["changed_words_in_top30"] = sum(in_top(case["question"], case["source_body"]) for case in changed)
                    result["changed_words_in_top30_stripped"] = sum(in_top(strip_boilerplate(case["question"]), case["source_body"]) for case in changed)
            finally:
                conn.close()
    result["reading_elements"] = len(orders[0])
    result["reading_same_set"] = sorted(orders[0]) == sorted(orders[1])
    result["reading_order_differs_at"] = sum(first != second for first, second in zip(*orders, strict=False))
    (WORK / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


# ---- report ----


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _verdict(judged: dict, arm: str) -> dict | None:
    label = judged["mapping"].get(arm)
    return judged["items"].get(label) if label else None


def summarize(questions: list[dict], rows: list[dict], judged: list[dict] | None, groups: list[str] | None) -> dict[str, dict]:
    """腕ごとに数える。groups を渡すとその群の問いだけ。"""
    summary: dict[str, dict] = {}
    for arm in ARMS:
        recalls, capped, precisions, latest, targets = [], [], [], [], []
        delivered_count = total = false_delivery = negatives = answered = swapped = judged_count = 0
        for number, (case, row) in enumerate(zip(questions, rows, strict=True)):
            if groups and case["group"] not in groups:
                continue
            words = row["arms"][arm]["self_words"]
            gold = case["gold"]
            total += 1
            delivered_count += bool(words)
            if not gold:
                negatives += 1
                false_delivery += bool(words)
            value = recall(words, gold)
            if value is not None:
                recalls.append(value)
                capped.append(capped_recall(words, gold, QUOTE_LIMIT))
            value = precision(words, gold)
            if value is not None and gold:
                precisions.append(value)
            value = latest_delivered(words, case["gold_dates"])
            if value is not None:
                latest.append(1.0 if value else 0.0)
            if case.get("source_body"):
                targets.append(1.0 if case["source_body"] in words else 0.0)
            if judged is not None and words:
                verdict = _verdict(judged[number], arm)
                if verdict:
                    judged_count += 1
                    answered += verdict["answers"] == "分かる"
                    swapped += bool(verdict["swapped"])
        summary[arm] = {
            "total": total,
            "delivered": delivered_count,
            "recall": mean(recalls),
            "recall_n": len(recalls),
            "capped": mean(capped),
            "precision": mean(precisions),
            "latest": mean(latest),
            "latest_n": len(latest),
            "target": mean(targets),
            "target_n": len(targets),
            "negatives": negatives,
            "false_delivery": false_delivery,
            "judged": judged_count,
            "answered": answered,
            "swapped": swapped,
        }
    return summary


def paired(questions: list[dict], rows: list[dict], first: str, second: str, groups: list[str] | None = None) -> dict:
    """正解のある問いで、2つの腕の届いた割合を問いごとに比べる。groups を渡すとその群の問いだけ。"""
    wins = losses = ties = 0
    for case, row in zip(questions, rows, strict=True):
        if not case["gold"] or (groups and case["group"] not in groups):
            continue
        a = recall(row["arms"][first]["self_words"], case["gold"])
        b = recall(row["arms"][second]["self_words"], case["gold"])
        if a > b:
            wins += 1
        elif a < b:
            losses += 1
        else:
            ties += 1
    return {"first_better": wins, "second_better": losses, "same": ties}


def cost_rows(rows: list[dict]) -> dict[str, dict]:
    """腕ごとに、1問あたりの量を出す。要素を選ぶ段は今の方式と今の方式＋BM25にだけ足す。"""
    result: dict[str, dict] = {}
    for arm in ARMS:
        uses_elements = arm in (ARM_MODEL, ARM_HYBRID)
        usd, input_tokens, cache_read, chars, seconds, calls = [], [], [], [], [], 0
        for row in rows:
            data = row["arms"][arm]
            call = data.get("call")
            element = row["element_call"] if uses_elements else None
            usd.append((call["usd"] if call else 0.0) + (element["usd"] if element else 0.0))
            input_tokens.append((call["input"] if call else 0) + (element["input"] if element else 0))
            cache_read.append(element["cache_read"] if element else 0)
            chars.append(data["pool_chars"])
            seconds.append((call["seconds"] or 0.0 if call else 0.0) + ((element["seconds"] or 0.0) if element else 0.0))
            calls += 1 if call else 0
        result[arm] = {
            "usd": mean(usd),
            "input": mean(input_tokens),
            "cache_read": mean(cache_read),
            "pool_chars": mean(chars),
            "seconds": mean(seconds),
            "calls": calls,
        }
    return result


def stability(first: list[dict], second: list[dict]) -> dict[str, dict]:
    """2回流して、届いた本人の言葉の組が同じだった問いを数える。

    2回とも何も届かなかった問いは「同じ」になるので、1回でも届いた問いだけの値を別に出す。
    """
    result = {}
    for arm in ARMS:
        same = empty = 0
        for a, b in zip(first, second, strict=True):
            one, two = set(a["arms"][arm]["self_words"]), set(b["arms"][arm]["self_words"])
            same += one == two
            empty += not one and not two
        result[arm] = {"same": same, "empty": empty, "total": len(first)}
    return result


def _table(summary: dict[str, dict]) -> list[str]:
    lines = [
        "| 腕 | 問い | 言葉が届いた問い | 正解のうち届いた割合 | 上限で割った割合 | 届いた言葉のうち正解の割合 | もとにした言葉が届いた | 新しい言葉が届いた | 話題がない問いに届けた | 判定：分かる | 判定：すり替えあり |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, data in summary.items():
        lines.append(
            f"| {arm} | {data['total']} | {data['delivered']} | {_percent(data['recall'])}（{data['recall_n']}問） | {_percent(data['capped'])} | {_percent(data['precision'])} | "
            f"{_percent(data['target'])}（{data['target_n']}問） | {_percent(data['latest'])}（{data['latest_n']}問） | "
            f"{data['false_delivery']}/{data['negatives']} | {data['answered']}/{data['judged']} | {data['swapped']}/{data['judged']} |"
        )
    return lines


def cmd_report() -> int:
    questions = _read_questions()
    runs = [json.loads((WORK / f"run_{rep}.json").read_text(encoding="utf-8")) for rep in (1, 2)]
    judged_path = WORK / "judged.json"
    judged = json.loads(judged_path.read_text(encoding="utf-8")) if judged_path.exists() else None
    groups = sorted({case["group"] for case in questions})
    first = runs[0]["rows"]
    lines = ["## すべての問い（1回目）", "", *_table(summarize(questions, first, judged, None))]
    for group in groups:
        lines += ["", f"## {group}（1回目）", "", *_table(summarize(questions, first, judged, [group]))]
    # 抜いた言葉の層は実際の比率（辿れる言葉と辿れない言葉の件数）で重みを付ける。抜いた数は同じなので、そのままだと辿れない言葉が多く見える
    sample = json.loads((WORK / "sample.json").read_text(encoding="utf-8"))
    weight = sample["reachable"] / (sample["reachable"] + sample["unreachable"])
    lines += ["", f"## 抜いた言葉の問いを実際の比率で重み付け（辿れる {weight * 100:.0f}%、辿れない {(1 - weight) * 100:.0f}%）", ""]
    for rep, run in zip((1, 2), runs, strict=True):
        reach = summarize(questions, run["rows"], None, ["要素から辿れる言葉"])
        miss = summarize(questions, run["rows"], None, ["要素から辿れない言葉"])
        for arm in ARMS:
            target = weight * reach[arm]["target"] + (1 - weight) * miss[arm]["target"]
            capped = weight * reach[arm]["capped"] + (1 - weight) * miss[arm]["capped"]
            lines.append(f"- {rep}回目 {arm}：もとにした言葉が届いた {_percent(target)}、上限で割った割合 {_percent(capped)}")
    lines += ["", "## 同じ問いを2回目に流した結果", "", *_table(summarize(questions, runs[1]["rows"], None, None))]
    lines += ["", "## 問いごとの比べ（正解のある問い、届いた割合）", ""]
    for rep, run in zip((1, 2), runs, strict=True):
        for a, b in ((ARM_HYBRID, ARM_MODEL), (ARM_BM25, ARM_MODEL)):
            pair = paired(questions, run["rows"], a, b)
            lines.append(f"- {rep}回目 {a} と {b}：{a}が上 {pair['first_better']}、{b}が上 {pair['second_better']}、同じ {pair['same']}")
    # 足した腕の伸びがどの問いから来るかを見る。同じ語の問いは BM25 に有利なので分ける
    changed_words = ["要素から辿れる言葉", "要素から辿れない言葉"]
    without_same = [group for group in groups if group != "同じ語を使う問い"]
    lines += ["", "## 足した腕の伸びの内訳", ""]
    for rep, run in zip((1, 2), runs, strict=True):
        base = summarize(questions, run["rows"], None, without_same)
        pair = paired(questions, run["rows"], ARM_HYBRID, ARM_MODEL, changed_words)
        lines.append(
            f"- {rep}回目 同じ語の問いを除く：上限で割った割合 {ARM_MODEL} {base[ARM_MODEL]['capped'] * 100:.1f}%、{ARM_HYBRID} {base[ARM_HYBRID]['capped'] * 100:.1f}%"
        )
        lines.append(f"- {rep}回目 語を変えた{pair['first_better'] + pair['second_better'] + pair['same']}問：{ARM_HYBRID}が上 {pair['first_better']}、{ARM_MODEL}が上 {pair['second_better']}、同じ {pair['same']}")
    lines += ["", "## 1問あたりの量（1回目）", "", "| 腕 | 費用（ドル） | 入力トークン | キャッシュ読み | 候補の字数 | 秒（AI の呼び出しの合計） | AI を呼んだ問い |", "|---|---|---|---|---|---|---|"]
    for arm, data in cost_rows(first).items():
        lines.append(
            f"| {arm} | {data['usd']:.4f} | {data['input']:.0f} | {data['cache_read']:.0f} | {data['pool_chars']:.0f} | {data['seconds']:.1f} | {data['calls']} |"
        )
    lines += ["", "## 2回流して同じ言葉の組が届いた問い（2回とも記録を入れ直している）", ""]
    for arm, value in stability(first, runs[1]["rows"]).items():
        reached = value["total"] - value["empty"]
        lines.append(
            f"- {arm}：全部の問いで {value['same']}/{value['total']}（2回とも何も届かない問い {value['empty']} を含む）、1回でも届いた問いで {value['same'] - value['empty']}/{reached}"
        )
    repeat_path = WORK / "repeat.json"
    if repeat_path.exists():
        repeat = json.loads(repeat_path.read_text(encoding="utf-8"))
        rows = repeat["rows"]
        same_elements = [row for row in rows if row["elements_same"]]
        lines += [
            "",
            f"## 同じ記録で今の方式を2回流した結果（{len(rows)}問）",
            "",
            f"- 選んだ要素が同じ問い：{len(same_elements)}/{len(rows)}",
            f"- 候補が同じ問い：{sum(row['pool_same'] for row in rows)}/{len(rows)}",
            f"- 届いた本人の言葉の組が同じ問い：{sum(row['words_same'] for row in rows)}/{len(rows)}",
            f"- 候補が同じ問いのうち、組も同じ問い：{sum(row['words_same'] for row in rows if row['pool_same'])}/{sum(row['pool_same'] for row in rows)}",
        ]
    probe_path = WORK / "probe.json"
    if probe_path.exists():
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        lines += [
            "",
            "## AI を呼ばずに測った数字",
            "",
            f"- 定型句のある問い：正解のある{probe['answered_questions']}問のうち{probe['boilerplate_questions']}問",
            f"- BM25 の上位30件に入る正解（上限で割る）：問いのまま {probe['top30_as_asked'] * 100:.1f}%、定型句を外す {probe['top30_stripped'] * 100:.1f}%",
            f"- BM25 の上位8件に入る正解（上限で割る）：問いのまま {probe['top8_as_asked'] * 100:.1f}%、定型句を外す {probe['top8_stripped'] * 100:.1f}%",
            f"- 語を変えた{probe['changed_words_questions']}問で、もとにした言葉が上位30件に入る：問いのまま {probe['changed_words_in_top30']}問、定型句を外す {probe['changed_words_in_top30_stripped']}問",
            f"- 記録を入れ直したときの、読む要素{probe['reading_elements']}個：組が同じ {probe['reading_same_set']}、並びが違う位置 {probe['reading_order_differs_at']}",
        ]
    agreement = WORK / "gold_agreement.json"
    if agreement.exists():
        value = json.loads(agreement.read_text(encoding="utf-8"))
        lines += ["", f"正解付けと別のモデルの一致（重なりの割合の平均）：{value['mean']:.2f}（{len(value['values'])}問）"]
    lines += ["", f"BM25 の検索にかかった時間：1問 {mean([row['search_ms'] for row in first]):.1f} ミリ秒", f"比べ全体の費用：{_spent():.2f} ドル"]
    text = "\n".join(lines)
    print(text)
    RESULTS.write_text(
        json.dumps(
            {
                "questions": [{key: case.get(key) for key in ("question", "group", "expect", "source_body", "gold")} for case in questions],
                "runs": [
                    [
                        {
                            "question": row["question"],
                            "direct_elements": row["direct_elements"],
                            "arms": {arm: {"self_words": data["self_words"], "error": data.get("error")} for arm, data in row["arms"].items()},
                        }
                        for row in run["rows"]
                    ]
                    for run in runs
                ],
                "judged": judged,
                "repeat": json.loads((WORK / "repeat.json").read_text(encoding="utf-8")) if (WORK / "repeat.json").exists() else None,
            },
            ensure_ascii=False,
            indent=0,
        ),
        encoding="utf-8",
    )
    (WORK / "report.md").write_text(text + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="今の方式と BM25 の RAG を比べます。")
    parser.add_argument("command", choices=["sample", "gold", "run", "judge", "repeat", "probe", "report"])
    parser.add_argument("--rep", type=int, default=1, choices=[1, 2], help="run の何回目か")
    args = parser.parse_args(argv)
    if args.command == "sample":
        return cmd_sample()
    if args.command == "gold":
        return cmd_gold()
    if args.command == "run":
        return cmd_run(args.rep)
    if args.command == "judge":
        return cmd_judge()
    if args.command == "repeat":
        return cmd_repeat()
    if args.command == "probe":
        return cmd_probe()
    return cmd_report()


if __name__ == "__main__":
    sys.exit(main())
