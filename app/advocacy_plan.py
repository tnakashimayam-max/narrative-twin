"""ケアプラン案へのツインの異議。

役割：ケアプランの第1版に、ツインが本人の言葉と意思モデルを根拠に異議を唱え、第2版を作る。版を並べて見せる。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、5（前の版を消さない）、9（本人の言葉は記録の本文のまま）、
          10（材料は見える範囲の関数から取る）、12（主語はツイン（AI）で、本人になりきらない）。

処理は3段に分ける。呼ぶ人が1段ずつ進めるので、途中の版も残る。
1. 第1版（役割：原案）：田中さんが入れた案ならそのまま使い、AI を呼ばない。入れなければ、本人の言葉を読まずに一般的な案を作る。
2. 異議（役割：根拠チェック）：材料の集め方は app/advocacy_voice.py と同じ。案の文を問いにして、本人の言葉を集める。
3. 第2版（役割：原案）：第1版と異議を読んで書き直す。原案の表（care_plan_drafts）にも残す。
本人の言葉は番号で受け取り、本文はプログラムが入れる。判定は根拠の出どころからプログラムが決める。
どの版を使うかは人が選ぶ。AI は選ばない。選び直しても前の選択は消さない。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（app/config.py）、
プロンプトキャッシュ（意思モデルのブロック）。

関連するテスト：tests/test_advocacy_plan.py
API の取り決め：docs/api.md
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import advocacy, advocacy_prompts, advocacy_sheet, advocacy_voice, ai, care_form_records, clock, config, consent, db, ledger, professions
from app.auth import NOT_FOUND, current_user, get_conn
from app import made_documents
from app.schemas import MadeDocumentKind, ConfirmStatus, InferenceKind, PlanObjectionDraft, RevisedPlanDraft, Statement, User

router = APIRouter(prefix="/api/advocacy")

LIMITS = config.MODEL_LIMITS
MAX_PLAN_CHARS = 2000
AI_FAILED_MESSAGE = "いま、案を作れませんでした。少し時間をおいて、もう一度お試しください。"
LABEL_OBJECTION = "ツイン（AI）の異議です。本人の言葉ではありません。"
LABEL_SECOND = "ツイン（AI）が異議をもとに書き直した案です。"
SOURCE_GENERAL = "本人の言葉を読まずに AI が作った一般的な案"
FIRST = "第1版"
SECOND = "第2版"
NEITHER = "どちらも使わない"


class PlanInput(BaseModel):
    """第1版の入力。text を省くと、AI が一般的な案を作る。"""

    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=MAX_PLAN_CHARS * 2)


class StepInput(BaseModel):
    """異議と第2版の入力。again は、前の行を残したまま作り直すときに使う。"""

    model_config = ConfigDict(extra="forbid")

    again: bool = False


class ChoiceInput(BaseModel):
    """どの版を使うかの入力。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal["第1版", "第2版", "どちらも使わない"]


def _log_result(conn: sqlite3.Connection, log_id: int | None) -> dict | None:
    if log_id is None:
        return None
    row = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (log_id,)).fetchone()
    return json.loads(row["result"])


def _choice_view(conn: sqlite3.Connection, plan_id: int) -> dict | None:
    """いちばん新しい選択を返す。だれが選んだかも見せる。AI が選んだように見えないようにするため。"""
    row = conn.execute(
        "SELECT * FROM advocacy_plan_choices WHERE plan_id = ? ORDER BY rowid DESC LIMIT 1", (plan_id,)
    ).fetchone()
    if row is None:
        return None
    chooser = db.get_user(conn, row["chooser_user_id"])
    return {
        "version": row["version"],
        "chooser": chooser.display_name if chooser else "はずした人",
        "created_at": row["created_at"],
    }


def _plan_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "first": _log_result(conn, row["first_log_id"]),
        "objection": _log_result(conn, row["objection_log_id"]),
        "second": _second_with_own_words(_log_result(conn, row["second_log_id"])),
        "choice": _choice_view(conn, row["id"]),
    }


def _second_with_own_words(second: dict | None) -> dict | None:
    """本人の意向の欄の上に載せる本人の言葉を添える。ツインの案と分けて読めるようにするため。"""
    if second is None:
        return None
    return {**second, "plan": {**second["plan"], "own_words": advocacy_voice.own_words(second["plan"]["evidence"])}}


def _own_plan(conn: sqlite3.Connection, viewer: User, plan_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT * FROM advocacy_plans WHERE id = ? AND asker_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.PLAN)}", (plan_id, viewer.id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def plan_text(first: dict) -> str:
    """第1版を、AI に渡す文にする。"""
    lines = [first["summary"]]
    lines.extend(f"- {item['aim']}：{item['support']}" for item in first["items"])
    return "\n".join(lines)


def _quotes(conn: sqlite3.Connection, numbers: list[int], statements: list[Statement]) -> list[dict]:
    kept = advocacy_voice.pick_quotes(numbers, statements)[: LIMITS["advocacy_quotes_per_reason"]]
    self_name = advocacy_voice._self_name(conn)
    return [advocacy.statement_view(conn, item, self_name) for item in kept]


def objection_view(conn: sqlite3.Connection, draft: PlanObjectionDraft, statements: list[Statement]) -> dict:
    """AI の出力を異議の形にする。本文は記録から入れる。判定は根拠の出どころからプログラムが決める。"""
    used = advocacy_voice.pick_quotes([number for point in draft.points for number in point.evidence_numbers], statements)
    return {
        "label": LABEL_OBJECTION,
        "claim": draft.claim.strip(),
        "points": [
            {"target": point.target.strip(), "objection": point.objection.strip(), "evidence": _quotes(conn, point.evidence_numbers, statements)}
            for point in draft.points
        ],
        "requests": [item.strip() for item in draft.requests],
        "ask_back": draft.ask_back,
        "verdict": advocacy_sheet._verdict(used).value,
    }


def second_view(conn: sqlite3.Connection, draft: RevisedPlanDraft, statements: list[Statement]) -> dict:
    """AI の出力を第2版の形にする。本文は記録から入れる。"""
    used = advocacy_voice.pick_quotes([number for item in draft.items for number in item.evidence_numbers], statements)
    return {
        "label": LABEL_SECOND,
        "summary": draft.summary.strip(),
        "items": [
            {"aim": item.aim.strip(), "support": item.support.strip(), "why": item.why.strip(), "evidence": _quotes(conn, item.evidence_numbers, statements)}
            for item in draft.items
        ],
        "plan": {
            "self_intent": draft.self_intent.strip(),
            "long_term_goal": draft.long_term_goal.strip(),
            "evidence": [quote for item in draft.items for quote in _quotes(conn, item.evidence_numbers, statements)],
            "verdict": advocacy_sheet._verdict(used).value,
        },
    }


def _objection_statements(conn: sqlite3.Connection, viewer: User, objection: dict) -> list[Statement]:
    """異議で使った言葉を、今の先頭の行で返す。第2版の材料に必ず入れる。今は見えない行は入れない。"""
    scopes = consent.visible_scopes(conn, viewer)
    kept: dict[int, Statement] = {}
    for point in objection["points"]:
        for quote in point["evidence"]:
            head = ledger.head_of(conn, quote["statement_id"])
            if head.share_scope in scopes and head.confirm_status is not ConfirmStatus.RESTING:
                kept[head.id] = head
    return list(kept.values())


def _set_log(conn: sqlite3.Connection, plan_id: int, column: str, log_id: int) -> None:
    # 版の行は inference_logs に残るので、ここを差し替えても前の版は消えない
    with ledger.atomic(conn):
        conn.execute(f"UPDATE advocacy_plans SET {column} = ? WHERE id = ?", (log_id, plan_id))


def add_imported_plan(conn: sqlite3.Connection, request: Request, viewer: User, client_key: str, view: dict) -> int:
    """取り込んだケアプラン（PDF）を第1版として入れ、案の番号を返す。AI は呼ばない。同じ client_key なら増やさない。

    入れる欄の字数の上限（MAX_PLAN_CHARS）は、人が貼る文のためのものなので、資料から読んだ案には当てない。
    関連するテスト：tests/test_professional_records.py
    """
    advocacy._require_tool(conn, viewer, professions.PLAN)
    with request.app.state.voice_lock:
        row = conn.execute(
            "SELECT id FROM advocacy_plans WHERE asker_user_id = ? AND client_key = ?", (viewer.id, client_key)
        ).fetchone()
        if row is not None:
            return row["id"]
        log_id = advocacy._log_inference(conn, InferenceKind.DRAFT, "取り込んだケアプラン", view, [], [], None)
        with ledger.atomic(conn):
            return db.insert_row(
                conn,
                "advocacy_plans",
                {
                    "asker_user_id": viewer.id,
                    "client_key": client_key,
                    "first_log_id": log_id,
                    "created_at": clock.now(request.app).isoformat(),
                },
            )


@router.post("/plans")
def make_first_plan(
    body: PlanInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """第1版を作る。同じ client_key で送り直しても増えない。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    text = (body.text or "").strip()
    if len(text) > MAX_PLAN_CHARS:
        raise HTTPException(status_code=400, detail=f"{MAX_PLAN_CHARS}字までで入れてください。")

    def existing() -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM advocacy_plans WHERE asker_user_id = ? AND client_key = ?", (viewer.id, body.client_key)
        ).fetchone()

    with request.app.state.voice_lock:
        row = existing()
        if row is None:
            now = clock.now(request.app)
            if text:
                view = {"source": f"{viewer.display_name}が入れた案", "summary": text, "items": []}
                result = None
            else:
                stage = ledger.life_stage_on(conn, now.date()).value
                try:
                    system = advocacy_prompts.plan_first_system()
                    plain = advocacy_prompts.plan_first_user_text(stage, advocacy_voice._now_lines(conn, viewer))
                    result = advocacy_voice.ask_without_medical_orders(
                        conn,
                        lambda extra: ai.write_general_plan(system=system, text=plain + extra),
                        "原案",
                        "ケアプランの第1版",
                    )
                except advocacy._AI_ERRORS as error:
                    raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
                view = {
                    "source": SOURCE_GENERAL,
                    "summary": result.value.summary.strip(),
                    "items": [{"aim": item.aim.strip(), "support": item.support.strip()} for item in result.value.items],
                }
            log_id = advocacy._log_inference(conn, InferenceKind.DRAFT, "ケアプランの第1版", view, [], [], result)
            with ledger.atomic(conn):
                db.insert_row(
                    conn,
                    "advocacy_plans",
                    {
                        "asker_user_id": viewer.id,
                        "client_key": body.client_key,
                        "first_log_id": log_id,
                        "created_at": now.isoformat(),
                    },
                )
            row = existing()
    return {"plan": _plan_view(conn, row)}


def form_note(conn: sqlite3.Connection) -> str:
    """専門職が入力した最新のアセスメントなどを、チェックの材料に足す文。なければ空。

    ケアプランのチェックは専門職だけが見るので、本人と家族に見せない記録も渡す（app/care_form_records.py）。
    関連するテスト：tests/test_care_form_records.py の test_入力した記録をケアプランのチェックに渡す
    """
    lines = care_form_records.care_form_lines(conn)
    if not lines:
        return ""
    return "\n\n## 専門職が入力した記録（アセスメントなど。本人と家族には見せない記録です）\n" + "\n".join(lines)


@router.post("/plans/{plan_id}/objection")
def object_to_plan(
    body: StepInput,
    request: Request,
    plan_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """第1版に、ツインが異議を唱える。作ってあれば AI を呼ばずに返す。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    row = _own_plan(conn, viewer, plan_id)
    with request.app.state.voice_lock:
        if body.again or row["objection_log_id"] is None:
            first = _log_result(conn, row["first_log_id"])
            task = advocacy_prompts.objection_task(advocacy_voice._self_name(conn), plan_text(first))
            now = clock.now(request.app)
            try:
                material = advocacy_voice.gather(conn, viewer, task)
                system = advocacy_prompts.plan_objection_system(advocacy_voice._self_name(conn))
                model_text = advocacy_prompts.voice_model_text(material.element_lines)
                text = advocacy_prompts.voice_user_text(task, material.now_lines, material.word_lines, now.date(), material.reading_lines)
                text += form_note(conn)
                result = advocacy_voice.ask_without_medical_orders(
                    conn,
                    lambda extra: ai.object_to_plan(system=system, model_text=model_text, text=text + extra),
                    "根拠チェック",
                    "ケアプランの異議",
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            view = {**objection_view(conn, result.value, material.statements), "reading": advocacy_voice.reading_note(material)}
            used = sorted({quote["statement_id"] for point in view["points"] for quote in point["evidence"]})
            log_id = advocacy._log_inference(conn, InferenceKind.EVIDENCE_CHECK, task, view, material.elements, used, result)
            _set_log(conn, plan_id, "objection_log_id", log_id)
            row = _own_plan(conn, viewer, plan_id)
    return {"plan": _plan_view(conn, row)}


@router.post("/plans/{plan_id}/revise")
def revise_plan(
    body: StepInput,
    request: Request,
    plan_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """第1版と異議を読んで、第2版を作る。作ってあれば AI を呼ばずに返す。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    row = _own_plan(conn, viewer, plan_id)
    if row["objection_log_id"] is None:
        raise HTTPException(status_code=400, detail="先に、ツインの異議を聞いてください。")
    with request.app.state.voice_lock:
        if body.again or row["second_log_id"] is None:
            first = _log_result(conn, row["first_log_id"])
            objection = _log_result(conn, row["objection_log_id"])
            self_name = advocacy_voice._self_name(conn)
            task = advocacy_prompts.revise_task(self_name, plan_text(first), objection)
            now = clock.now(request.app)
            try:
                material = advocacy_voice.gather(conn, viewer, task, required=_objection_statements(conn, viewer, objection))
                system = advocacy_prompts.plan_revise_system(self_name)
                model_text = advocacy_prompts.voice_model_text(material.element_lines)
                text = advocacy_prompts.voice_user_text(task, material.now_lines, material.word_lines, now.date(), material.reading_lines)
                text += form_note(conn)
                result = advocacy_voice.ask_without_medical_orders(
                    conn,
                    lambda extra: ai.revise_plan(system=system, model_text=model_text, text=text + extra),
                    "原案",
                    "ケアプランの第2版",
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            view = {**second_view(conn, result.value, material.statements), "reading": advocacy_voice.reading_note(material)}
            used = sorted({quote["statement_id"] for quote in view["plan"]["evidence"]})
            log_id = advocacy._log_inference(conn, InferenceKind.DRAFT, task, view, material.elements, used, result)
            with ledger.atomic(conn):
                advocacy_sheet._save_plan(conn, view, [element.id for element in material.elements], now.date())
            _set_log(conn, plan_id, "second_log_id", log_id)
            row = _own_plan(conn, viewer, plan_id)
    return {"plan": _plan_view(conn, row)}


@router.post("/plans/{plan_id}/choose")
def choose_version(
    body: ChoiceInput,
    request: Request,
    plan_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """どの版を使うかを人が選ぶ。AI は呼ばない。選び直すと新しい行を足し、前の選択は残す。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    row = _own_plan(conn, viewer, plan_id)
    if body.version == SECOND and row["second_log_id"] is None:
        raise HTTPException(status_code=400, detail="先に、第2版を作ってください。")
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "advocacy_plan_choices",
            {
                "plan_id": plan_id,
                "chooser_user_id": viewer.id,
                "version": body.version,
                "created_at": clock.now(request.app).isoformat(),
            },
        )
    return {"plan": _plan_view(conn, row)}


@router.get("/plans")
def read_latest_plan(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人のいちばん新しい案を、版を並べて返す。まだなければ空を返す。AI は呼ばない。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    row = conn.execute(
        f"SELECT * FROM advocacy_plans WHERE asker_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.PLAN)} ORDER BY rowid DESC LIMIT 1", (viewer.id,)
    ).fetchone()
    return {"plan": _plan_view(conn, row) if row else None}
