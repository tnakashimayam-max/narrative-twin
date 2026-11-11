"""生活まとめプラン。暮らしの場面ごとに、今まで、出来ること、出来ないこと、支援して欲しいこと、誰がを書く紙。

役割：ツイン（AI）がセルフ、今の様子、専門職の資料と入力した記録から下書きし、専門職が画面で直して、紙（A4 横）で出す。
4つの極：4. 10年後に代弁する。
守る原則：1（決めるのは人。下書きは専門職が直してから出す）、3（ツインの文と記録を分ける）、5（直しても前の版は残す）、
          12（紙に必ず「ツイン（AI）」と出す）、17（診断と服薬の指示はしない）。

決まり（tyumajinさんの決定、2026-11-06）
- もとは事業所の生活アセスメントの紙。名前は「生活まとめプラン」。ナラティブツインが出力する成果物にする。
- 支援して欲しいことは、本人の願いとして書く。ほかの列は事実を短く。記録にない場面と列は空にする（推測で埋めない）。
- 専門職だけが作り、読み、直す。専門職の入力した記録（本人と家族には見せない）を材料にするため。
- 専門職の直しは版として残す。本人の言葉ではないので、台帳には入れない。

紙の形は forms/life_plan.json（app/care_forms.py）。画面は static/life_plan.html。
使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（Opus。app/config.py）、
プロンプトキャッシュ（セルフのブロック）、考える深さの指定。

関連するテスト：tests/test_life_plan.py
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import (
    advocacy,
    advocacy_prompts,
    advocacy_voice,
    ai,
    care_form_records,
    care_forms,
    clock,
    db,
    ledger,
    professional_prompts,
    professions,
    selfhood_prompts,
)
from app.advocacy_letter import honorific
from app.auth import NOT_FOUND, current_user, get_conn
from app import made_documents
from app.schemas import MadeDocumentKind, DocumentStatus, InferenceKind, User

router = APIRouter(prefix="/api")

FORM_ID = "life_plan"
COLUMNS = ("past", "can", "cannot", "support", "who")
# 場面をまとめて読む話題。1つの問いで集めると、問いに近い話題の言葉だけで読む量の上限が埋まるため
TOPICS = ("食事と服薬", "入浴、身だしなみ、着替え", "家事、買い物、お金", "健康管理、安全、住まい", "趣味、人とのつながり、眠り")
AI_FAILED_MESSAGE = "いま、生活まとめプランを書けませんでした。少し時間をおいて、もう一度お試しください。"
# 紙の作成者。ツインが作ったものは、作った時点では作成者をツイン（AI）にする（原則12の名前で書く）
AUTHOR = "ツイン（AI）"
DOCUMENT_LINES = 6
CELL_LIMIT = 300


class PlanInput(BaseModel):
    """下書きを頼む入力。"""

    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=64)


class RowInput(BaseModel):
    """専門職が直した1行。"""

    model_config = ConfigDict(extra="forbid")

    area: str = Field(max_length=40)
    part: str | None = Field(default=None, max_length=10)
    past: str = Field(default="", max_length=CELL_LIMIT)
    can: str = Field(default="", max_length=CELL_LIMIT)
    cannot: str = Field(default="", max_length=CELL_LIMIT)
    support: str = Field(default="", max_length=CELL_LIMIT)
    who: str = Field(default="", max_length=CELL_LIMIT)


class EditInput(BaseModel):
    """専門職が直した生活まとめプラン。"""

    model_config = ConfigDict(extra="forbid")

    rows: list[RowInput] = Field(max_length=30)


def slots() -> list[tuple[str, str | None]]:
    """紙の行の並び。食事は朝食、昼食、夕食、飲水の4行に分かれる。"""
    result: list[tuple[str, str | None]] = []
    for row in care_forms.load(FORM_ID)["rows"]:
        if row.get("parts"):
            result += [(row["name"], part) for part in row["parts"]]
        else:
            result.append((row["name"], None))
    return result


def scene_names() -> list[str]:
    """AI に渡す場面の一覧。食事は分けの名前も添える。"""
    return [f"{row['name']}（part：{'、'.join(row['parts'])}）" if row.get("parts") else row["name"] for row in care_forms.load(FORM_ID)["rows"]]


def _plain(text: str | None) -> str:
    return (text or "").replace(" ", "").replace("　", "")


def fit_rows(rows: list[dict]) -> list[dict]:
    """AI か専門職の行を、紙の行に当てはめる。紙にない場面は捨て、書かれなかった場面は空の行にする。

    食事の分けを area に書いた行（area が「朝食」）も、食事の行として受け取る。
    関連するテスト：tests/test_life_plan.py の test_紙にない場面は捨て_書かれなかった場面は空の行にする
    """
    parts_of = {part: area for area, part in slots() if part}
    by_slot: dict[tuple[str, str | None], dict] = {}
    for row in rows:
        area, part = _plain(row.get("area")), _plain(row.get("part")) or None
        if area in parts_of and part is None:
            area, part = parts_of[area], area
        key = next(((name, slot_part) for name, slot_part in slots() if _plain(name) == area and slot_part == part), None)
        if key is not None and key not in by_slot:
            by_slot[key] = {column: (row.get(column) or "").strip() for column in COLUMNS}
    return [{"area": area, "part": part, **by_slot.get((area, part), dict.fromkeys(COLUMNS, ""))} for area, part in slots()]


def _document_lines(conn: sqlite3.Connection) -> list[str]:
    """専門職の資料の要点。アセスメントなど専門職だけの資料も渡す（生活まとめプランは専門職だけが見るため）。"""
    rows = conn.execute(
        "SELECT * FROM professional_documents WHERE status IN (?, ?) ORDER BY rowid DESC LIMIT ?",
        (DocumentStatus.WAITING.value, DocumentStatus.DONE.value, DOCUMENT_LINES),
    ).fetchall()
    return [f"{row['document_date'] or row['created_at'][:10]}／{row['kind']}（{row['author'] or '作成者不明'}）：{row['summary']}" for row in rows]


def _plan_row(conn: sqlite3.Connection, plan_id: int, viewer: User) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM life_plans WHERE id = ? AND {made_documents.not_deleted(MadeDocumentKind.LIFE_PLAN)}", (plan_id,)).fetchone()
    if row is None or row["requester_user_id"] != viewer.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def _versions(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM life_plan_versions WHERE plan_id = ? ORDER BY version", (plan_id,)).fetchall()


def label_for(editors: list[str]) -> str:
    """紙に印刷する札。ツイン（AI）が下書きしたことと、修正した人を書く（原則12）。

    関連するテスト：tests/test_life_plan.py の test_札にツインと直した人を書く
    """
    head = "ツイン（AI）が記録から下書きしました。"
    if not editors:
        return head + "専門職が確かめてからお使いください。"
    return head + f"{'、'.join(honorific(name) for name in editors)}が修正しました。"


def person_name(conn: sqlite3.Connection) -> str:
    """紙に書くご利用者名。入力した記録の氏名を先に使い、なければアプリの本人の名前（さんを外す）にする。"""
    for record in care_form_records.latest_records(conn):
        values = json.loads(record["content"]).get("values", {})
        name = values.get("氏名") or values.get("利用者名")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return advocacy_voice._self_name(conn).removesuffix("さん")


def _paper(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    versions = _versions(conn, row["id"])
    latest = versions[-1]
    editors: list[str] = []
    for version in versions:
        if version["edited_by_user_id"]:
            editor = db.get_user(conn, version["edited_by_user_id"])
            if editor and editor.display_name not in editors:
                editors.append(editor.display_name)
    requester = db.get_user(conn, row["requester_user_id"])
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["draft_log_id"],)).fetchone()
    draft = json.loads(log["result"]) if log else {}
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "written_on": latest["created_at"][:10],
        "self_name": honorific(advocacy_voice._self_name(conn)),
        # 紙のご利用者名。専門職が入力したフェイスシートかアセスメントの氏名があれば使う（アプリの名前には名字がないため）
        "person_name": person_name(conn),
        # 作成者は、作った時点ではツイン（AI）。直した人は editors に分けて持つ（tyumajinさんの決定、2026-11-06）
        "author": AUTHOR,
        "editors": [name.removesuffix("さん") for name in editors],
        "requester": requester.display_name if requester else "",
        "rows": json.loads(latest["body"]),
        "version": latest["version"],
        "edited": len(versions) > 1,
        "label": label_for(editors),
        "pieces": draft.get("pieces", []),
        "reading": draft.get("reading"),
    }


@router.get("/life-plan-form")
def blank_form() -> dict:
    """白紙の生活まとめプランの形。中身のない書式なので、ログインの前でも返す（白紙を印刷するため）。"""
    form = care_forms.load(FORM_ID)
    return {"form": {key: form[key] for key in ("title", "paper", "columns", "rows")}, "label": "ツイン（AI）が下書きし、専門職が修正して使う紙です。"}


@router.post("/advocacy/life-plans", status_code=201)
def make_plan(
    body: PlanInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """生活まとめプランの下書きを作る。同じ client_key なら、AI を呼ばずに前の下書きを返す。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)

    def existing() -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM life_plans WHERE requester_user_id = ? AND client_key = ?", (viewer.id, body.client_key)
        ).fetchone()

    with request.app.state.voice_lock:
        row = existing()
        if row is None:
            if not advocacy_voice.has_material(conn, viewer):
                raise HTTPException(status_code=400, detail=advocacy.NOT_YET)
            now = clock.now(request.app)
            self_name = honorific(advocacy_voice._self_name(conn))
            task = f"{self_name}の生活まとめプランを、暮らしの場面ごとに下書きしてください。"
            try:
                material = advocacy_voice.gather_topics(conn, viewer, task, TOPICS)
                system = professional_prompts.life_plan_system(self_name)
                if material.selfhood_body:
                    model_text = selfhood_prompts.answer_model_text(material.selfhood_body, material.element_lines[1:], material.heart_lines)
                else:
                    model_text = advocacy_prompts.voice_model_text(material.element_lines)
                text = professional_prompts.life_plan_user_text(
                    now.date(),
                    scene_names(),
                    care_form_records.care_form_lines(conn),
                    material.now_lines,
                    _document_lines(conn),
                    material.word_lines,
                )
                result = advocacy_voice.ask_without_medical_orders(
                    conn,
                    lambda extra: ai.write_life_plan(system=system, model_text=model_text, text=text + extra),
                    "生活まとめプラン",
                    "生活まとめプラン",
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            draft = result.value
            rows = fit_rows([item.model_dump() for item in draft.rows])
            pieces = (
                advocacy_voice._piece_views(draft.piece_numbers, material.pieces)
                if material.pieces
                else advocacy_voice._element_views(conn, draft.piece_numbers, material.elements)
            )
            view = {"rows": rows, "pieces": pieces, "reading": advocacy_voice.reading_note(material)}
            log_id = advocacy._log_inference(conn, InferenceKind.LIFE_PLAN, task, view, material.elements, [], result)
            with ledger.atomic(conn):
                plan_id = db.insert_row(
                    conn,
                    "life_plans",
                    {"requester_user_id": viewer.id, "draft_log_id": log_id, "client_key": body.client_key, "created_at": now.isoformat()},
                )
                db.insert_row(
                    conn,
                    "life_plan_versions",
                    {
                        "plan_id": plan_id,
                        "version": 1,
                        "body": json.dumps(rows, ensure_ascii=False),
                        "edited_by_user_id": None,
                        "created_at": now.isoformat(),
                    },
                )
            row = existing()
    return {"plan": _paper(conn, row)}


@router.get("/advocacy/life-plans")
def list_plans(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人が作った生活まとめプランを、新しい順に返す。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    rows = conn.execute(
        f"SELECT * FROM life_plans WHERE requester_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.LIFE_PLAN)} ORDER BY rowid DESC", (viewer.id,)
    ).fetchall()
    return {"items": [_paper(conn, row) for row in rows]}


@router.get("/advocacy/life-plans/{plan_id}")
def read_plan(
    plan_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    advocacy._require_tool(conn, viewer, professions.PLAN)
    return {"plan": _paper(conn, _plan_row(conn, plan_id, viewer))}


@router.post("/advocacy/life-plans/{plan_id}/edit")
def edit_plan(
    body: EditInput,
    request: Request,
    plan_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """専門職が直した中身を、新しい版として足す。前の版は残す（原則5）。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)
    row = _plan_row(conn, plan_id, viewer)
    rows = fit_rows([item.model_dump() for item in body.rows])
    latest = _versions(conn, plan_id)[-1]
    if rows == json.loads(latest["body"]):
        return {"plan": _paper(conn, row)}
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "life_plan_versions",
            {
                "plan_id": plan_id,
                "version": latest["version"] + 1,
                "body": json.dumps(rows, ensure_ascii=False),
                "edited_by_user_id": viewer.id,
                "created_at": clock.now(request.app).isoformat(),
            },
        )
    return {"plan": _paper(conn, row)}
