"""セルフの答え合わせ。

役割：検証役の実体。セルフが本人に近づいたかを、感想ではなく測る。測定は次の5つで、ラウンドごとに全出力を手で整えず残す。
  1. 未来の当て：ある時点までの盤面で、その後に本人が実際に決めた場面を答えさせ、本人の実際の言葉と別の AI が比べる
  2. 時点の比べ：2028年、2031年、2034年の盤面で、年を重ねるほど当たるかを見る
  3. 基本との比べ：同じ問いを BM25 の RAG と今の意思モデルにも答えさせる
  4. 正解との比べ：物語の設計資料から作った項目と、セルフの全文を比べる。エンジンは設計資料を読まない
  5. 守りの点検：引いてはいけない言葉、診断や服薬の指示の言い切り（app/medical.py）、型や点数の語をプログラムで探す
4つの極：4. 10年後に代弁する（確かめの道具）。
守る原則：3、8（架空データだけ）、9、15。点数は開発の確かめにだけ使い、画面には出さない。

問いは開発用（dev）と確認用（test）に分ける。チューニングは dev だけを見て行い、最後に test で確かめる。問いに合わせ込むのを防ぐため。

使い方：python -m eval.selfhood_check データベース build-cases --bible 設計資料のフォルダ --out eval/selfhood_cases.json --secret 項目の置き場
        python -m eval.selfhood_check データベース round --run 名前 --round 0 --split dev --out docs/verification/selfhood/round_0
関連するテスト：tests/test_selfhood_check.py
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from app import advocacy_voice, ai, config, db, ledger, medical, model_store, selfhood
from app.schemas import Role, ShareScope, Source

CUTS = ("2028-12-31", "2031-12-31", "2034-12-31")
ROLE_JUDGE = "答え合わせの判定"
ROLE_BUILD = "答え合わせの問い作り"
ROLE_BASE = "基本の答え"
WORKERS = 6
_TYPE_WORDS = ("性格タイプ", "タイプの人", "点数", "偏差値", "診断")
_SCORE_KEYS = ("same_decision", "same_reason", "voice", "no_fabrication")
_JUDGE_SYSTEM = """あなたは、本人の代弁を採点する係です。答えた仕組みは知らされません。
本人が実際にその場面で話した言葉を正解として、答えを次の4項目で 0、1、2 の点にします。甘くしません。
- same_decision：本人と同じ判断や向きか。2 同じ、1 一部同じか曖昧、0 違う。
- same_reason：同じ理由、同じ気がかりか。2 同じ、1 一部、0 違う。
- voice：本人らしい言い方か。短さ、語尾、方言、照れ。2 本人らしい、1 少し、0 別人。
- no_fabrication：答えの具体が「答えに使えた材料」か正解の言葉にあるか。2 作っていない、1 少し、0 作った。材料にあることは作り話にしません。
note は、なぜその点かを1文で書きます。病名を書きません。"""


class Score(BaseModel):
    same_decision: int
    same_reason: int
    voice: int
    no_fabrication: int
    note: str = ""


class Moment(BaseModel):
    date: str
    topic: str
    why: str


class Moments(BaseModel):
    moments: list[Moment]


class CaseDraft(BaseModel):
    usable: bool
    question: str = ""
    decision: str = ""
    reason: str = ""
    numbers: list[int] = []


class BibleItem(BaseModel):
    kind: str
    text: str
    question: str


class BibleItems(BaseModel):
    items: list[BibleItem]


class ItemVerdict(BaseModel):
    number: int
    verdict: str
    note: str = ""


class ItemVerdicts(BaseModel):
    verdicts: list[ItemVerdict]


class Answer(BaseModel):
    answer: str


def tokens(text: str) -> list[str]:
    """文字の2つ組にする。日本語を単語に分ける辞書を入れないため。"""
    cleaned = re.sub(r"\s", "", text)
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def bm25_rank(question: str, documents: list[str], k1: float = 1.5, b: float = 0.75) -> list[int]:
    """BM25 の順位を返す。基本の RAG と比べるため。"""
    tokenized = [Counter(tokens(document)) for document in documents]
    lengths = [sum(counter.values()) for counter in tokenized]
    average = sum(lengths) / max(len(lengths), 1)
    frequency = Counter(token for counter in tokenized for token in counter)
    total = len(documents)
    scores = []
    query = tokens(question)
    for index, counter in enumerate(tokenized):
        score = 0.0
        for token in query:
            if token in counter:
                idf = math.log(1 + (total - frequency[token] + 0.5) / (frequency[token] + 0.5))
                score += idf * counter[token] * (k1 + 1) / (counter[token] + k1 * (1 - b + b * lengths[index] / max(average, 1)))
        scores.append(score)
    return sorted(range(total), key=lambda index: scores[index], reverse=True)


def eligible_cuts(day: str) -> list[str]:
    """その場面より前の時点を返す。"""
    return [cut for cut in CUTS if cut < day]


def latest_cut(day: str) -> str | None:
    cuts = eligible_cuts(day)
    return cuts[-1] if cuts else None


def split_of(index: int) -> str:
    """問いを dev と test に分ける。3問に1問を test にする。"""
    return "test" if index % 3 == 2 else "dev"


def average_scores(scores: list[dict]) -> dict[str, float]:
    """項目ごとの平均と、全体の平均を返す。0 から 2 の点を 0 から 1 にする。"""
    if not scores:
        return {key: 0.0 for key in (*_SCORE_KEYS, "total")}
    result = {key: round(sum(item[key] for item in scores) / len(scores) / 2, 3) for key in _SCORE_KEYS}
    result["total"] = round(sum(result[key] for key in _SCORE_KEYS) / len(_SCORE_KEYS), 3)
    return result


def guard_findings(text: str, forbidden: dict[str, list[str]], min_length: int = 15) -> list[dict]:
    """文の中に、出てはいけない言葉がないかを探す。プログラムの点検で、AI を呼ばない。

    関連するテスト：tests/test_selfhood_check.py
    """
    findings = []
    for kind, bodies in forbidden.items():
        for body in bodies:
            if len(body) >= min_length and body in text:
                findings.append({"kind": kind, "length": len(body)})
    return findings


def forbidden_texts(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """引いてはいけない本文を集める。"""
    me = db.self_user(conn)
    pair = [
        row[0]
        for row in conn.execute(
            """
            SELECT m.text FROM room_messages m JOIN rooms r ON r.id = m.room_id
            WHERE r.kind <> 'ツイン' AND m.text IS NOT NULL
            """
        )
    ]
    # いま有効な行だけを見る。本人が範囲を広げた言葉は、古い行が「本人だけ」でも見せてよいため
    resting = [
        row[0]
        for row in conn.execute(
            """
            SELECT body FROM statements
            WHERE (confirm_status = 'お休み' OR share_scope = ?)
              AND id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
            """,
            (ShareScope.SELF_ONLY.value,),
        )
    ]
    return {"人だけの部屋の言葉": pair, "本人だけかお休みの言葉": resting, "_me": [me.display_name] if me else []}


# 答え合わせの費用。判定と基本の答えは ai_calls に残さないので、ここで積む
SPENT: dict[str, float] = {}
_SPENT_GUARD = threading.Lock()


def _ask(role: str, system: str, text: str, schema, cached: str | None = None):
    for attempt in range(2):
        try:
            result = ai.call_structured(role, system=system, content=text, schema=schema, cached_system=cached)
            with _SPENT_GUARD:
                SPENT[role] = SPENT.get(role, 0.0) + config.cost_usd(result.model, result.input_tokens, result.output_tokens)
            return result
        except (ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError):
            if attempt == 1:
                raise
    raise RuntimeError("届かない")


def build_cases(conn: sqlite3.Connection, bible: Path, out: Path, secret: Path) -> None:
    """問いを作る。設計資料はここでだけ読む。問いの文には結果を書かず、正解は記録の番号で持つ。病名は入れない。"""
    timeline = (bible / "年表.md").read_text(encoding="utf-8")
    profile = (bible / "人物像.md").read_text(encoding="utf-8")
    moments = _ask(
        ROLE_BUILD,
        "物語の年表を読み、主人公が自分で何かを決めた場面、人の案を断った、受け入れた場面を選びます。2029年以降から30個、時期を散らして選びます。date は YYYY-MM-DD です。病名を書きません。",
        timeline,
        Moments,
    ).value.moments
    me = db.self_user(conn)

    def one(moment: Moment) -> dict | None:
        center = date.fromisoformat(moment.date)
        rows = conn.execute(
            """
            SELECT id, effective_date, body FROM statements
            WHERE source = ? AND speaker_user_id = ? AND effective_date BETWEEN ? AND ?
              AND share_scope <> ? AND confirm_status <> 'お休み'
            ORDER BY effective_date, rowid
            """,
            (Source.SELF_WORDS.value, me.id, (center - timedelta(days=21)).isoformat(), (center + timedelta(days=21)).isoformat(), ShareScope.SELF_ONLY.value),
        ).fetchall()
        if not rows:
            return None
        lines = [f"[{n}] {row['effective_date']}：{row['body']}" for n, row in enumerate(rows, start=1)]
        draft = _ask(
            ROLE_BUILD,
            """場面と本人の実際の言葉を読み、専門職が本人のツインに聞く問いを作ります。
- 問いには結果を書きません。本人がどう決めたかが問いから分からないようにします。場面の説明は短く入れます。
- decision は本人の判断、reason は本人の理由を、言葉から1文で書きます。
- numbers は正解になる本人の言葉の番号です。言葉から判断が読めないなら usable を false にします。
- 病名を書きません。""",
            f"## 場面\n{moment.date} {moment.topic}\n{moment.why}\n\n## 本人の言葉\n" + "\n".join(lines),
            CaseDraft,
        ).value
        ids = [rows[n - 1]["id"] for n in draft.numbers if 1 <= n <= len(rows)]
        if not draft.usable or not ids:
            return None
        return {"date": moment.date, "topic": moment.topic, "question": draft.question, "decision": draft.decision, "reason": draft.reason, "true_statement_ids": ids}

    with ThreadPoolExecutor(WORKERS) as pool:
        cases = [case for case in pool.map(one, moments) if case]
    cases.sort(key=lambda case: case["date"])
    for index, case in enumerate(cases):
        case["id"] = f"c{index:02d}"
        case["split"] = split_of(index)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    items = _ask(
        ROLE_BUILD,
        """物語の設計資料を読み、主人公がどんな人かの項目を40個作ります。この項目で、記録から組み上げた人物像を採点します。
kind は 価値、決め方、関係、人生、話し方 のどれかです。text は正解の内容を1文で。question は、本人を知らない人にそれを聞く問いで、答えを書きません。
病名を書きません。""",
        profile + "\n\n" + timeline,
        BibleItems,
    ).value.items
    secret.write_text(json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"問い {len(cases)} 件、項目 {len(items)} 件")


def _viewer(conn: sqlite3.Connection):
    """問いを聞く人。託された専門職を使う。"""
    users = db.list_users(conn)
    return next(user for user in users if user.role is Role.PROFESSIONAL and user.relation and "ケアマネ" in user.relation)


def selfhood_answer(conn, run: str, question: str, cut: str | None) -> tuple[str, str]:
    """本人としての答えと、答えに使えた材料を返す。気持ちの読みは外から見た文なので、採点する答えに入れない。"""
    material = advocacy_voice.gather_from_selfhood(conn, _viewer(conn), question, run, as_of=cut)
    # 今日の日付は渡さない。問いの場面は時点より後なので、「まだ分からん」と答えてしまうため
    view, _ = advocacy_voice.speak(conn, material)
    reasons = " ".join(reason["text"] for reason in view["reasons"])
    return f"{view['claim']} {reasons}", "\n".join([*material.element_lines, *material.word_lines])


_BASE_SYSTEM = """あなたは本人に代わって専門職の問いに答えます。渡された材料だけを読み、本人として、本人の話し方で答えます。3〜6文で書きます。健康の助言をしません。"""


def bm25_answer(conn, question: str, cut: str) -> tuple[str, str]:
    rows = [row for row in selfhood.statement_rows(conn) if row["effective_date"] <= cut and row["source"] == Source.SELF_WORDS.value]
    order = bm25_rank(question, [row["body"] for row in rows])[:20]
    lines = "\n".join(f"{rows[i]['effective_date']}：{rows[i]['body']}" for i in order)
    return _ask(ROLE_BASE, _BASE_SYSTEM, f"## 問い\n{question}\n\n## 検索で見つけた本人の言葉\n{lines}", Answer).value.answer, lines


def model_answer(conn, question: str, cut: str) -> tuple[str, str]:
    heads = ledger.head_map(conn)
    elements = [element for element in model_store.active_elements(conn) if element.valid_from.isoformat() <= cut]
    reading = model_store.for_reading(elements, heads)
    lines = "\n".join(element.body for element in reading)
    return _ask(ROLE_BASE, _BASE_SYSTEM, f"## 問い\n{question}\n\n## 意思モデルの要素\n{lines}", Answer).value.answer, lines


def judge(conn, case: dict, answer: str, material: str = "") -> dict:
    rows = [conn.execute("SELECT effective_date, body FROM statements WHERE id = ?", (i,)).fetchone() for i in case["true_statement_ids"]]
    truth = "\n".join(f"{row[0]}：{row[1]}" for row in rows if row)
    result = _ask(
        ROLE_JUDGE,
        _JUDGE_SYSTEM,
        f"## 問い\n{case['question']}\n\n## 本人の実際の言葉\n{truth}\n\n## 本人の判断\n{case['decision']}\n\n## 本人の理由\n{case['reason']}\n\n## 答えに使えた材料\n{material[:30000]}\n\n## 採点する答え\n{answer}",
        Score,
    ).value
    return {key: max(0, min(2, getattr(result, key))) for key in _SCORE_KEYS} | {"note": result.note}


def check_selfhood_against_bible(body: str, items: list[dict]) -> dict:
    """正解の項目とセルフの全文を比べる。項目の文は記録に残さず、数だけを残す（設計資料の中身を公開しないため）。"""
    numbered = "\n".join(f"[{n}] {item['text']}" for n, item in enumerate(items, start=1))
    result = _ask(
        ROLE_JUDGE,
        "人物像の文が、正解の項目を言い当てているかを採点します。verdict は 当たり、一部、触れていない、食い違い のどれかです。甘くしません。全部の番号に答えます。",
        f"## 正解の項目\n{numbered}\n\n## 人物像\n{body}",
        ItemVerdicts,
    ).value
    counts = Counter(item.verdict for item in result.verdicts)
    by_kind: dict[str, Counter] = {}
    for verdict in result.verdicts:
        if 1 <= verdict.number <= len(items):
            by_kind.setdefault(items[verdict.number - 1]["kind"], Counter())[verdict.verdict] += 1
    hit = counts.get("当たり", 0) + 0.5 * counts.get("一部", 0)
    return {
        "score": round(hit / max(len(items), 1), 3),
        "counts": dict(counts),
        "by_kind": {key: dict(value) for key, value in by_kind.items()},
        "misses": [
            {"number": verdict.number, "verdict": verdict.verdict, "note": verdict.note[:60]}
            for verdict in result.verdicts
            if verdict.verdict in {"食い違い", "触れていない"}
        ],
    }


def run_round(conn, db_path: Path, run: str, round_number: int, split: str, out: Path, cases_path: Path, secret: Path | None, baselines: bool, all_cuts: bool) -> dict:
    """1回の答え合わせ。全出力を残す。"""
    cases = [case for case in json.loads(cases_path.read_text(encoding="utf-8")) if split == "all" or case["split"] == split]
    calls_before = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM ai_calls").fetchone()[0]
    jobs = []
    for case in cases:
        cuts = eligible_cuts(case["date"]) if all_cuts else [c for c in [latest_cut(case["date"])] if c]
        jobs += [(case, "selfhood", cut) for cut in cuts]
        if baselines and latest_cut(case["date"]):
            jobs += [(case, "bm25", latest_cut(case["date"])), (case, "model", latest_cut(case["date"]))]

    def work(job):
        case, method, cut = job
        local = db.connect(db_path)
        try:
            if method == "selfhood":
                answer, material = selfhood_answer(local, run, case["question"], cut)
            elif method == "bm25":
                answer, material = bm25_answer(local, case["question"], cut)
            else:
                answer, material = model_answer(local, case["question"], cut)
            return {"case": case["id"], "date": case["date"], "method": method, "cut": cut, "question": case["question"], "answer": answer, **judge(local, case, answer, material)}
        except (ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError, config.MissingApiKeyError) as error:
            return {"case": case["id"], "date": case["date"], "method": method, "cut": cut, "failed": type(error).__name__}
        finally:
            local.close()

    with ThreadPoolExecutor(WORKERS) as pool:
        rows = list(pool.map(work, jobs))
    good = [row for row in rows if "failed" not in row]
    summary: dict = {
        "round": round_number,
        "run": run,
        "split": split,
        "cases": len(cases),
        "answers": len(rows),
        "failed": len(rows) - len(good),
        "by_method": {method: average_scores([row for row in good if row["method"] == method]) for method in {row["method"] for row in good}},
        "selfhood_by_cut": {cut: average_scores([row for row in good if row["method"] == "selfhood" and row["cut"] == cut]) for cut in CUTS},
    }
    # セルフは、基本の2つと同じ「いちばん新しい時点」だけで比べる。古い時点を混ぜると不利になるため
    latest = [row for row in good if row["method"] == "selfhood" and row["cut"] == latest_cut(row["date"])]
    summary["by_method"]["selfhood"] = average_scores(latest)
    # 年を重ねるほど当たるかは、同じ問いで比べる。問いが違うと、問いの難しさの差を見ることになるため
    three = {row["case"] for row in good if row["method"] == "selfhood" and row["cut"] == CUTS[0]} & {
        row["case"] for row in good if row["method"] == "selfhood" and row["cut"] == CUTS[-1]
    }
    summary["growth_same_cases"] = {
        "cases": len(three),
        **{cut: average_scores([row for row in good if row["method"] == "selfhood" and row["cut"] == cut and row["case"] in three]) for cut in CUTS},
    }
    body = selfhood.selfhood_text(conn, run)
    if body and secret and secret.exists():
        summary["bible"] = check_selfhood_against_bible(body, json.loads(secret.read_text(encoding="utf-8")))
    forbidden = forbidden_texts(conn)
    forbidden.pop("_me", None)
    answers_text = "\n".join(row.get("answer", "") for row in good if row["method"] == "selfhood")
    summary["guard"] = {
        "selfhood": guard_findings(body or "", forbidden),
        "answers": guard_findings(answers_text, {"人だけの部屋の言葉": forbidden["人だけの部屋の言葉"]}),
        "type_words": [word for word in _TYPE_WORDS if word in (body or "") or word in answers_text],
        "medical_orders": medical.orders_in(answers_text),
    }
    spoken = conn.execute("SELECT role, model, input_tokens, output_tokens FROM ai_calls WHERE rowid > ?", (calls_before,)).fetchall()
    cost = dict(SPENT)
    for role, model, input_tokens, output_tokens in spoken:
        cost[role] = cost.get(role, 0.0) + config.cost_usd(model, input_tokens, output_tokens)
    summary["cost_usd"] = {key: round(value, 4) for key, value in cost.items()}
    summary["cost_total_usd"] = round(sum(cost.values()), 3)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scores.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    with (out / "answers.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({key: summary[key] for key in ("by_method", "growth_same_cases", "failed")}, ensure_ascii=False))
    print("正解との比べ", summary.get("bible", {}).get("score"), summary.get("bible", {}).get("counts"))
    print("守り", summary["guard"])
    print("費用", summary["cost_total_usd"])
    return summary


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="セルフの答え合わせ。")
    parser.add_argument("database", type=Path)
    parser.add_argument("action", choices=("build-cases", "round"))
    parser.add_argument("--bible", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--secret", type=Path)
    parser.add_argument("--cases", type=Path, default=config.ROOT / "eval" / "selfhood_cases.json")
    parser.add_argument("--run")
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--baselines", action="store_true")
    parser.add_argument("--all-cuts", action="store_true")
    args = parser.parse_args(argv)
    try:
        selfhood.refuse_real_records(args.database)
    except selfhood.RealRecordsRefused as error:
        print(str(error), file=sys.stderr)
        return 1
    db.init_db(args.database, seed_demo=False)
    conn = db.connect(args.database)
    try:
        if args.action == "build-cases":
            build_cases(conn, args.bible, args.out, args.secret)
        else:
            run_round(conn, args.database, args.run, args.round, args.split, args.out, args.cases, args.secret, args.baselines, args.all_cuts)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
