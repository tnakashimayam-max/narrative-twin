"""専門職とツインのチャット。

役割：専門職の問いに、意思モデルの要素を選び、根拠の本人の言葉を記録の本文のまま届ける。
4つの極：4. 10年後に代弁する。
守る原則：2（今の様子 → 本人の言葉 → AIの推測の順）、3（出どころを分ける）、9（本人の言葉は一字も変えない。
          記録がないことは「まだ聞けていません」）、10（根拠が見えない要素は使わない）、
          11（推定は別の枠に、根拠、反対の根拠、確からしさ、確かめ方を付ける）、12（本人になりきらない）。

AI の呼び出しは1回で、AI は要素の番号を選ぶだけにする。本人の言葉の本文を AI に渡さず、受け取らない。
引用はプログラムが選ばれた要素の根拠から決める。AI の出力から本人の言葉が作られる道がないため、言い換えは起きない。
要素の本文は AI の読み取りなので、本人の言葉としては出さない。

推論で読むのは意思モデルの上限までで、記録が増えても読む量はほぼ変わらない（model_store.for_reading）。

原則に触れる頼み（なりきり、言い換え、健康の判断、言い切り）には、決まった1文を添える。断って終わりにせず、
通常の処理も流す。出力の道が安全なので、語の一覧の拾いすぎは、1文が余分に付くだけで害が小さい。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（app/config.py）、
プロンプトキャッシュ（意思モデルのブロック）。

関連するテスト：tests/test_advocacy.py
全体の説明：docs/architecture.md、決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import ai, clock, config, consent, db, devlog, handover, ledger, model_store, observations, prompts, twin
from app.auth import NOT_FOUND, current_user, get_conn
from app.records import order_for_display
from app.schemas import (
    ConfirmStatus,
    InferenceKind,
    ModelElement,
    RequestKind,
    Role,
    Source,
    Statement,
    User,
    _twin_voice,
)

router = APIRouter(prefix="/api/advocacy")

NOT_YET = "まだ聞けていません。"
MAX_QUESTION_CHARS = 2000
LIMITS = config.MODEL_LIMITS
_AI_ERRORS = (config.MissingApiKeyError, ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError)
AI_FAILED_MESSAGE = "いま、うまくお答えできませんでした。少し時間をおいて、もう一度お試しください。"

# 原則に触れる頼みの語。AI の判定に重ねる。語の一覧だけで見分けようとはしない。
_REQUEST_WORDS: dict[RequestKind, tuple[str, ...]] = {
    RequestKind.IMPERSONATE: ("なりきって", "なりきり", "本人になって", "本人として答え", "本人のつもりで", "さんとして答え", "さんになって"),
    RequestKind.REPHRASE: ("言い換えて", "言い直して", "書き換えて", "書き直して", "言葉を作って", "要約して本人"),
    RequestKind.HEALTH: ("病気かどうか", "病名", "診断して", "認知症か", "薬をやめ", "治療を判断", "判断して"),
    RequestKind.ASSERT: ("言い切って", "断定して", "言い切れ", "記録にないこと"),
}
_NOTICES = {
    RequestKind.IMPERSONATE: "ツイン（AI）は本人になりきりません。記録にある言葉を、そのまま届けます。",
    RequestKind.REPHRASE: "本人の言葉は言い換えません。記録の本文のまま届けます。",
    RequestKind.HEALTH: "健康や病気の判断はしません。お医者さんや看護師さんにご相談ください。",
    RequestKind.ASSERT: "記録にないことは言い切りません。分かる範囲をお伝えします。",
}
_ORDER = [RequestKind.NORMAL, RequestKind.ASSERT, RequestKind.REPHRASE, RequestKind.HEALTH, RequestKind.IMPERSONATE]

_LEAD_MAX_CHARS = 80
_HEDGED_END = re.compile(r"(かもしれません|かも知れません)[。！!]?$")
_SELF_SOURCES = {Source.SELF_WORDS, Source.SELF_CORRECTION}
_TESTIMONY_SOURCES = {Source.FAMILY_TESTIMONY, Source.CONNECTED_TESTIMONY}
_CONFIRMED_LABELS = {
    ConfirmStatus.CONFIRMED: "本人が確かめた",
    ConfirmStatus.CORRECTED: "本人が直した",
    ConfirmStatus.UNCONFIRMED: "まだ確かめていない",
}


class AskInput(BaseModel):
    """専門職の問いの入力。client_key は画面が作る文字列で、同じものを送り直しても答えは増えない。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=MAX_QUESTION_CHARS * 2)
    client_key: str = Field(min_length=1, max_length=64)


def request_kind_from_words(question: str) -> RequestKind:
    for kind, words in _REQUEST_WORDS.items():
        if any(word in question for word in words):
            return kind
    return RequestKind.NORMAL


def stronger_kind(first: RequestKind, second: RequestKind) -> RequestKind:
    return max(first, second, key=_ORDER.index)


def usable_lead(lead: str | None) -> str | None:
    """AI の短い案内を確かめる。通らなければ捨てる。答えに関わらないので、やり直さない。"""
    if not lead:
        return None
    text = lead.strip()
    if not text or len(text) > _LEAD_MAX_CHARS or "「" in text or "『" in text or "おっしゃ" in text:
        return None
    if any(word in text for word in twin._NOT_TO_ASK):
        return None
    try:
        _twin_voice(text)
    except ValueError:
        return None
    return text


def _date_words(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def _speaker_name(conn: sqlite3.Connection, statement: Statement) -> str:
    if statement.speaker_is_twin:
        return "ツイン（AI）"
    user = db.get_user(conn, statement.speaker_user_id)
    return user.display_name if user else "はずした人"


def statement_view(conn: sqlite3.Connection, statement: Statement, self_name: str) -> dict:
    """言明を画面に返す形にする。本文は記録のまま。前置きはプログラムが付ける。"""
    day = _date_words(statement.effective_date)
    if statement.source is Source.SELF_WORDS:
        lead_in = f"{day}、{self_name}はこうおっしゃいました"
    elif statement.source is Source.SELF_CORRECTION:
        lead_in = f"{day}、{self_name}はこう直されました"
    elif statement.source in _TESTIMONY_SOURCES:
        lead_in = f"{day}、{_speaker_name(conn, statement)}のお話です"
    else:
        lead_in = f"{day}の AI の推測です"
    return {
        "statement_id": statement.id,
        "body": statement.body,
        "lead_in": lead_in,
        "date": statement.effective_date.isoformat(),
        "source": statement.source.value,
        "speaker": _speaker_name(conn, statement),
        "confirm": _CONFIRMED_LABELS.get(statement.confirm_status, statement.confirm_status.value),
    }


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"[\s、。！？!?「」（）()]", "", text)
    return {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}


def _closeness(statement: Statement, words: set[str]) -> int:
    """要素の本文や問いと、言明の文字の並びがどれだけ重なるかを数える。選ばれた要素の根拠の中だけで使う。"""
    return len(_bigrams(statement.body) & words)


def _evidence_heads(
    elements: list[ModelElement], heads: dict[int, Statement], scopes, question: str
) -> list[Statement]:
    """選ばれた要素の根拠のうち、見える先頭の行を、要素ごとに関係の近い順に集める。"""
    chosen: dict[int, Statement] = {}
    for element in elements:
        words = _bigrams(element.body) | _bigrams(question)
        candidates = [
            heads[number]
            for number in element.evidence_statement_ids
            if number in heads
            and heads[number].share_scope in scopes
            and heads[number].confirm_status is not ConfirmStatus.RESTING
        ]
        candidates.sort(
            key=lambda item: (_closeness(item, words), item.confirm_status is not ConfirmStatus.UNCONFIRMED, item.effective_date),
            reverse=True,
        )
        taken = {Source.SELF_WORDS: 0, Source.SELF_CORRECTION: 0}
        for candidate in candidates:
            if candidate.source in _SELF_SOURCES:
                if taken[candidate.source] >= LIMITS["quotes_per_element"]:
                    continue
                taken[candidate.source] += 1
            chosen.setdefault(candidate.id, candidate)
    return list(chosen.values())


def _sections(conn: sqlite3.Connection, picked: list[Statement], self_name: str) -> dict:
    """出どころごとに分けて、上限まで並べる（原則2、3）。"""
    ordered = order_for_display(picked)
    self_words = [item for item in ordered if item.source in _SELF_SOURCES][: LIMITS["quotes_total"]]
    testimony = [item for item in ordered if item.source in _TESTIMONY_SOURCES][: LIMITS["testimonies"]]
    guesses = [item for item in ordered if item.source is Source.AI_GUESS][: LIMITS["guesses"]]
    return {
        "self_words": [statement_view(conn, item, self_name) for item in self_words],
        "testimony": [statement_view(conn, item, self_name) for item in testimony],
        "guesses": [statement_view(conn, item, self_name) for item in guesses],
    }


def _confidence_words(value: float) -> str:
    if value >= 0.5:
        return "中くらい"
    if value >= 0.25:
        return "低い"
    return "とても低い"


def _materials(
    elements: list[ModelElement], heads: dict[int, Statement], scopes, question: str, conn: sqlite3.Connection
) -> tuple[list[Statement], list[str]]:
    """推定の材料を、上限まで番号つきで作る。本人の言葉を先に、証言と分かるように並べる。"""
    picked = _evidence_heads(elements, heads, scopes, question)
    ordered = order_for_display(picked)[: LIMITS["estimate_statements"]]
    lines = [
        f"[{number}] {item.effective_date.isoformat()}／{item.source.value}／{_speaker_name(conn, item)}：{item.body}"
        for number, item in enumerate(ordered, start=1)
    ]
    return ordered, lines


def _usable_estimate(draft, materials: list[Statement]) -> dict | None:
    """推定の案を確かめる。言い切りと、渡していない番号の根拠を通さない。根拠がなければ枠を出さない。"""
    if not _HEDGED_END.search(draft.estimate.strip()):
        return None
    try:
        _twin_voice(draft.estimate)
    except ValueError:
        return None
    if any(word in draft.estimate for word in ("病気", "病名", "認知症", "診断")):
        return None

    def pick(numbers: list[int]) -> list[Statement]:
        seen: dict[int, Statement] = {}
        for number in numbers:
            if 1 <= number <= len(materials):
                seen.setdefault(materials[number - 1].id, materials[number - 1])
        return list(seen.values())

    evidence = pick(draft.evidence_numbers)
    counter = [item for item in pick(draft.counter_numbers) if item.id not in {e.id for e in evidence}]
    if not evidence:
        return None
    return {"text": draft.estimate.strip(), "evidence": evidence, "counter": counter, "draft": draft}


def _log_inference(conn, kind: InferenceKind, question: str, view: dict, elements, statements, result) -> int:
    with ledger.atomic(conn):
        return db.insert_row(
            conn,
            "inference_logs",
            {
                "kind": kind.value,
                "question": question,
                "result": json.dumps(view, ensure_ascii=False),
                "element_ids": [element.id for element in elements],
                "statement_ids": statements,
                "model": result.model if result else None,
                "input_tokens": result.input_tokens if result else None,
                "output_tokens": result.output_tokens if result else None,
                "created_at": clock_iso(),
            },
        )


def clock_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _log_call(conn, role: str, result, target: str) -> None:
    db.log_ai_call(
        conn,
        role=role,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        target=target,
        seconds=result.seconds,
        cache_read_tokens=result.cache_read_tokens,
        cache_write_tokens=result.cache_write_tokens,
    )


def _remember_question(conn: sqlite3.Connection, question: str) -> None:
    """まだ聞けていない問いを質問の記録に残す。健康の語がある問いと、よく似た問いは残さない。"""
    if any(word in question for word in twin._NOT_TO_ASK):
        return
    rows = conn.execute("SELECT question FROM question_logs ORDER BY id DESC LIMIT 200").fetchall()
    if twin.memory.is_repeated(question, [row["question"] for row in rows]):
        return
    ledger.add_pending_question(conn, question=question)


def answer(conn: sqlite3.Connection, viewer: User, question: str, client=None) -> dict:
    """問いに答える。AI の呼び出しは要素を選ぶ1回と、必要なときの推定の1回だけ。"""
    started = time.monotonic()
    self_user = db.self_user(conn)
    self_name = self_user.display_name if self_user else "本人"
    scopes = consent.visible_scopes(conn, viewer)
    heads = ledger.head_map(conn)
    visible = model_store.visible_elements(conn, viewer)
    reading = model_store.for_reading(visible, heads)
    lines = [model_store.describe(element, number, heads) for number, element in enumerate(reading, start=1)]
    model_text = prompts.twin_answer_model_text(lines)
    result = ai.pick_elements(
        system=prompts.twin_answer_system(self_name), model_text=model_text, question=question, client=client
    )
    _log_call(conn, "ツインの答え", result, "専門職の問い")
    pick = result.value

    def chosen(numbers: list[int]) -> list[ModelElement]:
        seen: dict[int, ModelElement] = {}
        for number in numbers:
            if 1 <= number <= len(reading):
                seen.setdefault(reading[number - 1].id, reading[number - 1])
        return list(seen.values())[: LIMITS["selected_elements"]]

    direct = chosen(pick.direct_numbers)
    related = [element for element in chosen(pick.related_numbers) if element.id not in {d.id for d in direct}]
    kind = stronger_kind(pick.request_kind, request_kind_from_words(question))
    picked = _evidence_heads(direct, heads, scopes, question)
    sections = _sections(conn, picked, self_name)
    has_self_words = bool(sections["self_words"])
    view: dict = {
        "question": question,
        "notice": _NOTICES.get(kind),
        "lead": usable_lead(pick.lead),
        # いちばん上に今の様子を出す（原則2）。問いに関係なく、いつも入れる
        "now": observations.now_view(conn, viewer, LIMITS["now"]),
        **sections,
        "not_yet": None if has_self_words else NOT_YET,
        "estimate": None,
        "elements": [
            {
                "element_id": element.id,
                "body": element.body,
                "kind": element.kind.value,
                "unit": element.unit.value,
                "confirmed": model_store.confirmed_by_self(element, heads),
            }
            for element in direct
        ],
    }
    statements = [item["statement_id"] for key in ("self_words", "testimony", "guesses") for item in sections[key]]
    answer_log = _log_inference(conn, InferenceKind.TWIN_ANSWER, question, view, direct, statements, result)
    estimate_log = None
    if not has_self_words:
        _remember_question(conn, question)
        estimate_log = _estimate(conn, view, question, [*direct, *related], heads, scopes, self_name, client)
    devlog.record(
        "専門職の問い",
        outcome=devlog.SUCCESS if has_self_words else devlog.NOT_YET,
        ms=devlog.since(started),
        question_chars=len(question),
        model_chars=len(model_text),
    )
    return {"view": view, "answer_log": answer_log, "estimate_log": estimate_log}


def _estimate(conn, view: dict, question: str, elements, heads, scopes, self_name: str, client) -> int | None:
    """推定の枠を作る。材料がなければ呼ばない。作れなくても答えは返す。"""
    if not elements:
        return None
    materials, lines = _materials(elements, heads, scopes, question, conn)
    if not materials:
        return None
    try:
        result = ai.estimate_will(
            system=prompts.estimate_system(self_name), text=prompts.estimate_user_text(question, lines), client=client
        )
    except _AI_ERRORS:
        return None
    _log_call(conn, "推定意思", result, "専門職の問い")
    usable = _usable_estimate(result.value, materials)
    if usable is None:
        return None
    draft = usable["draft"]
    view["estimate"] = {
        "text": usable["text"],
        "evidence": [statement_view(conn, item, self_name) for item in usable["evidence"]],
        "counter": [statement_view(conn, item, self_name) for item in usable["counter"]],
        "confidence": _confidence_words(draft.confidence),
        "how_to_confirm": draft.how_to_confirm.strip(),
    }
    statements = [item.id for item in [*usable["evidence"], *usable["counter"]]]
    return _log_inference(conn, InferenceKind.ESTIMATED_WILL, question, view["estimate"], elements, statements, result)


def _require_supporter(conn: sqlite3.Connection, viewer: User) -> None:
    if not observations.can_use_supporter_screen(conn, viewer):
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def _turn_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["answer_log_id"],)).fetchone()
    view = json.loads(log["result"])
    if row["estimate_log_id"]:
        estimate = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["estimate_log_id"],)).fetchone()
        view["estimate"] = json.loads(estimate["result"])
    return {"id": row["id"], "created_at": row["created_at"], **view}


def _existing_turn(conn: sqlite3.Connection, viewer: User, client_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM advocacy_turns WHERE asker_user_id = ? AND client_key = ?", (viewer.id, client_key)
    ).fetchone()


@router.post("/ask")
def ask(
    body: AskInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """専門職の問いに答える。同じ client_key で送り直しても、答えは増えない。"""
    _require_supporter(conn, viewer)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="聞きたいことを入れてください。")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=400, detail=f"{MAX_QUESTION_CHARS}字までで聞いてください。")
    existing = _existing_turn(conn, viewer, body.client_key)
    if existing is not None:
        return {"turn": _turn_view(conn, existing)}
    # 2回押しで AI を2回呼ばないよう、同じ人の問いは順に処理する
    with request.app.state.advocacy_lock:
        existing = _existing_turn(conn, viewer, body.client_key)
        if existing is not None:
            return {"turn": _turn_view(conn, existing)}
        try:
            outcome = answer(conn, viewer, question)
        except _AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
        now = clock.now(request.app).isoformat()
        with ledger.atomic(conn):
            db.insert_row(
                conn,
                "advocacy_turns",
                {
                    "asker_user_id": viewer.id,
                    "client_key": body.client_key,
                    "answer_log_id": outcome["answer_log"],
                    "estimate_log_id": outcome["estimate_log"],
                    "created_at": now,
                },
            )
    return {"turn": _turn_view(conn, _existing_turn(conn, viewer, body.client_key))}


@router.get("/turns")
def list_turns(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人の問いと答えを、古い順に返す。ほかの人のやり取りは返さない。"""
    _require_supporter(conn, viewer)
    rows = conn.execute(
        "SELECT * FROM advocacy_turns WHERE asker_user_id = ? ORDER BY rowid", (viewer.id,)
    ).fetchall()
    return {"items": [_turn_view(conn, row) for row in rows]}


@router.get("/overview")
def overview(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """画面のいちばん上に出す、今の様子と、本人が範囲を決めた日を返す。"""
    _require_supporter(conn, viewer)
    themes = handover.handover_themes(conn)
    self_user = db.self_user(conn)
    return {
        "self_name": self_user.display_name if self_user else "本人",
        "now": observations.now_view(conn, viewer, LIMITS["now"]),
        "decided_on": max(themes.values()).isoformat() if themes else None,
        "themes": [theme.value for theme in themes],
    }


@router.get("/elements")
def list_elements(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """見える意思モデルの要素を返す。花びらは専門職にだけ返す。"""
    _require_supporter(conn, viewer)
    heads = ledger.head_map(conn)
    elements = model_store.for_reading(model_store.visible_elements(conn, viewer), heads)
    show_petals = viewer.role is Role.PROFESSIONAL
    return {
        "items": [
            {
                "element_id": element.id,
                "kind": element.kind.value,
                "unit": element.unit.value,
                "body": element.body,
                "condition": element.condition,
                "confirmed": model_store.confirmed_by_self(element, heads),
                "valid_from": element.valid_from.isoformat(),
                "valid_to": element.valid_to.isoformat() if element.valid_to else None,
                **({"petals": [petal.value for petal in element.petals]} if show_petals else {}),
            }
            for element in elements
        ]
    }


@router.get("/elements/{element_id}")
def read_element(
    element_id: int, viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """要素の根拠を、言明と発言の原文までたどる。見えない要素は、ない要素と同じ 404 を返す。"""
    _require_supporter(conn, viewer)
    element = next((item for item in model_store.visible_elements(conn, viewer) if item.id == element_id), None)
    if element is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    heads = ledger.head_map(conn)
    self_user = db.self_user(conn)
    self_name = self_user.display_name if self_user else "本人"
    evidence = []
    for number in element.evidence_statement_ids:
        head = heads[number]
        utterances = [ledger.get_utterance(conn, value) for value in head.source_utterance_ids]
        evidence.append(
            {
                **statement_view(conn, head, self_name),
                "utterances": [
                    {"utterance_id": item.id, "text": item.text, "spoken_at": item.spoken_at.isoformat()}
                    for item in utterances
                    if item is not None
                ],
            }
        )
    return {"element_id": element.id, "body": element.body, "kind": element.kind.value, "evidence": evidence}
