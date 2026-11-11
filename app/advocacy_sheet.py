"""1枚の紙。ライフサポート手帳とケアプラン原案を1枚にしたもの。

役割：ツインの代弁を真ん中に置き、裏付けの本人の言葉、ケアプラン原案、会議で本人が言った言葉を1枚にまとめる。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）、10（材料は見える範囲の関数から取る）、
          12（主語はツイン（AI）で、本人になりきらない）、15（ツインのいない部屋の発言は AI に送らない）。

話題ごとに材料を集めて、ツインの文を1回で作る。材料の集め方は app/advocacy_voice.py と同じ。
会議の言葉は AI を通さず、部屋の発言をプログラムがそのまま載せる。読むたびに部屋から取り直すので、紙を作り直さなくても
会議のあとの言葉が載る。田中さんの問いかけと組にするのは、「同じたい」のような返事だけでは意味が読めないため。
「大切なもの」の欄も、その言葉を含む問いへの本人の返事をプログラムがそのまま載せる。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（app/config.py）、
プロンプトキャッシュ（意思モデルのブロック）。

関連するテスト：tests/test_advocacy_sheet.py
API の取り決め：docs/api.md
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import advocacy, advocacy_prompts, advocacy_voice, ai, clock, config, db, ledger, observations, professions, room_store
from app.auth import NOT_FOUND, current_user, get_conn
from app import made_documents
from app.schemas import MadeDocumentKind, EvidenceVerdict, InferenceKind, MessageKind, SheetDraft, Source, Statement, User

router = APIRouter(prefix="/api/advocacy")

MEETING_ROOM = "ケア会議"
TREASURE_WORD = "大切なもの"
TASK = "ケア会議の机に置く1枚の紙を書いてください。"
# 話題ごとに材料を集める。「ご飯」と「風呂」のように、1つの問いでは同時に近づけない話題があるため
TOPICS = (
    "この家で暮らすこと、施設、最期を迎える場所",
    "ご飯、炊きたて、台所、食べること",
    "風呂、長風呂、一人の時間",
    "人の手を借りること、他人が家に入ること、ヘルパー",
    "美穂さんと家族への思い、迷惑や負担のこと",
    "和子さん、庭、大切にしているもの",
)
AI_FAILED_MESSAGE = "いま、紙を作れませんでした。少し時間をおいて、もう一度お試しください。"
LABEL = "真ん中の文はツイン（AI）の代弁で、推測を含みます。引いた本人の言葉には、日付を付けています。"


class SheetInput(BaseModel):
    """1枚の紙の入力。again は、前の紙を残したまま作り直すときに使う。"""

    model_config = ConfigDict(extra="forbid")

    again: bool = False
    meeting_room_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)


def _meeting_room_id(conn: sqlite3.Connection, viewer: User, room_id: int | None) -> int | None:
    """読む会議の部屋を決める。入っていない部屋は読まない。指定がなければ、入っている「ケア会議」の新しいほうを読む。"""
    if room_id is not None:
        if room_store.get_room(conn, room_id) is None or not room_store.is_member(conn, room_id, viewer.id):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return room_id
    rows = conn.execute("SELECT id FROM rooms WHERE name = ? ORDER BY rowid DESC", (MEETING_ROOM,)).fetchall()
    return next((row["id"] for row in rows if room_store.is_member(conn, row["id"], viewer.id)), None)


def meeting_pairs(conn: sqlite3.Connection, room_id: int | None) -> list[dict]:
    """部屋の発言から、問いかけと本人の返事の組を作る。ツインの発言は載せない。本文は部屋の行のまま。"""
    self_user = db.self_user(conn)
    if room_id is None or self_user is None:
        return []
    pairs: list[dict] = []
    asked: dict | None = None
    for message in room_store.messages_range(conn, room_id, 0, -1, None):
        # ツインが入った、はずれたのお知らせは、入れた人の発言として残っているので、種類で外す
        if message["kind"] != MessageKind.SAY.value or message["is_twin"] or not message["text"]:
            continue
        if message["sender_user_id"] == self_user.id:
            pairs.append({"asked": asked, "answer": {"text": message["text"], "sent_at": message["sent_at"]}})
            asked = None
        else:
            speaker = db.get_user(conn, message["sender_user_id"])
            asked = {"speaker": speaker.display_name if speaker else "はずした人", "text": message["text"]}
    return pairs


def _treasure(pairs: list[dict]) -> dict | None:
    """「大切なもの」を聞いた問いへの、本人の最初の返事を返す。AI に選ばせないので、本文のまま載る。"""
    for pair in pairs:
        if pair["asked"] and TREASURE_WORD in pair["asked"]["text"]:
            return pair["answer"]
    return None


def _meeting_lines(conn: sqlite3.Connection, room_id: int | None, pairs: list[dict]) -> list[str]:
    """AI に渡す会議の行を作る。ツインのいない部屋の発言は AI に送らない（原則15）。"""
    if room_id is None or room_store.active_presence(conn, room_id) is None:
        return []
    lines = []
    for pair in pairs:
        if pair["asked"]:
            lines.append(f"{pair['asked']['speaker']}：{pair['asked']['text']}")
        lines.append(f"本人：{pair['answer']['text']}")
    return lines


def _verdict(statements: list[Statement]) -> EvidenceVerdict:
    """原案の根拠の出どころから、判定を決める。AI に判定させない。"""
    sources = {item.source for item in statements}
    if sources & {Source.SELF_WORDS, Source.SELF_CORRECTION}:
        return EvidenceVerdict.SELF_WORDS
    if sources & {Source.FAMILY_TESTIMONY, Source.CONNECTED_TESTIMONY}:
        return EvidenceVerdict.TESTIMONY_ONLY
    if Source.AI_GUESS in sources:
        return EvidenceVerdict.AI_ONLY
    return EvidenceVerdict.NO_EVIDENCE


def _spread(items: list[dict], limit: int) -> list[dict]:
    """古い順の言葉から、最初と最後を残して上限まで選ぶ。変わり方が分かるようにするため。"""
    if len(items) <= limit:
        return items
    if limit < 2:
        return items[:limit]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(index * step)] for index in range(limit)]


def build_view(conn: sqlite3.Connection, draft: SheetDraft, statements: list[Statement]) -> dict:
    """AI の出力を紙の形にする。本文は記録から入れる。"""
    self_name = advocacy_voice._self_name(conn)

    def quotes(numbers: list[int]) -> list[dict]:
        return [advocacy.statement_view(conn, item, self_name) for item in advocacy_voice.pick_quotes(numbers, statements)]

    plan_statements = advocacy_voice.pick_quotes(draft.plan_evidence_numbers, statements)
    return {
        "label": LABEL,
        "self_name": self_name,
        "claim": draft.claim.strip(),
        "topics": [
            {
                "title": topic.title.strip(),
                "text": topic.text.strip(),
                "evidence": quotes(topic.evidence_numbers)[: config.MODEL_LIMITS["advocacy_quotes_per_reason"]],
            }
            for topic in draft.topics
        ],
        "change": (
            {
                "text": draft.change.text.strip(),
                "evidence": _spread(quotes(draft.change.evidence_numbers), config.MODEL_LIMITS["sheet_change_quotes"]),
            }
            if draft.change
            else None
        ),
        "plan": {
            "self_intent": draft.self_intent.strip(),
            "long_term_goal": draft.long_term_goal.strip(),
            "evidence": quotes(draft.plan_evidence_numbers)[: config.MODEL_LIMITS["sheet_plan_quotes"]],
            # 原案の表には根拠を全部残す。あとからたどれるようにするため。応答には本文を入れない
            "evidence_ids": [item.id for item in plan_statements],
            "verdict": _verdict(plan_statements).value,
        },
        "requests": [item.strip() for item in draft.requests],
        "ask_back": draft.ask_back,
    }


def _used_statement_ids(view: dict) -> list[int]:
    ids = {quote["statement_id"] for topic in view["topics"] for quote in topic["evidence"]}
    if view["change"]:
        ids.update(quote["statement_id"] for quote in view["change"]["evidence"])
    ids.update(quote["statement_id"] for quote in view["plan"]["evidence"])
    return sorted(ids)


def _save_plan(conn: sqlite3.Connection, view: dict, element_ids: list[int], day) -> None:
    """ケアプラン原案を原案の表に残す。前の版を指す。"""
    last = conn.execute("SELECT id FROM care_plan_drafts ORDER BY id DESC LIMIT 1").fetchone()
    conn.execute(
        """
        INSERT INTO care_plan_drafts (self_intent, long_term_goal, evidence_statement_ids, evidence_element_ids,
            verdict, created_on, previous_version_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            view["plan"]["self_intent"],
            view["plan"]["long_term_goal"],
            json.dumps(view["plan"].get("evidence_ids") or [quote["statement_id"] for quote in view["plan"]["evidence"]]),
            json.dumps(element_ids),
            view["plan"]["verdict"],
            day.isoformat(),
            last["id"] if last else None,
        ),
    )


def _latest_row(conn: sqlite3.Connection, viewer: User) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM advocacy_sheets WHERE asker_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.SHEET)} ORDER BY rowid DESC LIMIT 1", (viewer.id,)
    ).fetchone()


def _sheet_view(request: Request, conn: sqlite3.Connection, viewer: User, row: sqlite3.Row, room_id: int | None) -> dict:
    """保存した紙に、今の様子と会議の言葉を足して返す。この2つは読むたびに取り直す。AI は呼ばない。"""
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["log_id"],)).fetchone()
    pairs = meeting_pairs(conn, room_id)
    saved = json.loads(log["result"])
    # 本人の意向の欄の上に載せる本人の言葉。案の根拠になければ、話題の根拠から選ぶ
    topic_quotes = [quote for topic in saved["topics"] for quote in topic["evidence"]]
    saved["plan"]["own_words"] = advocacy_voice.own_words([*saved["plan"]["evidence"], *topic_quotes])
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "made_on": clock.now(request.app).date().isoformat(),
        "deployment_label": request.app.state.deployment.label,
        **saved,
        "decide": advocacy_voice.DECIDE,
        "now": observations.now_view(conn, viewer, config.MODEL_LIMITS["now"]),
        "meeting": pairs,
        "treasure": _treasure(pairs),
    }


@router.post("/sheet")
def make_sheet(
    body: SheetInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """1枚の紙を作る。作ってあれば AI を呼ばずに返す。"""
    advocacy._require_tool(conn, viewer, professions.SHEET)
    room_id = _meeting_room_id(conn, viewer, body.meeting_room_id)
    with request.app.state.voice_lock:
        row = None if body.again else _latest_row(conn, viewer)
        if row is None:
            now = clock.now(request.app)
            try:
                material = advocacy_voice.gather_topics(conn, viewer, TASK, TOPICS)
                system = advocacy_prompts.sheet_system(advocacy_voice._self_name(conn))
                model_text = advocacy_prompts.voice_model_text(material.element_lines)
                text = advocacy_prompts.sheet_user_text(
                    TASK,
                    material.now_lines,
                    material.word_lines,
                    _meeting_lines(conn, room_id, meeting_pairs(conn, room_id)),
                    now.date(),
                )
                result = advocacy_voice.ask_without_medical_orders(
                    conn,
                    lambda extra: ai.write_sheet(system=system, model_text=model_text, text=text + extra),
                    "ツインのアドボカシー",
                    "1枚の紙",
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            view = {**build_view(conn, result.value, material.statements), "reading": advocacy_voice.reading_note(material)}
            log_id = advocacy._log_inference(
                conn, InferenceKind.DRAFT, TASK, view, material.elements, _used_statement_ids(view), result
            )
            with ledger.atomic(conn):
                _save_plan(conn, view, [element.id for element in material.elements], now.date())
                db.insert_row(
                    conn,
                    "advocacy_sheets",
                    {"asker_user_id": viewer.id, "log_id": log_id, "created_at": now.isoformat()},
                )
            row = _latest_row(conn, viewer)
    return {"sheet": _sheet_view(request, conn, viewer, row, room_id)}


@router.get("/sheet")
def read_sheet(
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """いちばん新しい紙を返す。まだなければ空を返す。AI は呼ばない。"""
    advocacy._require_tool(conn, viewer, professions.SHEET)
    row = _latest_row(conn, viewer)
    if row is None:
        return {"sheet": None}
    return {"sheet": _sheet_view(request, conn, viewer, row, _meeting_room_id(conn, viewer, None))}
