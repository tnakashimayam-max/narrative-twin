"""ツインのアドボカシーを、10年分のデータで確かめる道具。

役割：節目の控えを写して、挙げた言葉が届くか、見えてはいけない記録が漏れないか、紙が1枚に収まるかを確かめ、表にして書き出す。
4つの極：4. 10年後に代弁する（確かめるためだけの道具）。
守る原則：8（架空のデータだけを使う）、9（本人の言葉は記録の本文のまま）、10（見える範囲）。

渡されたデータベースには書き込まない。必ず --out の下へ写してから開く。自分用の記録（data/mine）を渡すと止まる。
AI を呼ぶのは --ai を付けたときだけで、付けなければ費用はかからない。サーバーは同じプロセスの中で動かし、紙の見た目を
確かめるときだけ 127.0.0.1 の 8010 番で待ち受ける。

問いと言葉の一覧は eval/advocacy_check.json にある。物語のチャットから、人が書き写さずに作った。

使い方：
  python -m scripts.check_advocacy --db 控えのパス                         AI を呼ばない確認
  python -m scripts.check_advocacy --db 控えのパス --ai --budget 2.5        問い、1枚の紙、ケアプラン案も本物の AI で流す
  python -m scripts.check_advocacy --db 控えのパス --ai --only 1,2         1番目と2番目の問いだけを流す

結果は --out の下の report.md と results.json に書く。

関連するテスト：tests/test_check_advocacy.py
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.routing import Route

from app import ai, config, consent, db, ledger, model_store
from app.main import create_app
from app.schemas import ConfirmStatus, ElementKind, Source

CASES = Path(__file__).resolve().parent.parent / "eval" / "advocacy_check.json"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "trial" / "advocacy_check"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 8010
DEMO_DAY = "2036-05-06"
ASKER = "田中さん"
FAMILY = "美穂さん"
# 1回あたりの費用の見込み（ドル）。1か月分の実測の約3倍で、10年分の実測が出たら直す
ESTIMATE = {"問い": 0.25, "紙": 0.35, "案": 0.45}
MEETING_ROOM = "ケア会議"
_TWIN_TEXT_KEYS = {
    "claim", "text", "answer", "requests", "ask_back", "self_intent", "long_term_goal",
    "summary", "target", "objection", "aim", "support", "why",
}  # fmt: skip
_NOT_TWIN_KEYS = {"evidence", "quote", "now", "meeting", "treasure", "first"}
_MIN_FRAGMENT = 8
_CONFIRMED = {ConfirmStatus.CONFIRMED, ConfirmStatus.CORRECTED}


class RefusedPath(ValueError):
    """開いてはいけない場所を渡されたとき。"""


def refuse_private(path: Path) -> None:
    """自分用の記録は開かない。実在の人のデータなので、確かめの道具からも読まない。"""
    if "mine" in {part.lower() for part in path.resolve().parts}:
        raise RefusedPath(f"自分用の記録は開きません：{path}")


def copy_database(source: Path, out: Path) -> Path:
    """控えを --out の下へ写す。流し込みの途中の書き込みも読めるよう、WAL も一緒に写す。前の確認の写しは上書きする。"""
    refuse_private(source)
    if not source.is_file():
        raise FileNotFoundError(f"データベースがありません：{source}")
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "narrative_twin.db"
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    shutil.copyfile(source, target)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(source) + suffix)
        if extra.exists():
            shutil.copyfile(extra, Path(str(target) + suffix))
    return target


def load_cases(path: Path = CASES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _user_by_name(conn: sqlite3.Connection, name: str):
    return next((user for user in db.list_users(conn) if user.name == name), None)


def check_words(conn: sqlite3.Connection, words: list[dict], asker_name: str = ASKER, family_name: str = FAMILY) -> list[dict]:
    """挙げた言葉が記録にあるか、だれに見えるか、読む要素の根拠に入っているかを調べる。AI は呼ばない。"""
    asker = _user_by_name(conn, asker_name)
    family = _user_by_name(conn, family_name)
    current = ledger.current_statements(conn)
    heads = ledger.head_map(conn)
    seen_by_asker = {item.id for item in consent.visible_statements(conn, asker)} if asker else set()
    seen_by_family = {item.id for item in consent.visible_statements(conn, family)} if family else set()
    evidence: set[int] = set()
    if asker:
        for element in model_store.for_advocacy(model_store.visible_elements(conn, asker), heads):
            for number in element.evidence_statement_ids:
                if number in heads:
                    evidence.add(heads[number].id)
    rows = []
    for word in words:
        found = [item for item in current if word["key"] in item.body and item.effective_date.isoformat() == word["date"]]
        item = found[0] if found else None
        rows.append(
            {
                "date": word["date"],
                "key": word["key"],
                "found": item is not None,
                "source": item.source.value if item else None,
                "confirm": item.confirm_status.value if item else None,
                "asker_sees": any(row.id in seen_by_asker for row in found),
                "family_sees": any(row.id in seen_by_family for row in found),
                "in_model": any(row.id in evidence for row in found),
            }
        )
    return rows


def _fragments(text: str) -> list[str]:
    """文ごとに分ける。短い文は別の話でも重なるので調べない。"""
    return [part.strip() for part in re.split(r"[。！？\n]", text) if len(part.strip()) >= _MIN_FRAGMENT]


def check_hidden(conn: sqlite3.Connection, hidden: list[str], viewers: tuple[str, ...] = (ASKER, FAMILY)) -> list[dict]:
    """「本人だけ」の言葉が、ほかの人に見える記録や要素に出ていないかを調べる。"""
    leaks = []
    fragments = [fragment for line in hidden for fragment in _fragments(line)]
    for name in viewers:
        viewer = _user_by_name(conn, name)
        if viewer is None:
            continue
        bodies = [item.body for item in consent.visible_statements(conn, viewer)]
        bodies += [element.body for element in model_store.visible_elements(conn, viewer)]
        for fragment in fragments:
            if any(fragment in body for body in bodies):
                leaks.append({"viewer": name, "fragment": fragment})
    return leaks


def check_secret_words(conn: sqlite3.Connection, words: list[str]) -> dict[str, int]:
    """人だけの部屋にしか書かない語が、AI の読む記録に入っていないかを数える。"""
    counts = {}
    for word in words:
        total = 0
        for table, column in (("statements", "body"), ("model_elements", "body"), ("utterances", "text")):
            total += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE instr({column}, ?) > 0", (word,)).fetchone()[0]
        counts[word] = total
    return counts


def model_summary(conn: sqlite3.Connection, asker_name: str = ASKER) -> dict:
    """読む要素の量と、10年の変わり方の材料があるかをまとめる。"""
    asker = _user_by_name(conn, asker_name)
    statements = ledger.current_statements(conn)
    confirmed = [item for item in statements if item.confirm_status in _CONFIRMED]
    summary: dict = {
        "statements": len(statements),
        "self_words": sum(item.source is Source.SELF_WORDS for item in statements),
        "confirmed": len(confirmed),
        "last_confirmed_on": max((item.effective_date.isoformat() for item in confirmed), default=None),
        "resting": sum(item.confirm_status is ConfirmStatus.RESTING for item in statements),
    }
    if asker is None:
        return {**summary, "asker_found": False}
    heads = ledger.head_map(conn)
    visible = model_store.visible_elements(conn, asker)
    reading = model_store.for_advocacy(visible, heads)
    return {
        **summary,
        "asker_found": True,
        "elements_visible": len(visible),
        "elements_read": len(reading),
        "chars_read": sum(len(item.body) + len(item.condition or "") for item in reading),
        "by_year": dict(sorted(Counter(item.valid_from.year for item in reading).items())),
        "changes": sum(item.kind is ElementKind.CHANGE for item in reading),
        "confirmed_elements": sum(model_store.confirmed_by_self(item, heads) for item in reading),
    }


def twin_texts(value) -> list[str]:
    """画面に返す形から、ツイン自身の文だけを取り出す。記録の本文は入れない。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _NOT_TWIN_KEYS:
                continue
            if key in _TWIN_TEXT_KEYS and isinstance(item, str):
                found.append(item)
            elif key in _TWIN_TEXT_KEYS and isinstance(item, list) and all(isinstance(text, str) for text in item):
                found.extend(item)
            else:
                found.extend(twin_texts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(twin_texts(item))
    return found


def scan_flags(value, flag_words: list[str], hidden: list[str]) -> dict:
    """ツインの文の中の、気になる語と、「本人だけ」の言葉を探す。止めずに、読む人に知らせるだけにする。"""
    text = "\n".join(twin_texts(value))
    fragments = [fragment for line in hidden for fragment in _fragments(line)]
    return {
        "flag_words": sorted({word for word in flag_words if word in text}),
        "hidden_leaks": [fragment for fragment in fragments if fragment in text],
    }


def _quote_bodies(value) -> list[str]:
    bodies: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "body" and isinstance(item, str):
                bodies.append(item)
            else:
                bodies.extend(_quote_bodies(item))
    elif isinstance(value, list):
        for item in value:
            bodies.extend(_quote_bodies(item))
    return bodies


def word_hits(expect: list[str], turn: dict, material: str, voice: dict | None) -> list[dict]:
    """問いごとに、言葉がどこまで届いたかを分ける。答えに届いたか、ツインが読んだか、主張の根拠に使ったか。"""
    delivered = [item["body"] for key in ("self_words", "testimony") for item in turn.get(key, [])]
    used = _quote_bodies({"reasons": voice.get("reasons", []), "objections": voice.get("objections", [])}) if voice else []
    return [
        {
            "key": key,
            "delivered": any(key in body for body in delivered),
            "read": key in material,
            "used": any(key in body for body in used),
        }
        for key in expect
    ]


class Usage:
    """この確認で使った費用を、処理の記録から計算する。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ai_calls").fetchone()[0]

    def calls(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.role, a.model, a.input_tokens, a.output_tokens, a.seconds,
                   COALESCE(c.cache_read_tokens, 0) AS cache_read, COALESCE(c.cache_write_tokens, 0) AS cache_write
            FROM ai_calls a LEFT JOIN ai_call_cache c ON c.ai_call_id = a.id
            WHERE a.id > ? ORDER BY a.id
            """,
            (self.base,),
        ).fetchall()
        result = []
        for row in rows:
            price = config.PRICE_PER_MTOK.get(row["model"], config.PRICE_PER_MTOK["claude-opus-5-5"])
            cost = (
                row["input_tokens"] * price["input"]
                + row["cache_write"] * price["input"] * 1.25
                + row["cache_read"] * price["input"] * 0.1
                + row["output_tokens"] * price["output"]
            ) / 1_000_000
            result.append({**dict(row), "cost": round(cost, 4)})
        return result

    def total(self) -> float:
        return round(sum(call["cost"] for call in self.calls()), 4)

    def by_role(self) -> list[dict]:
        roles: dict[str, dict] = {}
        for call in self.calls():
            row = roles.setdefault(call["role"], {"role": call["role"], "calls": 0, "seconds": 0.0, "cost": 0.0})
            row["calls"] += 1
            row["seconds"] = round(row["seconds"] + (call["seconds"] or 0), 1)
            row["cost"] = round(row["cost"] + call["cost"], 4)
        return list(roles.values())


@contextmanager
def _capture_advocate(texts: list[str]):
    """ツインに渡した材料を覚える。読んだかどうかを、画面に返らない入力から確かめるため。"""
    original = ai.advocate

    def spy(**kwargs):
        texts.append(kwargs.get("text", ""))
        return original(**kwargs)

    ai.advocate = spy
    try:
        yield
    finally:
        ai.advocate = original


def _headers(user_id: int) -> dict[str, str]:
    return {"X-NT-User": str(user_id)}


def ensure_meeting(client: TestClient, conn: sqlite3.Connection, lines: list[dict]) -> str:
    """写しに会議の部屋がなければ、物語の18行を入れる。ツインのいない部屋なので AI は呼ばない。"""
    if conn.execute("SELECT 1 FROM rooms WHERE name = ?", (MEETING_ROOM,)).fetchone():
        return "ある"
    users = {user.name: user.id for user in db.list_users(conn)}
    if ASKER not in users or "誠一さん" not in users:
        return "人がいないので入れていない"
    members = [users[name] for name in ("誠一さん", FAMILY) if name in users]
    response = client.post(
        "/api/rooms", json={"kind": "グループ", "name": MEETING_ROOM, "member_ids": members}, headers=_headers(users[ASKER])
    )
    response.raise_for_status()
    room_id = response.json()["id"]
    for index, line in enumerate(lines):
        client.post(
            f"/api/rooms/{room_id}/messages",
            json={"text": line["text"], "client_key": f"check-meeting-{index}"},
            headers=_headers(users[line["by"]]),
        ).raise_for_status()
    return "入れた"


def run_question(client, headers: dict, index: int, case: dict, cases: dict, texts: list[str]) -> dict:
    """1つの問いを流す。答えと主張を別々に測る。"""
    started = time.monotonic()
    asked = client.post("/api/advocacy/ask", json={"question": case["question"], "client_key": f"check-{index}"}, headers=headers)
    result: dict = {"question": case["question"], "ask_status": asked.status_code, "ask_seconds": round(time.monotonic() - started, 1)}
    if asked.status_code != 200:
        result["error"] = asked.text[:300]
        return result
    turn = asked.json()["turn"]
    result["not_yet"] = bool(turn["not_yet"])
    result["expect_not_yet"] = bool(case.get("expect_not_yet"))
    result["delivered"] = [f"{item['date']}：{item['body']}" for item in turn["self_words"]]
    result["testimony"] = [f"{item['date']}／{item['speaker']}：{item['body']}" for item in turn["testimony"]]
    result["estimate"] = bool(turn.get("estimate"))
    before = len(texts)
    started = time.monotonic()
    spoken = client.post(f"/api/advocacy/turns/{turn['id']}/advocate", json={}, headers=headers)
    result["voice_status"] = spoken.status_code
    result["voice_seconds"] = round(time.monotonic() - started, 1)
    voice = spoken.json().get("voice") if spoken.status_code == 200 else None
    material = texts[-1] if len(texts) > before else ""
    result["voice"] = voice
    result["hits"] = word_hits(case.get("expect", []), turn, material, voice)
    result["flags"] = scan_flags(voice, cases["flag_words"], cases["hidden"]) if voice else None
    return result


def run_sheet(client, headers: dict, cases: dict) -> dict:
    started = time.monotonic()
    response = client.post("/api/advocacy/sheet", json={"again": True}, headers=headers)
    result: dict = {"status": response.status_code, "seconds": round(time.monotonic() - started, 1)}
    if response.status_code == 200:
        sheet = response.json()["sheet"]
        result["sheet"] = sheet
        result["flags"] = scan_flags(sheet, cases["flag_words"], cases["hidden"])
    else:
        result["error"] = response.text[:300]
    return result


def run_plan(client, headers: dict, cases: dict) -> dict:
    """ケアプラン案を3段流す。第1版は一覧の案で、AI を呼ばない。"""
    result: dict = {"seconds": {}}
    started = time.monotonic()
    made = client.post("/api/advocacy/plans", json={"client_key": "check-plan", "text": cases["plan"]}, headers=headers)
    result["seconds"]["第1版"] = round(time.monotonic() - started, 1)
    if made.status_code != 200:
        return {**result, "error": made.text[:300]}
    plan = made.json()["plan"]
    for name, step in (("異議", "objection"), ("第2版", "revise")):
        started = time.monotonic()
        response = client.post(f"/api/advocacy/plans/{plan['id']}/{step}", json={"again": True}, headers=headers)
        result["seconds"][name] = round(time.monotonic() - started, 1)
        if response.status_code != 200:
            return {**result, "plan": plan, "error": response.text[:300]}
        plan = response.json()["plan"]
    result["plan"] = plan
    result["flags"] = scan_flags({"objection": plan["objection"], "second": plan["second"]}, cases["flag_words"], cases["hidden"])
    return result


def check_one_page(app, user_id: int, out: Path) -> dict:
    """紙を印刷の形にして、ページ数を数える。別の Chrome を画面なしで起動する。Chrome がなければ飛ばす。"""
    if not CHROME.exists():
        return {"skipped": "Chrome が見つかりません"}
    import uvicorn

    async def login(_request) -> HTMLResponse:
        script = f'sessionStorage.setItem("nt_user_id","{user_id}");location.replace("/sheet.html"+location.search);'
        return HTMLResponse(f"<script>{script}</script>")

    app.router.routes.insert(0, Route("/__login", login))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    limit = time.monotonic() + 15
    while not server.started and time.monotonic() < limit and thread.is_alive():
        time.sleep(0.1)
    if not server.started:
        return {"skipped": f"{PORT} 番で起動できませんでした"}
    pdf = out / "sheet.pdf"
    image = out / "sheet_print.png"
    base = [str(CHROME), "--headless=new", "--disable-gpu", "--virtual-time-budget=8000"]
    try:
        subprocess.run(
            [*base, f"--user-data-dir={out / 'chrome_pdf'}", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", f"http://127.0.0.1:{PORT}/__login"],
            timeout=90, check=False, capture_output=True,
        )  # fmt: skip
        subprocess.run(
            [*base, f"--user-data-dir={out / 'chrome_png'}", "--window-size=718,1047", f"--screenshot={image}", f"http://127.0.0.1:{PORT}/__login?print"],
            timeout=90, check=False, capture_output=True,
        )  # fmt: skip
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    if not pdf.exists():
        return {"skipped": "PDF を作れませんでした"}
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))
    return {"pages": pages, "pdf": str(pdf), "image": str(image)}


def _mark(value: bool) -> str:
    return "○" if value else "×"


def build_report(results: dict) -> str:
    """結果を表にする。AI の文は手で整えず、そのまま載せる。"""
    lines = ["# ツインのアドボカシーの確認", "", f"- 控え：`{results['db']}`", f"- 聞く人：{results['asker']}", f"- 費用：${results['spent']}", ""]
    summary = results["model"]
    lines += [
        "## 記録と意思モデル（AI なし）",
        "",
        f"- 言明 {summary['statements']} 件、本人の言葉 {summary['self_words']} 件、今は話したくない {summary['resting']} 件",
        f"- 本人が確かめた言明 {summary['confirmed']} 件、最後は {summary['last_confirmed_on'] or 'まだありません'}",
    ]
    if summary.get("asker_found"):
        lines += [
            f"- 見える要素 {summary['elements_visible']} 件、読む要素 {summary['elements_read']} 件（{summary['chars_read']}字）",
            f"- 読む要素のうち、変化 {summary['changes']} 件、本人が確かめた根拠を持つもの {summary['confirmed_elements']} 件",
            f"- 年ごとの要素：{summary['by_year']}",
        ]
    else:
        lines.append(f"- {results['asker']}がいません。招く前の控えです。")
    lines += ["", "### 挙げた言葉", "", "| 日付 | 言葉 | 記録 | 出どころ | 確かめ | 聞く人に見える | 家族に見える | 要素の根拠 |", "|---|---|---|---|---|---|---|---|"]
    for row in results["words"]:
        lines.append(
            f"| {row['date']} | {row['key']} | {_mark(row['found'])} | {row['source'] or '―'} | {row['confirm'] or '―'} "
            f"| {_mark(row['asker_sees'])} | {_mark(row['family_sees'])} | {_mark(row['in_model'])} |"
        )
    lines += ["", f"- 「本人だけ」の言葉の漏れ：{len(results['hidden_leaks'])} 件"]
    lines += [f"  - {leak['viewer']}：{leak['fragment']}" for leak in results["hidden_leaks"]]
    lines += [f"- AI の読む記録の中の語：{results['secret_words']}", f"- 会議の部屋：{results['meeting']}", ""]
    if results.get("questions"):
        lines += ["## 問い", ""]
    for item in results.get("questions", []):
        lines += [f"### {item['question']}", ""]
        if item.get("skipped"):
            lines += [item["skipped"], ""]
            continue
        lines.append(f"- 答え {item.get('ask_seconds')}秒、ツインの主張 {item.get('voice_seconds')}秒")
        if "not_yet" in item:
            lines.append(f"- まだ聞けていません：{_mark(item['not_yet'])}（期待：{_mark(item['expect_not_yet'])}）、推定の枠：{_mark(item['estimate'])}")
        if item.get("hits"):
            lines += ["", "| 言葉 | 答えに届いた | ツインが読んだ | 主張の根拠 |", "|---|---|---|---|"]
            lines += [f"| {hit['key']} | {_mark(hit['delivered'])} | {_mark(hit['read'])} | {_mark(hit['used'])} |" for hit in item["hits"]]
        voice = item.get("voice")
        if voice:
            lines += ["", "**ツインの主張**", "", f"> {voice['claim']}", ""]
            lines.append(f"- 気になる語：{item['flags']['flag_words']}、「本人だけ」の言葉：{item['flags']['hidden_leaks']}")
            if voice.get("ask_back"):
                lines.append(f"- 聞き返し：{voice['ask_back']}")
        if item.get("error"):
            lines.append(f"- 失敗：{item['error']}")
        lines.append("")
    sheet = results.get("sheet")
    if sheet:
        lines += ["## 1枚の紙", ""]
        if sheet.get("skipped"):
            lines += [sheet["skipped"], ""]
        elif sheet.get("sheet"):
            body = sheet["sheet"]
            lines += [f"- 作成 {sheet['seconds']}秒", f"- 印刷：{sheet.get('page')}", f"- 大切なもの：{body['treasure']}", f"- 会議の言葉：{len(body['meeting'])} 組", "", f"> {body['claim']}", ""]
            if body.get("change"):
                lines += ["**10年の変わり方**", "", f"> {body['change']['text']}", ""]
            lines.append(f"- 気になる語：{sheet['flags']['flag_words']}、「本人だけ」の言葉：{sheet['flags']['hidden_leaks']}")
        else:
            lines.append(f"- 失敗：{sheet.get('error')}")
        lines.append("")
    plan = results.get("plan")
    if plan:
        lines += ["## ケアプラン案", ""]
        if plan.get("skipped"):
            lines += [plan["skipped"], ""]
        elif plan.get("plan") and plan["plan"].get("second"):
            body = plan["plan"]
            lines += [f"- 時間：{plan['seconds']}", f"- 第1版：{body['first']['summary']}", "", f"> {body['objection']['claim']}", ""]
            lines += [f"- 第2版：{body['second']['summary']}", f"- 気になる語：{plan['flags']['flag_words']}、「本人だけ」の言葉：{plan['flags']['hidden_leaks']}"]
        else:
            lines.append(f"- 失敗：{plan.get('error')}")
        lines.append("")
    if results.get("usage"):
        lines += ["## 費用と時間", "", "| 役割 | 回数 | 秒 | 費用 |", "|---|---|---|---|"]
        lines += [f"| {row['role']} | {row['calls']} | {row['seconds']} | ${row['cost']} |" for row in results["usage"]]
        lines.append("")
    return "\n".join(lines)


def _picked_questions(cases: dict, only: str | None) -> list[tuple[int, dict]]:
    numbered = list(enumerate(cases["questions"], start=1))
    if not only:
        return numbered
    wanted = {int(part) for part in only.split(",")}
    return [item for item in numbered if item[0] in wanted]


def run(db_path: Path, out: Path, *, use_ai: bool, budget: float, only: str | None = None, page: bool = True, cases_path: Path = CASES) -> dict:
    """確認を流して、結果を書き出す。AI を呼ぶ段は、見込みの費用を足して上限を超えるなら流さない。"""
    refuse_private(out)
    out.mkdir(parents=True, exist_ok=True)
    cases = load_cases(cases_path)
    work = copy_database(db_path, out)
    app = create_app(work)
    client = TestClient(app)
    conn = db.connect(work)
    usage = Usage(conn)
    # 招く日より前には田中さんが入れないので、写しの中の今日を会議の日にする
    client.post("/api/dev/date", json={"date": DEMO_DAY})
    results: dict = {
        "db": str(db_path),
        "asker": ASKER,
        "meeting": ensure_meeting(client, conn, cases["meeting"]),
        "model": model_summary(conn),
        "words": check_words(conn, cases["words"]),
        "hidden_leaks": check_hidden(conn, cases["hidden"]),
        "secret_words": check_secret_words(conn, cases["secret_words"]),
    }

    def save() -> None:
        results["spent"] = usage.total()
        results["usage"] = usage.by_role()
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "report.md").write_text(build_report(results), encoding="utf-8")

    save()
    asker = _user_by_name(conn, ASKER)
    if use_ai and asker is not None:
        headers = _headers(asker.id)
        results["questions"] = []
        texts: list[str] = []
        with _capture_advocate(texts):
            for index, case in _picked_questions(cases, only):
                if usage.total() + ESTIMATE["問い"] > budget:
                    results["questions"].append({"question": case["question"], "skipped": f"費用の上限のため流していません（ここまで ${usage.total()}）"})
                    continue
                results["questions"].append(run_question(client, headers, index, case, cases, texts))
                save()
        if only is None:
            if usage.total() + ESTIMATE["紙"] > budget:
                results["sheet"] = {"skipped": f"費用の上限のため流していません（ここまで ${usage.total()}）"}
            else:
                results["sheet"] = run_sheet(client, headers, cases)
                if page and results["sheet"].get("sheet"):
                    results["sheet"]["page"] = check_one_page(app, asker.id, out)
            save()
            if usage.total() + ESTIMATE["案"] > budget:
                results["plan"] = {"skipped": f"費用の上限のため流していません（ここまで ${usage.total()}）"}
            else:
                results["plan"] = run_plan(client, headers, cases)
    save()
    conn.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.check_advocacy", description="ツインのアドボカシーを10年分のデータで確かめます。")
    parser.add_argument("--db", type=Path, required=True, help="節目の控えのデータベース。書き込まず、写して使います")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="結果と写しを置く場所")
    parser.add_argument("--ai", action="store_true", help="本物の AI を呼びます。付けなければ費用はかかりません")
    parser.add_argument("--budget", type=float, default=2.5, help="費用の上限（ドル）")
    parser.add_argument("--only", help="流す問いの番号（例：1,2）。指定すると紙と案は流しません")
    parser.add_argument("--no-page", action="store_true", help="紙のページ数を確かめません")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        results = run(args.db, args.out, use_ai=args.ai, budget=args.budget, only=args.only, page=not args.no_page)
    except (RefusedPath, FileNotFoundError) as error:
        print(error)
        return 2
    print(f"結果：{args.out / 'report.md'}")
    print(f"費用：${results['spent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
