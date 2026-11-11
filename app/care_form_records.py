"""専門職が入力するアセスメント、フェイスシート、ライフサポートプランの記録。

役割：forms/ の書式どおりに専門職が入力した記録を、版を残して保存する。資料のレビュー、ケアプランのチェック、生活まとめプランに、
     最新の記録を材料として渡す（care_form_lines）。
4つの極：4. 10年後に代弁する。
守る原則：3（専門職の記録は本人の言葉と分ける）、5（直しても前の版は消さない）、
          17（医療の情報も材料にする。診断と服薬の指示はしない）。

決まり（tyumajinさんの決定、2026-11-06）
- メッセージだけでは分からない、その時点の心身の状況を、専門職として入力する記録。
- 本人と家族には開示しない。台帳（言明）には入れない。台帳に入れると、本人の画面で見えるため。
- 見られるのは、ケアプランのツールを持つ専門職だけ。ご家族も受け取るツインの代弁と手紙の材料にも渡さない。
- 直すときは新しい行を足し、前の行を指す（replaces_id）。前の版は残す。
- 新しく入れるときは、JSON か PDF を読み込める。PDF はツイン（AI）が書式の欄に当てはめて読み、値を返すだけで保存しない。
  専門職が画面で確かめてから保存する（JSON は画面の中で読む。static/care_form.js）。
- 前の記録は開いて読み、直し、コピーし、削除できる（資料の画面と同じ形）。コピーは別の日付の新しい記録になり、
  どの記録をコピーしたか（copied_from）を中身に残す。同じ記録を違う日で使うことがあるため。
- 削除は行を消さず、いちばん新しい版に印（care_form_deletions）を足す。前の版もまとめて一覧、中身、AI の材料から外れる。
  削除の前に作った生活まとめプランやチェックは、そのまま残す。

関連するテスト：tests/test_care_form_records.py
"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app import advocacy, ai, care_forms, clock, db, ledger, professional_prompts, professional_records, professions
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import CareFormRead, User

router = APIRouter(prefix="/api/advocacy/care-forms")

# AI に渡す専門職の記録の文字数の上限。記録が増えても読む量を増やさない
LINES_LIMIT = 4000
AI_FAILED_MESSAGE = "いま、資料を読めませんでした。少し時間をおいて、もう一度お試しください。"


class RecordInput(BaseModel):
    """専門職が入力した書式の値。values の名前は「まとまり／欄」の形（app/care_forms.py の field_keys）。"""

    model_config = ConfigDict(extra="forbid")

    form_id: Literal["assessment", "face_sheet", "life_support_plan"]
    record_date: date
    values: dict[str, Any] = {}
    details: dict[str, str] = {}
    replaces_id: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    # コピーして入れたときの、もとの記録。直すとき（replaces_id）とは一緒に使わない
    copied_from: int | None = Field(default=None, ge=1, le=db.MAX_ID)
    client_key: str = Field(min_length=1, max_length=64)


def _require(conn: sqlite3.Connection, viewer: User) -> None:
    """ケアプランのツールを持つ専門職だけを通す。本人と家族には、ないものとして答える。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)


def _filled(values: dict, details: dict) -> tuple[dict, dict]:
    """空の値を落とす。空の行も落とす。"""
    kept: dict = {}
    for key, value in values.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            rows = [{name: text.strip() for name, text in entry.items() if isinstance(text, str) and text.strip()} for entry in value]
            rows = [row for row in rows if row]
            if rows:
                kept[key] = rows
        elif isinstance(value, list):
            if value:
                kept[key] = value
        elif isinstance(value, str) and value.strip():
            kept[key] = value.strip()
    return kept, {key: text.strip() for key, text in details.items() if text.strip()}


def _replaced_ids(conn: sqlite3.Connection) -> set[int]:
    """直された前の版の番号。削除した記録の版も含めて数える。前の版が新しい版として出てこないようにするため。"""
    return {row[0] for row in conn.execute("SELECT replaces_id FROM care_form_records WHERE replaces_id IS NOT NULL")}


def _deleted_ids(conn: sqlite3.Connection) -> set[int]:
    """削除の印が付いた記録（いちばん新しい版）の番号。"""
    return {row[0] for row in conn.execute("SELECT record_id FROM care_form_deletions")}


def _replaced_by(conn: sqlite3.Connection) -> dict[int, int]:
    """前の版の番号から、それを直した版の番号を引く表。"""
    return {
        row["replaces_id"]: row["id"]
        for row in conn.execute("SELECT id, replaces_id FROM care_form_records WHERE replaces_id IS NOT NULL")
    }


def _head_of(record_id: int, replaced_by: dict[int, int]) -> int:
    """その記録のいちばん新しい版の番号。"""
    seen = {record_id}
    while record_id in replaced_by and replaced_by[record_id] not in seen:
        record_id = replaced_by[record_id]
        seen.add(record_id)
    return record_id


def _earlier(conn: sqlite3.Connection, row: sqlite3.Row) -> list[sqlite3.Row]:
    """前の版を、新しいほうから並べる。"""
    found: list[sqlite3.Row] = []
    seen = {row["id"]}
    replaces = row["replaces_id"]
    while replaces is not None and replaces not in seen:
        before = conn.execute("SELECT * FROM care_form_records WHERE id = ?", (replaces,)).fetchone()
        if before is None:
            break
        found.append(before)
        seen.add(replaces)
        replaces = before["replaces_id"]
    return found


def _view(conn: sqlite3.Connection, row: sqlite3.Row, *, with_content: bool = True) -> dict:
    author = db.get_user(conn, row["author_user_id"])
    content = json.loads(row["content"])
    copied = content.get("copied_from")
    source = conn.execute("SELECT record_date FROM care_form_records WHERE id = ?", (copied,)).fetchone() if copied else None
    view = {
        "id": row["id"],
        "form_id": row["form_id"],
        "title": care_forms.load(row["form_id"])["title"],
        "record_date": row["record_date"],
        "author": author.display_name if author else "",
        "replaces_id": row["replaces_id"],
        # 第何版か。直すたびに1つ増える
        "version": len(_earlier(conn, row)) + 1,
        "copied_from": copied,
        "copied_from_date": source["record_date"] if source else None,
        "created_at": row["created_at"],
    }
    if with_content:
        view.update({"values": content.get("values", {}), "details": content.get("details", {})})
    return view


def latest_records(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """書式ごとの、いちばん新しい記録（直された前の版と、削除した記録は除く）。日付の新しいものを先にする。"""
    hidden = _replaced_ids(conn) | _deleted_ids(conn)
    rows = conn.execute("SELECT * FROM care_form_records ORDER BY record_date DESC, rowid DESC").fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["id"] not in hidden and row["form_id"] not in latest:
            latest[row["form_id"]] = row
    return [latest[form_id] for form_id in care_forms.INPUT_FORMS if form_id in latest]


def _value_text(value: Any) -> str:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return "／".join("、".join(f"{name} {text}" for name, text in entry.items()) for entry in value)
    if isinstance(value, list):
        return "、".join(value)
    return str(value)


def care_form_lines(conn: sqlite3.Connection) -> list[str]:
    """最新のアセスメント、フェイスシート、ライフサポートプランを、AI に渡す行にする。空の欄は渡さない。

    専門職だけの記録なので、資料のレビュー、ケアプランのチェック、生活まとめプラン（どれもケアプランの道具を持つ専門職だけが見る）にだけ渡す。
    """
    lines: list[str] = []
    used = 0
    for row in latest_records(conn):
        content = json.loads(row["content"])
        author = db.get_user(conn, row["author_user_id"])
        head = f"### {care_forms.load(row['form_id'])['title']}（{row['record_date']}、{author.display_name if author else '専門職'}が入力）"
        body = [head]
        for key, value in content.get("values", {}).items():
            detail = content.get("details", {}).get(key)
            body.append(f"- {key}：{_value_text(value)}" + (f"（詳細：{detail}）" if detail else ""))
        text = "\n".join(body)
        if used + len(text) > LINES_LIMIT:
            text = text[: max(LINES_LIMIT - used, 0)]
        if text:
            lines.append(text)
            used += len(text)
        if used >= LINES_LIMIT:
            break
    return lines


@router.get("/forms")
def list_forms(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """入力できる書式の定義を返す。画面はこれを読んで入力欄を組み立てる。"""
    _require(conn, viewer)
    return {"items": [care_forms.load(form_id) for form_id in care_forms.INPUT_FORMS]}


@router.get("/records")
def list_records(
    form_id: str | None = Query(default=None, max_length=40),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """入力した記録の一覧（新しい版だけ。削除した記録は出さない）。中身は1件ずつ読む。"""
    _require(conn, viewer)
    hidden = _replaced_ids(conn) | _deleted_ids(conn)
    rows = conn.execute("SELECT * FROM care_form_records ORDER BY record_date DESC, rowid DESC").fetchall()
    items = [_view(conn, row, with_content=False) for row in rows if row["id"] not in hidden and (form_id is None or row["form_id"] == form_id)]
    return {"items": items}


def _live_row(conn: sqlite3.Connection, record_id: int) -> tuple[sqlite3.Row, int]:
    """記録の行と、いちばん新しい版の番号を返す。ない記録と、削除した記録（前の版も）は、ないものとして答える。"""
    row = conn.execute("SELECT * FROM care_form_records WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    head = _head_of(record_id, _replaced_by(conn))
    if head in _deleted_ids(conn):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row, head


@router.get("/records/{record_id}")
def read_record(
    record_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """記録の中身。前の版の一覧（earlier）と、いちばん新しい版の番号（latest_id）も返す。前の版も開いて読める。"""
    _require(conn, viewer)
    row, head = _live_row(conn, record_id)
    view = _view(conn, row)
    view["latest_id"] = head
    view["earlier"] = [_view(conn, before, with_content=False) for before in _earlier(conn, row)]
    return {"record": view}


@router.delete("/records/{record_id}")
def delete_record(
    request: Request,
    record_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """記録を削除する。行は消さず、いちばん新しい版に印を足す。前の版もまとめて見えなくなる。

    消せるのは、ケアプランのツールを持つ専門職（直せる人と同じ）。前の版を指して消すことはできない。
    """
    _require(conn, viewer)
    row, head = _live_row(conn, record_id)
    if head != row["id"]:
        raise HTTPException(status_code=400, detail="前の版は消せません。新しい版を開いて削除してください。")
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "care_form_deletions",
            {"record_id": row["id"], "deleted_by_user_id": viewer.id, "deleted_at": clock.now(request.app).isoformat()},
        )
    return {"deleted": row["id"]}


@router.post("/records", status_code=201)
def save_record(
    body: RecordInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """入力した書式を保存する。同じ client_key なら、新しく足さずに前の保存を返す。"""
    _require(conn, viewer)
    existing = conn.execute(
        "SELECT * FROM care_form_records WHERE author_user_id = ? AND client_key = ?", (viewer.id, body.client_key)
    ).fetchone()
    if existing is not None:
        return {"record": _view(conn, existing)}
    values, details = _filled(body.values, body.details)
    found = care_forms.problems(body.form_id, values, details)
    if found:
        raise HTTPException(status_code=400, detail="、".join(found[:3]))
    if body.replaces_id is not None and body.copied_from is not None:
        raise HTTPException(status_code=400, detail="修正することとコピーは、一緒にはできません。")
    deleted = _deleted_ids(conn)
    copied_from = body.copied_from
    if body.replaces_id is not None:
        before = conn.execute("SELECT form_id, content FROM care_form_records WHERE id = ?", (body.replaces_id,)).fetchone()
        if before is None or before["form_id"] != body.form_id or body.replaces_id in deleted:
            raise HTTPException(status_code=400, detail="修正する前の記録が見つかりません。")
        if body.replaces_id in _replaced_ids(conn):
            raise HTTPException(status_code=400, detail="この記録は、もう修正されています。新しい版を開いてください。")
        # コピーした記録を直しても、どの記録のコピーかは引き継ぐ
        copied_from = json.loads(before["content"]).get("copied_from")
    if body.copied_from is not None:
        source = conn.execute("SELECT form_id FROM care_form_records WHERE id = ?", (body.copied_from,)).fetchone()
        if source is None or source["form_id"] != body.form_id or _head_of(body.copied_from, _replaced_by(conn)) in deleted:
            raise HTTPException(status_code=400, detail="コピーするもとの記録が見つかりません。")
    content = {"values": values, "details": details}
    if copied_from is not None:
        content["copied_from"] = copied_from
    now = clock.now(request.app)
    with ledger.atomic(conn):
        record_id = db.insert_row(
            conn,
            "care_form_records",
            {
                "form_id": body.form_id,
                "record_date": body.record_date.isoformat(),
                "content": json.dumps(content, ensure_ascii=False),
                "author_user_id": viewer.id,
                "replaces_id": body.replaces_id,
                "client_key": body.client_key,
                "created_at": now.isoformat(),
            },
        )
    row = conn.execute("SELECT * FROM care_form_records WHERE id = ?", (record_id,)).fetchone()
    return {"record": _view(conn, row)}


def _plain_key(key: str) -> str:
    return key.replace(" ", "").replace("　", "").replace("/", "／")


def _find_field(by_key: dict, key: str):
    """欄を名前で探す。AI が頭に見出しを付けた名前（「利用者基本情報／氏名」）でも、頭を外しながら探す（実測）。"""
    parts = _plain_key(key).split("／")
    for start in range(len(parts)):
        found = by_key.get("／".join(parts[start:]))
        if found is not None:
            return found
    return None


def fit_read(form_id: str, draft: CareFormRead) -> dict:
    """AI が読んだ値を、書式に合うものだけにする。書式にない欄、選択肢にない値は捨て、捨てた欄の名前を返す。

    関連するテスト：tests/test_care_form_records.py の test_PDFを書式の欄に読み_合わない値は捨てて_保存はしない
    """
    rows = care_forms.fields(form_id)
    by_key = {_plain_key(key): (key, row) for key, row in zip(care_forms.field_keys(form_id), rows)}
    repeats = {row["path"][0]: (row["repeat"], set()) for row in rows if row["path"] and row["repeat"] > 1}
    for row in rows:
        if row["path"] and row["path"][0] in repeats:
            repeats[row["path"][0]][1].add(row["name"])
    values: dict = {}
    details: dict = {}
    skipped: list[str] = []
    for entry in draft.entries:
        found = _find_field(by_key, entry.key)
        if found is None:
            skipped.append(entry.key)
            continue
        key, row = found
        if row["type"] == "select":
            if entry.value.strip() in row["choices"]:
                values[key] = entry.value.strip()
            elif entry.value.strip():
                skipped.append(key)
        elif row["type"] == "multi_select":
            chosen = [choice for choice in entry.choices if choice in row["choices"]]
            if chosen:
                values[key] = chosen
        elif entry.value.strip():
            values[key] = entry.value.strip()
        if row["detail"] and entry.detail.strip():
            details[key] = entry.detail.strip()
    grouped: dict[str, dict[int, dict]] = {}
    for item in draft.rows:
        if item.group not in repeats or item.name not in repeats[item.group][1] or item.index >= repeats[item.group][0]:
            skipped.append(f"{item.group}／{item.name}")
            continue
        if item.value.strip():
            grouped.setdefault(item.group, {}).setdefault(item.index, {})[item.name] = item.value.strip()
    for group, by_index in grouped.items():
        values[group] = [by_index[index] for index in sorted(by_index)]
    record_date = draft.record_date if draft.record_date and len(draft.record_date) == 10 else None
    return {"values": values, "details": details, "record_date": record_date, "person_name": draft.person_name.strip(), "skipped": sorted(set(skipped))}


@router.post("/read")
async def read_pdf(
    request: Request,
    form_id: Literal["assessment", "face_sheet", "life_support_plan"] = Query(),
    file_name: str = Query(default="資料.pdf", max_length=120),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """アセスメントなどの PDF を、書式の欄に当てはめて読む。読んだ値を返すだけで、保存はしない。

    本文にファイルそのものを送る（資料の取り込みと同じ形）。読む処理はスレッドで動かし、ほかの画面を止めない。
    """
    data = await request.body()
    return await run_in_threadpool(_read_pdf, request, form_id, file_name, viewer, conn, data)


def _read_pdf(request: Request, form_id: str, file_name: str, viewer: User, conn: sqlite3.Connection, data: bytes) -> dict:
    _require(conn, viewer)
    # 自分用の起動では読まない。資料には本人以外の人の情報も入っていて、API へ送ると取り消せないため
    if request.app.state.deployment.mine:
        raise HTTPException(status_code=400, detail=professional_records.MINE_MESSAGE)
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="PDF のファイルを渡してください。")
    if len(data) > professional_records.MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="10MB までの PDF にしてください。")
    if professional_records.count_pages(data) > professional_records.MAX_PAGES:
        raise HTTPException(status_code=400, detail=f"{professional_records.MAX_PAGES}ページまでの PDF にしてください。")
    form = care_forms.load(form_id)
    self_user = db.self_user(conn)
    name = self_user.display_name if self_user else "本人"
    with request.app.state.voice_lock:
        try:
            result = ai.read_care_form(
                system=professional_prompts.care_form_read_system(name),
                pdf_base64=base64.standard_b64encode(data).decode("ascii"),
                text=professional_prompts.care_form_read_user_text(form["title"], professional_prompts.care_form_field_lines(form), file_name),
            )
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
        advocacy._log_call(conn, "書式を読む", result, "アセスメントなどの読み取り")
        conn.commit()
    read = fit_read(form_id, result.value)
    # 名前が本人と合わないときは知らせる。保存は専門職が確かめてからなので、ここでは止めない
    read["name_matches"] = professional_records.name_matches(conn, read["person_name"])
    return {"read": read}
