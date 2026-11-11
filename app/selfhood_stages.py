"""セルフ生成エンジンの各段。AI を呼ぶ層。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：段1b（やり取りを読む）、段2（仕分ける）、段3（仮説を直す）、段5（全体を照らす。段4を含む）、段6（セルフを書く）、
      段7（確かめる問いを作る）を呼び、プログラムが確かめて、盤面へ追記する。
4つの極：3. 読み解き、積み上げる。
守る原則：3、4（読みは仮説）、5（追記だけ）、10（本人だけの記録を読まない）、15（本人のいない部屋を読まない。読んだ言葉を引かない）。

Claude が担うのは読みと文で、番号と確からしさと状態はプログラムが決める。AI が渡していない番号を返しても捨てる。
モデルの使い分け：回数の多い読みと仕分けは Haiku 5.5、仮説の文は Sonnet 5.5、全体を見る段とセルフは Opus 5.5。理由は app/config.py と docs/tools.md。
再生は前の月の結果を使うので順に流す。朝までの壁時計を優先して Message Batches API は使わない。理由は docs/decisions.md。

関連するテスト：tests/test_selfhood.py
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from app import ai, config, db, selfhood, selfhood_prompts, twin_model
from app.schemas import (
    ConfirmStatus,
    DialogueBatch,
    EvidenceStance,
    EvidenceWeightKind,
    IlluminateResult,
    SelfhoodQuestions,
    SelfhoodWriting,
    PieceStatus,
    ReviseResult,
    SortResult,
    Source,
)

ROLE_DIALOGUE = "やり取りを読む"
ROLE_DIALOGUE_DEEP = "重いやり取りを読む"
ROLE_SORT = "ピースに仕分ける"
ROLE_REVISE = "仮説を直す"
ROLE_ILLUMINATE = "全体を照らす"
ROLE_SELFHOOD = "セルフを書く"
ROLE_QUESTIONS = "確かめる問いを作る"
_CHUNK = 30
_CONTEXT = 3
_SORT_CHUNK = 70
_REVISE_CHUNK = 25
# 読みの確からしさ。本人の発言の読みでも、言葉だけでは分からないことが多いので高くしない
_LOW_INTENT = 0.4
_HIGH_INTENT = 0.5
_NO_SELF_CAUTION = "本人の発言がない記録からの仮説です。本人に確かめていません。"
_HEAVY_WORDS = (*twin_model._LIFE_WORDS, *twin_model._FEELING_WORDS, *twin_model._BODY_WORDS)


@dataclass
class Spend:
    """費用の積み上げ。段ごとに持ち、上限で止める。"""

    limit: float
    by_stage: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(self.by_stage.values())

    def add(self, conn: sqlite3.Connection, role: str, result, target: str) -> None:
        cost = config.cost_usd(result.model, result.input_tokens, result.output_tokens)
        self.by_stage[role] = self.by_stage.get(role, 0.0) + cost
        db.log_ai_call(
            conn, role=role, model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens, target=target
        )

    def check(self) -> None:
        if self.total > self.limit:
            raise BudgetExceeded(f"費用の上限を超えました（${self.total:.2f}）")


class BudgetExceeded(RuntimeError):
    """費用の上限を超えたとき。"""


_AI_ERRORS = (ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError, config.MissingApiKeyError)


def _call(role: str, system: str, text: str, schema, client=None):
    """1回やり直して呼ぶ。失敗は None。1か月の失敗で全部を止めず、記録に残して次へ進む。"""
    for attempt in range(2):
        try:
            return ai.call_structured(role, system=system, content=text, schema=schema, client=client)
        except _AI_ERRORS:
            if attempt == 1:
                return None
    return None


def _name_of(conn: sqlite3.Connection) -> str:
    me = db.self_user(conn)
    return me.display_name if me else "本人"


def _speakers(conn: sqlite3.Connection) -> dict[int, str]:
    return {user.id: user.display_name for user in db.list_users(conn)}


def _unread_dialogues(conn: sqlite3.Connection, period: str | None) -> list[sqlite3.Row]:
    done = {row[0] for row in conn.execute("SELECT message_id FROM dialogue_readings")}
    return [row for row in selfhood.dialogue_rows(conn, period) if row["id"] not in done]


def read_dialogues(conn: sqlite3.Connection, spend: Spend, *, period: str | None = None, workers: int = 6, client=None) -> int:
    """段1b。本人のいる部屋のメッセージを1件ずつ読み、読みの表に足す。言明にしない。"""
    rows = _unread_dialogues(conn, period)
    names = _speakers(conn)
    me = db.self_user(conn)
    by_room: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_room.setdefault(row["room_id"], []).append(row)
    jobs = []
    for messages in by_room.values():
        for start in range(0, len(messages), _CHUNK):
            part = messages[start : start + _CHUNK]
            context = [f"{names.get(item['sender_user_id'], '?')}：{item['text'][:60]}" for item in messages[max(start - _CONTEXT, 0) : start]]
            targets = [f"[{number}] {names.get(item['sender_user_id'], '?')}（{item['sent_at'][:10]}）：{item['text']}" for number, item in enumerate(part, start=1)]
            deep = any(word in item["text"] for item in part for word in _HEAVY_WORDS)
            jobs.append((part, selfhood_prompts.dialogue_text(context, targets), deep))
    system = selfhood_prompts.dialogue_system(me.display_name)

    def run(job):
        part, text, deep = job
        role = ROLE_DIALOGUE_DEEP if deep else ROLE_DIALOGUE
        return part, role, _call(role, system, text, DialogueBatch, client)

    saved = 0
    moment = datetime.now().astimezone().isoformat()
    with ThreadPoolExecutor(workers) as pool:
        for part, role, result in pool.map(run, jobs):
            if result is None:
                spend.failures.append(f"{role}：{part[0]['sent_at'][:10]}")
                continue
            spend.add(conn, role, result, f"やり取り {part[0]['sent_at'][:7]}")
            for reading in result.value.readings:
                if not 1 <= reading.number <= len(part):
                    continue
                message = part[reading.number - 1]
                is_self = message["sender_user_id"] == me.id
                db.insert_row(
                    conn,
                    "dialogue_readings",
                    {
                        "message_id": message["id"],
                        "room_id": message["room_id"],
                        "speaker_user_id": message["sender_user_id"],
                        "effective_date": message["sent_at"][:10],
                        "reading": reading.reading,
                        "petals": reading.petals[:2],
                        "confidence": _HIGH_INTENT if is_self else _LOW_INTENT,
                        "caution": None if is_self else _NO_SELF_CAUTION,
                        "is_added_reply": int(message["room_kind"] == "ツイン"),
                        "model": result.model,
                        "created_at": moment,
                    },
                )
                saved += 1
            conn.commit()
            spend.check()
    return saved


def month_items(conn: sqlite3.Connection, period: str) -> list[dict]:
    """1か月の材料を返す。本文は AI に読ませるが、本人のいない部屋とお休みは入らない。"""
    names = _speakers(conn)
    me = db.self_user(conn)
    heard = selfhood.heard_statement_ids(conn)
    items = []
    for row in selfhood.statement_rows(conn, period):
        reading = selfhood.reading_of(conn, row["id"])
        speaker = names.get(row["speaker_user_id"], "?")
        items.append(
            {
                "table": "statements",
                "id": row["id"],
                "day": row["effective_date"],
                "line": f"{row['effective_date']}／{row['source']}／{speaker}：{row['body'][:160]}" + (f"　読み：{reading[:160]}" if reading else ""),
                "speaker_is_self": row["speaker_user_id"] == me.id,
                "kind": selfhood.weight_kind_of(
                    row["source"], confirmed=row["confirm_status"] == ConfirmStatus.CONFIRMED.value, heard=row["id"] in heard
                ),
            }
        )
    for row in conn.execute("SELECT * FROM dialogue_readings WHERE effective_date LIKE ? ORDER BY effective_date, rowid", (f"{period}%",)):
        message = conn.execute("SELECT text FROM room_messages WHERE id = ?", (row["message_id"],)).fetchone()
        speaker = names.get(row["speaker_user_id"], "?")
        added = bool(row["is_added_reply"])
        items.append(
            {
                "table": "dialogue_readings",
                "id": row["id"],
                "day": row["effective_date"],
                "line": f"{row['effective_date']}／やり取り／{speaker}：{(message['text'] if message else '')[:100]}　読み：{row['reading']}"[:320],
                "speaker_is_self": row["speaker_user_id"] == me.id,
                "kind": selfhood.weight_kind_of("", added=added, dialogue=True),
            }
        )
    return items


def sort_month(conn: sqlite3.Connection, run: str, period: str, last_day: str, spend: Spend, client=None) -> dict[str, list[str]]:
    """段2。読みをピースに仕分け、根拠を足す。ピースごとの新しい根拠の文を返す。"""
    items = month_items(conn, period)
    pieces = selfhood.board(conn, run, last_day)
    touched: dict[str, list[str]] = {}
    system = selfhood_prompts.sort_system(_name_of(conn))
    lines = selfhood.piece_lines(pieces, with_hypotheses=False)
    known = {piece["key"] for piece in pieces}
    for start in range(0, len(items), _SORT_CHUNK):
        part = items[start : start + _SORT_CHUNK]
        text = selfhood_prompts.sort_text(period, lines, [f"[{n}] {item['line']}" for n, item in enumerate(part, start=1)])
        result = _call(ROLE_SORT, system, text, SortResult, client)
        if result is None:
            spend.failures.append(f"{ROLE_SORT}：{period}")
            continue
        spend.add(conn, ROLE_SORT, result, f"仕分け {period}")
        for proposal in result.value.new_pieces[:3]:
            key = _add_proposed_piece(conn, run, proposal, last_day)
            if key and key not in known:
                known.add(key)
        for match in result.value.matches:
            if not (1 <= match.item <= len(part) and 1 <= match.piece <= len(pieces)):
                continue
            item, piece = part[match.item - 1], pieces[match.piece - 1]
            if not selfhood.accepts(piece["key"], item["kind"], item["speaker_is_self"]):
                continue
            selfhood.add_evidence(
                conn,
                run=run,
                key=piece["key"],
                stance=EvidenceStance(match.stance),
                kind=item["kind"],
                source_table=item["table"],
                source_id=item["id"],
                day=item["day"],
            )
            touched.setdefault(piece["key"], []).append(f"（{match.stance}）{item['line']}")
    conn.commit()
    return touched


def _add_proposed_piece(conn: sqlite3.Connection, run: str, proposal, last_day: str) -> str | None:
    """AI が足したいピースを盤面に足す。区画 C と D だけ。数が増えても読む量が増えすぎないよう、上限を持つ。"""
    if not proposal.section.startswith(("C", "D")) or not proposal.name.strip():
        return None
    section = selfhood.SECTION_RELATION if proposal.section.startswith("C") else selfhood.SECTION_STORY
    key = f"{section[0]}/{proposal.person or ''}/{proposal.name.strip()[:30]}"
    count = conn.execute("SELECT COUNT(DISTINCT piece_key) FROM selfhood_pieces WHERE run = ?", (run,)).fetchone()[0]
    if selfhood.latest_version(conn, run, key) or count >= config.SELFHOOD_MAX_PIECES:
        return None
    selfhood.add_piece_version(
        conn,
        run=run,
        key=key,
        section=section,
        name=proposal.name.strip()[:40],
        person=None,
        hypotheses=[],
        standing=selfhood.Standing(PieceStatus.EMPTY, 0.5, 0.0, 0.0),
        replay_date=last_day,
    )
    return key


def revise_month(
    conn: sqlite3.Connection, run: str, period: str, last_day: str, touched: dict[str, list[str]], spend: Spend, client=None
) -> int:
    """段3。当たったピースだけ仮説の文を直し、確からしさと状態はプログラムが計算して、新しい版を足す。"""
    pieces = [piece for piece in selfhood.board(conn, run, last_day) if piece["key"] in touched]
    system = selfhood_prompts.revise_system(_name_of(conn))
    saved = 0
    for start in range(0, len(pieces), _REVISE_CHUNK):
        part = pieces[start : start + _REVISE_CHUNK]
        blocks = []
        for number, piece in enumerate(part, start=1):
            now = "／".join(piece["hypotheses"]) or "（空き）"
            news = "\n".join(touched[piece["key"]][:12])
            blocks.append(f"[{number}] {piece['section']}｜{piece['name']}\n今の仮説（{piece['status']} {piece['confidence']}）：{now}\n新しい根拠：\n{news}")
        result = _call(ROLE_REVISE, system, selfhood_prompts.revise_text(period, blocks), ReviseResult, client)
        rewritten: dict[int, list[str]] = {}
        changed: set[int] = set()
        model = None
        if result is None:
            spend.failures.append(f"{ROLE_REVISE}：{period}")
        else:
            spend.add(conn, ROLE_REVISE, result, f"仮説 {period}")
            model = result.model
            rewritten = {item.number: item.hypotheses for item in result.value.pieces}
            changed = set(result.value.changed_numbers)
        for number, piece in enumerate(part, start=1):
            standing = selfhood.stand(selfhood.evidence_of(conn, run, piece["key"], last_day), _day(last_day), changed=number in changed)
            selfhood.add_piece_version(
                conn,
                run=run,
                key=piece["key"],
                section=piece["section"],
                name=piece["name"],
                person=piece["person"],
                hypotheses=rewritten.get(number) or piece["hypotheses"],
                standing=standing,
                replay_date=last_day,
                model=model,
            )
            saved += 1
    conn.commit()
    return saved


def _day(text: str):
    from datetime import date

    return date.fromisoformat(text)


def illuminate(conn: sqlite3.Connection, run: str, as_of: str, spend: Spend, client=None) -> int:
    """段5（段4を畳んだ）。全体の矛盾、奥の筋、空きの推測を読み、ピースを直す。履歴は消さない。"""
    pieces = selfhood.board(conn, run, as_of)
    result = _call(
        ROLE_ILLUMINATE,
        selfhood_prompts.illuminate_system(_name_of(conn)),
        selfhood_prompts.illuminate_text(as_of, selfhood.piece_lines(pieces)),
        IlluminateResult,
        client,
    )
    if result is None:
        spend.failures.append(f"{ROLE_ILLUMINATE}：{as_of}")
        return 0
    spend.add(conn, ROLE_ILLUMINATE, result, f"全体 {as_of}")
    inferred = set(result.value.inferred_numbers)
    saved = 0
    for item in result.value.pieces:
        if not 1 <= item.number <= len(pieces) or not item.hypotheses:
            continue
        piece = pieces[item.number - 1]
        if item.number in inferred and piece["status"] == PieceStatus.EMPTY.value:
            selfhood.add_evidence(
                conn,
                run=run,
                key=piece["key"],
                stance=EvidenceStance.SUPPORT,
                kind=EvidenceWeightKind.INFERENCE,
                source_table="selfhood_pieces",
                source_id=None,
                day=as_of,
            )
        standing = selfhood.stand(selfhood.evidence_of(conn, run, piece["key"], as_of), _day(as_of))
        selfhood.add_piece_version(
            conn,
            run=run,
            key=piece["key"],
            section=piece["section"],
            name=piece["name"],
            person=piece["person"],
            hypotheses=item.hypotheses,
            standing=standing,
            replay_date=as_of,
            model=result.model,
        )
        saved += 1
    conn.commit()
    return saved


def write_selfhood(conn: sqlite3.Connection, run: str, as_of: str, spend: Spend, client=None, tuning: selfhood.Tuning = selfhood.TUNING) -> str | None:
    """段6。盤面から人物像を書き、新しい版として残す。本人だけの記録は盤面に入っていない。"""
    name = _name_of(conn)
    pieces = selfhood.board(conn, run, as_of, tuning)
    system = selfhood_prompts.selfhood_system(name)
    text = selfhood_prompts.selfhood_text(name, as_of, selfhood.piece_lines(pieces))
    result = _call(ROLE_SELFHOOD, system, text, SelfhoodWriting, client)
    if result is None:
        spend.failures.append(f"{ROLE_SELFHOOD}：{as_of}")
        return None
    spend.add(conn, ROLE_SELFHOOD, result, f"セルフ {as_of}")
    found = selfhood.elapsed_years(result.value.body)
    if found:
        # 経過の年数は書いた時点でしか正しくないので、1回だけ書き直させる（selfhood.elapsed_years の説明）。
        again = _call(ROLE_SELFHOOD, system, text + selfhood_prompts.elapsed_years_note(found), SelfhoodWriting, client)
        if again is not None:
            spend.add(conn, ROLE_SELFHOOD, again, f"セルフ {as_of} 書き直し")
            result = again
        if selfhood.elapsed_years(result.value.body):
            spend.failures.append(f"{ROLE_SELFHOOD}：{as_of} 経過の年数が残った")
    db.insert_row(
        conn,
        "selfhood_documents",
        {
            "run": run,
            "kind": "セルフ",
            "as_of": as_of,
            "body": result.value.body,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "created_at": datetime.now().astimezone().isoformat(),
        },
    )
    conn.commit()
    return result.value.body


def write_questions(conn: sqlite3.Connection, run: str, as_of: str, spend: Spend, client=None) -> list[dict]:
    """段7。聞く価値のある見立てから、確かめる問いの候補を作って残す。本人との会話につなぐのは次の段階。"""
    pieces = selfhood.board(conn, run, as_of)
    worth = [piece for piece in pieces if piece["status"] in {PieceStatus.HYPOTHESIS.value, PieceStatus.WAVERING.value, PieceStatus.INFERRED.value}]
    result = _call(ROLE_QUESTIONS, selfhood_prompts.questions_system(_name_of(conn)), "\n".join(selfhood.piece_lines(worth)), SelfhoodQuestions, client)
    if result is None:
        spend.failures.append(f"{ROLE_QUESTIONS}：{as_of}")
        return []
    spend.add(conn, ROLE_QUESTIONS, result, f"問い {as_of}")
    items = [
        {"piece": worth[item.piece - 1]["key"], "question": item.question, "reason": item.reason}
        for item in result.value.questions
        if 1 <= item.piece <= len(worth)
    ]
    db.insert_row(
        conn,
        "selfhood_documents",
        {
            "run": run,
            "kind": "確かめる問い",
            "as_of": as_of,
            "body": json.dumps(items, ensure_ascii=False),
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "created_at": datetime.now().astimezone().isoformat(),
        },
    )
    conn.commit()
    return items


def replay_month(conn: sqlite3.Connection, run: str, period: str, last_day: str, spend: Spend, log: Callable[[dict], None], client=None) -> None:
    """1か月分を再生する。段ごとの結果を、たどった道として残す。"""
    touched = sort_month(conn, run, period, last_day, spend, client)
    revised = revise_month(conn, run, period, last_day, touched, spend, client)
    log({"period": period, "touched": len(touched), "revised": revised, "spent": round(spend.total, 3)})
    spend.check()


_ = Source  # 出どころの型を、読み手のためにここから参照できるようにする
