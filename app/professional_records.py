"""専門職がツインに伝えること（チャットと資料）。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：専門職がチャットで伝えた文と、渡した資料（PDF）をツインが読み、項目に分ける。専門職が「残す」を押した項目だけを、
     今の様子として台帳に入れる。取り込んだケアプランは、ケアプランのチェックに渡す。
4つの極：3. 読み解き、積み上げる。
守る原則：1（判断は人。残すかは専門職が決める）、3（今の様子は本人の言葉と分ける）、5（台帳は足すだけ）、
          9（資料の中の「本人：〜」は専門職の聞き取りで、本人の言葉として引かない）、17（医療の情報も
          書かれたとおりに観察の項目にする。診断と服薬の指示はしない）。

流れ：読む → 項目に分ける（確かめ待ち）→ 専門職が残す → 今の様子の言明。チャットの履歴にはしない。
台帳は消せないので、確かめる前に止める作りにする。資料に書かれた利用者の名前が本人と合わないときは、
項目を出す前に止め、専門職が「この人の資料です」と答えるまで進めない。
話した人と対象を分ける：話した人は伝えた専門職。聞き取りの項目は、セルフの重みを証言と同じにする（app/selfhood.py が kind を読む）。

自分用の起動（実データ）では、資料の取り込みを止める。資料には本人以外の人の情報も入っていて、API へ送ると取り消せないため。

PDF の渡し方（文書のブロック、上限、引用の位置を返す機能と構造化出力を同時に使えないこと）は、
設計のときに Claude Code の claude-api スキルで確かめた（2026-11-06）。
使っている Claude API の機能：PDF の入力（文書のブロック。スキャンはページを画像として読む）、JSON 出力と Pydantic での検証、
モデルの使い分け（伝えを分けるのは Haiku、資料を読むのは Sonnet。app/config.py）。

関連するテスト：tests/test_professional_records.py
設計：docs/decisions.md の「専門職が伝えること」
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path as FilePath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app import (
    advocacy,
    advocacy_plan,
    advocacy_prompts,
    advocacy_voice,
    ai,
    clock,
    db,
    ledger,
    observations,
    professional_prompts,
    professions,
    selfhood_prompts,
)
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import (
    DocumentKind,
    DocumentStatus,
    InferenceKind,
    LetterStatus,
    ProfessionalItemKind,
    ProfessionalItemStatus,
    ShareScope,
    Theme,
    User,
)

router = APIRouter(prefix="/api/advocacy")

TELL_TRIGGER = "専門職の伝え"
DOCUMENT_TRIGGER = "資料の取り込み"
MAX_TELL_CHARS = 2000
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 30
AI_FAILED_MESSAGE = "いま、ツインが読めませんでした。少し時間をおいて、もう一度お試しください。"
MINE_MESSAGE = "自分用の起動では、資料を取り込めません。資料には本人以外の方の情報も入っているためです。"
_PAGE_MARK = re.compile(rb"/Type\s*/Page(?![s\w])")
_HEARD = {ProfessionalItemKind.SELF_HEARD, ProfessionalItemKind.FAMILY_HEARD}
# ご家族も受け取る資料。ケアプランは本人と家族が同意して受け取り、担当者会議には家族も出る。
# アセスメント、支援経過、事業所の報告、そのほかは専門職の内側の記録として、残した記録を「専門職だけ」にする。
# アセスメントを家族に見せないのは tyumajinさんの決定（2026-11-06）。ほかの種類は、分からないものを見せない側に倒した
FAMILY_VISIBLE_KINDS = {DocumentKind.CARE_PLAN.value, DocumentKind.MEETING.value}
# 消去できるのは、取り込んでからこの時間まで。過ぎると、AI の処理が積み重なって前の時点に戻せなくなるため（tyumajinさんの決定）
ERASE_HOURS = 72


class TellInput(BaseModel):
    """チャットで伝える文。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TELL_CHARS)
    client_key: str = Field(min_length=1, max_length=64)


class DecideInput(BaseModel):
    """項目を残すかどうか。text を入れると、直した文で残す。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["残す", "残さない"]
    text: str | None = Field(default=None, max_length=300)


def documents_dir(request: Request) -> FilePath:
    """資料の置き場。写真の隣に置く。Git には入れない（.gitignore の data/**/documents/）。"""
    return request.app.state.deployment.photos_dir.parent / "documents"


def _require_document_tool(conn: sqlite3.Connection, viewer: User) -> None:
    """資料を扱えるのは、ケアプランのチェックを使える専門職だけ。"""
    advocacy._require_tool(conn, viewer, professions.PLAN)


def _plain(name: str) -> str:
    """名前を比べる形にする。空白と敬称を外す。"""
    return re.sub(r"\s|さん$|様$|殿$", "", name or "")


def _self_names(conn: sqlite3.Connection) -> list[str]:
    self_user = db.self_user(conn)
    if self_user is None:
        return []
    names = {self_user.name, self_user.called_name or "", self_user.display_name}
    return [_plain(name) for name in names if _plain(name)]


def name_matches(conn: sqlite3.Connection, person_name: str) -> bool:
    """資料の利用者の名前が本人と合うかを返す。名前が書かれていなければ合うとみなす（専門職が確かめて残すため）。

    「田中誠一」と「誠一」のように、片方がもう片方を含めば合うとする。
    関連するテスト：tests/test_professional_records.py の test_名前が本人と合わない資料は項目を出さずに止める
    """
    written = _plain(person_name)
    if not written:
        return True
    return any(name in written or written in name for name in _self_names(conn))


def _about_user_id(conn: sqlite3.Connection, about: str) -> int | None:
    """「だれについてか」の名前を人の番号に直す。本人の名前か「本人」なら本人。見つからなければ空。"""
    plain = _plain(about)
    self_user = db.self_user(conn)
    if self_user and (not plain or plain == "本人" or plain in _self_names(conn)):
        return self_user.id
    for user in db.list_users(conn):
        # 資料には「長女」のように続柄で書かれることが多いので、続柄でも引く
        names = {_plain(user.name), _plain(user.called_name or ""), _plain(user.display_name), _plain(user.relation or "")}
        if plain in names - {""}:
            return user.id
    return None


def _item_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    about = db.get_user(conn, row["about_user_id"]) if row["about_user_id"] else None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "page": row["page"],
        **_place_of(conn, row["id"]),
        "about": about.display_name if about else None,
        "status": row["status"],
        "statement_id": row["statement_id"],
        "document_id": row["document_id"],
        "turn_id": row["turn_id"],
    }


def _place_of(conn: sqlite3.Connection, item_id: int) -> dict:
    """資料から読んだ項目の、資料の中の場所。チャットの項目にはない。"""
    place = conn.execute("SELECT section, quote FROM professional_item_places WHERE item_id = ?", (item_id,)).fetchone()
    return {"section": place["section"] if place else "", "quote": place["quote"] if place else ""}


def _items(conn: sqlite3.Connection, column: str, value: int) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM professional_items WHERE {column} = ? ORDER BY rowid", (value,)).fetchall()
    return [_item_view(conn, row) for row in rows]


def _insert_items(conn: sqlite3.Connection, teller: User, drafts, *, document_id=None, turn_id=None, at: str) -> None:
    for draft in drafts:
        item_id = db.insert_row(
            conn,
            "professional_items",
            {
                "document_id": document_id,
                "turn_id": turn_id,
                "teller_user_id": teller.id,
                "kind": draft.kind.value,
                "text": draft.text.strip(),
                "page": draft.page if document_id else None,
                "about_user_id": _about_user_id(conn, draft.about),
                "status": ProfessionalItemStatus.WAITING.value,
                "created_at": at,
            },
        )
        if document_id and (draft.section.strip() or draft.quote.strip()):
            conn.execute(
                "INSERT INTO professional_item_places (item_id, section, quote) VALUES (?, ?, ?)",
                (item_id, draft.section.strip(), draft.quote.strip()),
            )


# ---- チャットで伝える ----


def _tell_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {"id": row["id"], "text": row["text"], "created_at": row["created_at"], "items": _items(conn, "turn_id", row["id"])}


def tell(conn: sqlite3.Connection, request: Request, viewer: User, text: str, client_key: str) -> dict:
    """伝えた文を項目に分け、確かめ待ちにして返す。台帳にはまだ入れない。同じ client_key なら AI を呼ばずに返す。"""
    advocacy._require_supporter(conn, viewer)
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="伝えることを入れてください。")

    def existing() -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM professional_tells WHERE teller_user_id = ? AND client_key = ?", (viewer.id, client_key)
        ).fetchone()

    with request.app.state.voice_lock:
        row = existing()
        if row is None:
            now = clock.now(request.app)
            self_user = db.self_user(conn)
            name = self_user.display_name if self_user else "本人"
            try:
                result = ai.split_report(
                    system=professional_prompts.split_system(name),
                    text=professional_prompts.split_user_text(text, now.date()),
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            advocacy._log_call(conn, "伝えを分ける", result, "専門職の伝え")
            view = {"items": [item.model_dump(mode="json") for item in result.value.items]}
            log_id = advocacy._log_inference(conn, InferenceKind.REPORT_SPLIT, text, view, [], [], result)
            with ledger.atomic(conn):
                tell_id = db.insert_row(
                    conn,
                    "professional_tells",
                    {"teller_user_id": viewer.id, "client_key": client_key, "text": text, "log_id": log_id, "created_at": now.isoformat()},
                )
                _insert_items(conn, viewer, result.value.items, turn_id=tell_id, at=now.isoformat())
            row = existing()
    return _tell_view(conn, row)


@router.post("/tell")
def tell_twin(
    body: TellInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """チャットで伝える。分けた項目を返す。残すかどうかは、項目ごとに専門職が決める。"""
    return {"tell": tell(conn, request, viewer, body.text, body.client_key)}


@router.get("/tells")
def list_tells(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人が伝えたことを、古い順に返す。ほかの人の伝えは返さない。"""
    advocacy._require_supporter(conn, viewer)
    rows = conn.execute("SELECT * FROM professional_tells WHERE teller_user_id = ? ORDER BY rowid", (viewer.id,)).fetchall()
    return {"items": [_tell_view(conn, row) for row in rows]}


# ---- 資料（PDF）の取り込み ----


def scope_of(document: sqlite3.Row | None) -> ShareScope:
    """残す記録の見える範囲。チャットの伝えとケアプランは意思決定支援用、アセスメントなどは専門職だけ。

    関連するテスト：tests/test_professional_records.py の test_アセスメントから残した記録は家族に見えない
    """
    if document is None or document["kind"] in FAMILY_VISIBLE_KINDS:
        return ShareScope.DECISION_SUPPORT
    return ShareScope.PROFESSIONAL_ONLY


def erase_until(document: sqlite3.Row) -> datetime:
    return datetime.fromisoformat(document["created_at"]) + timedelta(hours=ERASE_HOURS)


def _document_view(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime | None = None) -> dict:
    uploader = db.get_user(conn, row["uploader_user_id"])
    waiting_name = row["status"] == DocumentStatus.NAME_CHECK.value
    return {
        "family_can_see": scope_of(row) is ShareScope.DECISION_SUPPORT,
        "erase_until": erase_until(row).isoformat(),
        "can_erase": now is not None and now <= erase_until(row),
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "author": row["author"],
        "document_date": row["document_date"],
        "person_name": row["person_name_on_document"],
        "pages": row["pages"],
        "status": row["status"],
        "summary": row["summary"],
        "care_plan": json.loads(row["plan_json"]) if row["plan_json"] else None,
        "has_medical": bool(row["has_medical"]),
        "uploader": uploader.display_name if uploader else "はずした人",
        "created_at": row["created_at"],
        "made_by_ai": _read_of(conn, row).get("made_by_ai", False) or made_by_ai_of(row["author"] or "", False),
        "review": review_of(conn, row["id"]),
        # 名前の確かめが済むまで、項目は見せない。別の人の資料の中身を広げないため
        "items": [] if waiting_name else _items(conn, "document_id", row["id"]),
    }


def made_by_ai_of(author: str, flagged: bool) -> bool:
    """AI が作った資料かを返す。読む係の印か、書いた人の欄にツインや AI とあれば、そうとみなす。

    関連するテスト：tests/test_professional_records.py の test_ツインが作った資料は_レビューだけで記録の候補にしない
    """
    plain = (author or "").replace(" ", "")
    return flagged or "ツイン" in plain or plain.upper() in {"AI", "ＡＩ"} or "（AI）" in plain


def _read_of(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["read_log_id"],)).fetchone()
    return json.loads(log["result"]) if log else {}


def review_of(conn: sqlite3.Connection, document_id: int) -> dict | None:
    """いちばん新しいレビュー。まだなければ空。"""
    row = conn.execute(
        "SELECT log_id, created_at FROM document_reviews WHERE document_id = ? ORDER BY rowid DESC LIMIT 1", (document_id,)
    ).fetchone()
    if row is None:
        return None
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["log_id"],)).fetchone()
    return {**json.loads(log["result"]), "created_at": row["created_at"]} if log else None


def _document_row(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM professional_documents WHERE id = ?", (document_id,)).fetchone()
    if row is None or row["status"] == DocumentStatus.WITHDRAWN.value:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def count_pages(data: bytes) -> int:
    """PDF のページ数を数える。圧縮された形では数えられないことがあり、そのときは 0 を返す（上限の確かめを飛ばす）。"""
    return len(_PAGE_MARK.findall(data))


@router.post("/documents", status_code=201)
async def upload_document(
    request: Request,
    client_key: str = Query(min_length=1, max_length=64),
    file_name: str = Query(default="資料.pdf", max_length=120),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """資料（PDF）を受け取り、ツインが読んで項目に分ける。本文にファイルそのものを送る（写真と同じ形）。

    本文を受け取るところだけを非同期にし、読む処理はスレッドで動かす。AI を待つ40秒ほどの間に、
    ほかの画面の応答まで止めないため（鍵は threading.Lock なので、受け付けの流れの中で待たせない）。
    """
    data = await request.body()
    return await run_in_threadpool(_read_upload, request, client_key, file_name, viewer, conn, data)


def _read_upload(request: Request, client_key: str, file_name: str, viewer: User, conn: sqlite3.Connection, data: bytes) -> dict:
    """受け取った PDF を確かめ、ツインに読ませて保存する。upload_document から、スレッドで呼ぶ。"""
    _require_document_tool(conn, viewer)
    if request.app.state.deployment.mine:
        raise HTTPException(status_code=400, detail=MINE_MESSAGE)
    existing = conn.execute(
        "SELECT * FROM professional_documents WHERE uploader_user_id = ? AND client_key = ?", (viewer.id, client_key)
    ).fetchone()
    if existing is not None:
        return {"document": _document_view(conn, existing, clock.now(request.app))}
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="PDF のファイルを渡してください。")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="10MB までの PDF にしてください。")
    pages = count_pages(data)
    if pages > MAX_PAGES:
        raise HTTPException(status_code=400, detail=f"{MAX_PAGES}ページまでの PDF にしてください。")
    now = clock.now(request.app)
    self_user = db.self_user(conn)
    name = self_user.display_name if self_user else "本人"
    with request.app.state.voice_lock:
        try:
            result = ai.read_document(
                system=professional_prompts.document_system(name),
                pdf_base64=base64.standard_b64encode(data).decode("ascii"),
                text=professional_prompts.document_user_text(file_name, now.date()),
            )
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
        advocacy._log_call(conn, "資料を読む", result, "資料の取り込み")
        read = result.value
        folder = documents_dir(request)
        folder.mkdir(parents=True, exist_ok=True)
        stored = f"{secrets.token_hex(12)}.pdf"
        (folder / stored).write_bytes(data)
        status = DocumentStatus.WAITING if name_matches(conn, read.person_name) else DocumentStatus.NAME_CHECK
        # ツイン（AI）や AI が作った資料は、項目にしない。AI の文を本人の思いや今の様子として記録に戻さないため（原則3）
        made_by_ai = made_by_ai_of(read.author, read.made_by_ai)
        if made_by_ai and status is DocumentStatus.WAITING:
            status = DocumentStatus.DONE
        log_id = advocacy._log_inference(conn, InferenceKind.DOCUMENT_READ, file_name, read.model_dump(mode="json"), [], [], result)
        with ledger.atomic(conn):
            document_id = db.insert_row(
                conn,
                "professional_documents",
                {
                    "uploader_user_id": viewer.id,
                    "author": read.author.strip() or None,
                    "kind": read.kind.value,
                    "document_date": read.document_date.strip() or None,
                    "title": read.title.strip() or file_name,
                    "file": stored,
                    "pages": pages or None,
                    "person_name_on_document": read.person_name.strip() or None,
                    "status": status.value,
                    "summary": read.summary.strip(),
                    "plan_json": json.dumps(read.care_plan.model_dump(mode="json"), ensure_ascii=False) if read.care_plan else None,
                    "has_medical": int(read.has_medical),
                    "read_log_id": log_id,
                    "client_key": client_key,
                    "created_at": now.isoformat(),
                },
            )
            if not made_by_ai:
                _insert_items(conn, viewer, read.items[:15], document_id=document_id, at=now.isoformat())
    return {"document": _document_view(conn, _document_row(conn, document_id), now)}


@router.get("/documents")
def list_documents(
    request: Request, viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """取り込んだ資料を新しい順に返す。消去した資料は返さない。"""
    _require_document_tool(conn, viewer)
    rows = conn.execute(
        "SELECT * FROM professional_documents WHERE status <> ? ORDER BY rowid DESC", (DocumentStatus.WITHDRAWN.value,)
    ).fetchall()
    now = clock.now(request.app)
    # 資料の一覧で、この人がその資料についてツインと話した数を出す（資料の画面、app/document_chat.py）
    chats = dict(
        conn.execute(
            "SELECT document_id, COUNT(*) FROM document_chats WHERE asker_user_id = ? GROUP BY document_id", (viewer.id,)
        ).fetchall()
    )
    return {"items": [{**_document_view(conn, row, now), "chat_count": chats.get(row["id"], 0)} for row in rows]}


@router.get("/documents/{document_id}")
def read_document(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_document_tool(conn, viewer)
    return {"document": _document_view(conn, _document_row(conn, document_id), clock.now(request.app))}


REVIEW_FAILED_MESSAGE = "いま、資料のレビューを書けませんでした。少し時間をおいて、もう一度お試しください。"


@router.post("/documents/{document_id}/review")
def review_document(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """渡された資料を、ツインが本人のセルフと照らしてレビューし、文章で返す。作ってあれば AI を呼ばずに返す。

    資料を渡したあと、項目の一覧より先に専門職へ返す答え（tyumajinさんの決定、2026-11-06）。
    名前の確かめ待ちの資料は、本人の資料と確かめるまでレビューしない（別の人の資料の中身を広げないため）。
    関連するテスト：tests/test_professional_records.py の test_資料をセルフと照らしてレビューし_作ってあれば呼ばない
    """
    _require_document_tool(conn, viewer)
    row = _document_row(conn, document_id)
    if row["status"] == DocumentStatus.NAME_CHECK.value:
        raise HTTPException(status_code=400, detail="資料の名前を確かめてから、レビューします。")
    with request.app.state.voice_lock:
        existing = review_of(conn, document_id)
        if existing is not None:
            return {"review": existing}
        document = _document_view(conn, row)
        self_name = advocacy_voice._self_name(conn)
        task = f"専門職が渡した資料（{document['kind']}、{document.get('title') or ''}）を、{self_name}の暮らしと思いに照らしてレビューしてください。"
        try:
            material = advocacy_voice.gather(conn, viewer, task)
            if material.selfhood_body:
                model_text = selfhood_prompts.answer_model_text(material.selfhood_body, material.element_lines[1:], material.heart_lines)
            else:
                model_text = advocacy_prompts.voice_model_text(material.element_lines)
            # 専門職が入力したアセスメントなども照らす（tyumajinさんの決定）。レビューはケアプランの道具を持つ専門職だけが読むので、
            # 本人と家族に見せない記録も渡せる。care_form_records がこのファイルを読み込むので、ここで読み込む（輪にしないため）
            from app import care_form_records

            text = professional_prompts.document_review_user_text(
                document, document["items"], material.now_lines, material.word_lines, care_form_records.care_form_lines(conn)
            )
            result = advocacy_voice.ask_without_medical_orders(
                conn,
                lambda extra: ai.review_document(system=professional_prompts.document_review_system(self_name), model_text=model_text, text=text + extra),
                "資料のレビュー",
                "資料のレビュー",
            )
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=REVIEW_FAILED_MESSAGE) from error
        log_id = advocacy._log_inference(conn, InferenceKind.DOCUMENT_REVIEW, task, result.value.model_dump(mode="json"), material.elements, [], result)
        with ledger.atomic(conn):
            db.insert_row(conn, "document_reviews", {"document_id": document_id, "log_id": log_id, "created_at": clock.now(request.app).isoformat()})
    return {"review": review_of(conn, document_id)}


@router.get("/documents/{document_id}/file")
def document_file(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    """元の PDF を返す。専門職が原文と照らせるようにするため。"""
    _require_document_tool(conn, viewer)
    row = _document_row(conn, document_id)
    path = documents_dir(request) / row["file"]
    if not row["file"] or not path.exists():
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(path.read_bytes(), media_type="application/pdf")


@router.post("/documents/{document_id}/person-ok")
def confirm_person(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """名前が合わなかった資料を、専門職が「この人の資料です」と確かめる。確かめると項目が見える。"""
    _require_document_tool(conn, viewer)
    row = _document_row(conn, document_id)
    if row["status"] == DocumentStatus.NAME_CHECK.value:
        with ledger.atomic(conn):
            conn.execute(
                "UPDATE professional_documents SET status = ? WHERE id = ?", (DocumentStatus.WAITING.value, document_id)
            )
    return {"document": _document_view(conn, _document_row(conn, document_id), clock.now(request.app))}


# ---- 消去（別の人の資料を取り込んでしまったとき） ----
# tyumajinさんの決定（2026-11-06）：消去ボタンを付ける。AI が処理したものは、その前の時点まで戻す。
# 時間が経ちすぎると戻せないのは仕方がない（ERASE_HOURS まで）。
# 戻す範囲：資料、ファイル、読んだ記録、項目、残した記録（言明と、その確かめやお休みの行、発言、会話、解釈）、
# この資料のケアプランのチェック、残したあとに作られたツインの考え、手紙、1枚の紙。
# 残したあとのものは、どの答えがこの記録を読んだかを記録していないので、残した時点より後に作られたものをすべて戻す。
# API へ送った中身は取り消せない。そのため自分用の起動（実データ）では取り込みを止めている。


def _ids(rows) -> list[int]:
    return [row[0] for row in rows]


def _marks(values: list) -> str:
    return ", ".join("?" for _ in values)


def erase_targets(conn: sqlite3.Connection, document: sqlite3.Row) -> dict[str, list[int]]:
    """消去で戻すものの番号を集める。下見と消去で同じものを使う。"""
    kept = conn.execute(
        "SELECT statement_id, decided_at FROM professional_items WHERE document_id = ? AND status = ?",
        (document["id"], ProfessionalItemStatus.KEPT.value),
    ).fetchall()
    statements = [row["statement_id"] for row in kept if row["statement_id"]]
    # 本人の確かめやお休みで足された行は、元の行を指して連なる。連なりごと戻す
    frontier = list(statements)
    while frontier:
        found = _ids(conn.execute(f"SELECT id FROM statements WHERE replaces_id IN ({_marks(frontier)})", frontier))
        frontier = [item for item in found if item not in statements]
        statements.extend(frontier)
    utterances: list[int] = []
    for statement_id in statements:
        row = conn.execute("SELECT source_utterance_ids FROM statements WHERE id = ?", (statement_id,)).fetchone()
        utterances.extend(item for item in json.loads(row[0]) if item not in utterances)
    conversations = (
        sorted(set(_ids(conn.execute(f"SELECT conversation_id FROM utterances WHERE id IN ({_marks(utterances)}) AND conversation_id IS NOT NULL", utterances))))
        if utterances
        else []
    )
    since = min((row["decided_at"] for row in kept if row["decided_at"]), default=None)
    later = (lambda table: _ids(conn.execute(f"SELECT id FROM {table} WHERE created_at >= ?", (since,)))) if since else (lambda table: [])
    plans = _ids(conn.execute("SELECT id FROM advocacy_plans WHERE client_key = ?", (f"document-{document['id']}",)))
    return {
        "statements": statements,
        "utterances": utterances,
        "conversations": conversations,
        "voices": later("advocacy_voices"),
        "plans": plans,
        "letters": later("advocacy_letters"),
        "sheets": later("advocacy_sheets"),
    }


def _erase_preview(conn: sqlite3.Connection, document: sqlite3.Row, now: datetime) -> dict:
    targets = erase_targets(conn, document)
    kept = conn.execute(
        "SELECT COUNT(*) FROM professional_items WHERE document_id = ? AND status = ?",
        (document["id"], ProfessionalItemStatus.KEPT.value),
    ).fetchone()[0]
    # 返した手紙は、紙で渡っているかもしれない。画面からは消すが、紙は取り戻せないことを伝える
    returned = (
        conn.execute(
            f"SELECT COUNT(*) FROM advocacy_letters WHERE id IN ({_marks(targets['letters'])}) AND status = ?",
            [*targets["letters"], LetterStatus.RETURNED.value],
        ).fetchone()[0]
        if targets["letters"]
        else 0
    )
    can_erase = now <= erase_until(document)
    return {
        "can_erase": can_erase,
        "reason": None if can_erase else f"取り込んでから{ERASE_HOURS}時間を過ぎたので、消去できません。AI の処理が積み重なり、前の時点に戻せないためです。",
        "erase_until": erase_until(document).isoformat(),
        "counts": {
            "資料": 1,
            "残した記録": kept,
            "ツインの考え": len(targets["voices"]),
            "ケアプランのチェック": len(targets["plans"]),
            "手紙": len(targets["letters"]),
            "1枚の紙": len(targets["sheets"]),
            # 資料についてのチャット（app/document_chat.py）も一緒に消えることを、消去の前に見せる
            "資料についてのチャット": conn.execute(
                "SELECT COUNT(*) FROM document_chats WHERE document_id = ?", (document["id"],)
            ).fetchone()[0],
        },
        "returned_letters": returned,
    }


@router.get("/documents/{document_id}/erase-preview")
def erase_preview(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """消去で消えるものを数えて返す。押す前に、何が戻るかを人が見られるようにする。AI は呼ばない。"""
    _require_document_tool(conn, viewer)
    return {"preview": _erase_preview(conn, _document_row(conn, document_id), clock.now(request.app))}


def _delete_logs(conn: sqlite3.Connection, log_ids: list[int | None]) -> None:
    ids = [item for item in log_ids if item]
    if ids:
        conn.execute(f"DELETE FROM inference_logs WHERE id IN ({_marks(ids)})", ids)


@router.post("/documents/{document_id}/erase")
def erase_document(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """資料を消去し、取り込む前の時点に戻す。台帳の決まりの例外（db.erase_appended_rows）。

    関連するテスト：tests/test_professional_records.py の test_消去すると残した記録も取り込む前に戻る
    """
    _require_document_tool(conn, viewer)
    document = _document_row(conn, document_id)
    preview = _erase_preview(conn, document, clock.now(request.app))
    if not preview["can_erase"]:
        raise HTTPException(status_code=400, detail=preview["reason"])
    with request.app.state.voice_lock:
        targets = erase_targets(conn, document)
        with ledger.atomic(conn):
            # 項目と、残した記録（解釈、言明の連なり、発言、会話の順。参照される側をあとに消す）
            conn.execute(
                "DELETE FROM professional_item_places WHERE item_id IN (SELECT id FROM professional_items WHERE document_id = ?)", (document_id,)
            )
            conn.execute("DELETE FROM professional_items WHERE document_id = ?", (document_id,))
            if targets["statements"]:
                conn.execute(f"DELETE FROM interpretations WHERE statement_id IN ({_marks(targets['statements'])})", targets["statements"])
            db.erase_appended_rows(conn, "statements", targets["statements"])
            db.erase_appended_rows(conn, "utterances", targets["utterances"])
            if targets["conversations"]:
                conn.execute(f"DELETE FROM conversations WHERE id IN ({_marks(targets['conversations'])})", targets["conversations"])
            # 残したあとに AI が作ったもの
            for voice_id in targets["voices"]:
                log = conn.execute("SELECT log_id FROM advocacy_voices WHERE id = ?", (voice_id,)).fetchone()
                conn.execute("DELETE FROM advocacy_voices WHERE id = ?", (voice_id,))
                _delete_logs(conn, [log["log_id"]])
            for plan_id in targets["plans"]:
                plan = conn.execute("SELECT * FROM advocacy_plans WHERE id = ?", (plan_id,)).fetchone()
                conn.execute("DELETE FROM advocacy_plan_choices WHERE plan_id = ?", (plan_id,))
                conn.execute("DELETE FROM advocacy_plans WHERE id = ?", (plan_id,))
                _delete_logs(conn, [plan["first_log_id"], plan["objection_log_id"], plan["second_log_id"]])
            for letter_id in targets["letters"]:
                letter = conn.execute("SELECT draft_log_id FROM advocacy_letters WHERE id = ?", (letter_id,)).fetchone()
                conn.execute("DELETE FROM advocacy_letter_versions WHERE letter_id = ?", (letter_id,))
                conn.execute("DELETE FROM advocacy_letters WHERE id = ?", (letter_id,))
                _delete_logs(conn, [letter["draft_log_id"]])
            for sheet_id in targets["sheets"]:
                sheet = conn.execute("SELECT log_id FROM advocacy_sheets WHERE id = ?", (sheet_id,)).fetchone()
                conn.execute("DELETE FROM advocacy_sheets WHERE id = ?", (sheet_id,))
                _delete_logs(conn, [sheet["log_id"]])
            # レビュー、資料、読んだ記録
            reviews = [row["log_id"] for row in conn.execute("SELECT log_id FROM document_reviews WHERE document_id = ?", (document_id,))]
            conn.execute("DELETE FROM document_reviews WHERE document_id = ?", (document_id,))
            _delete_logs(conn, reviews)
            # 資料についてのチャット（問いと、ツインの代弁の推論の記録）。document_chats は資料を外部キーで指すので、
            # 先に消さないと資料を消せず、外部キーの違反で 500 になっていた
            # 関連するテスト：tests/test_document_chat.py の test_チャットした資料も消去でき_チャットも一緒に消える
            chats = [row["log_id"] for row in conn.execute("SELECT log_id FROM document_chats WHERE document_id = ?", (document_id,))]
            conn.execute("DELETE FROM document_chats WHERE document_id = ?", (document_id,))
            _delete_logs(conn, chats)
            conn.execute("DELETE FROM professional_documents WHERE id = ?", (document_id,))
            _delete_logs(conn, [document["read_log_id"]])
    if document["file"]:
        (documents_dir(request) / document["file"]).unlink(missing_ok=True)
    return {"erased": preview["counts"], "returned_letters": preview["returned_letters"]}


# ---- 項目を残す ----


def _statement_text(kind: str, text: str, document: sqlite3.Row | None) -> str:
    """台帳に入れる文。聞き取りは聞き取りと分かるようにし、資料なら資料の種類を添える。"""
    body = text.strip()
    if ProfessionalItemKind(kind) in _HEARD:
        body = f"{body}（{kind}）"
    if document is not None:
        body = f"{body}（{document['kind']}より）"
    return body


@router.post("/items/{item_id}/decide")
def decide_item(
    body: DecideInput,
    request: Request,
    item_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """項目を残すか決める。残すと、今の様子の言明として台帳に入る。決めた項目は決め直せない（台帳は消せないため）。"""
    advocacy._require_supporter(conn, viewer)
    row = conn.execute("SELECT * FROM professional_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    document = None
    if row["document_id"]:
        _require_document_tool(conn, viewer)
        document = _document_row(conn, row["document_id"])
        if document["status"] == DocumentStatus.NAME_CHECK.value:
            raise HTTPException(status_code=400, detail="先に、だれの資料かを確かめてください。")
    elif row["teller_user_id"] != viewer.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if row["status"] != ProfessionalItemStatus.WAITING.value:
        return {"item": _item_view(conn, row)}
    now = clock.now(request.app)
    with ledger.atomic(conn):
        if body.decision == "残す":
            self_user = db.self_user(conn)
            teller = db.get_user(conn, row["teller_user_id"])
            if self_user is None or teller is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            text = (body.text or row["text"]).strip()
            statement = observations.save_observation(
                conn,
                teller,
                self_user,
                _statement_text(row["kind"], text, document),
                Theme.HOME,
                now,
                trigger=DOCUMENT_TRIGGER if document is not None else TELL_TRIGGER,
                share_scope=scope_of(document),
            )
            conn.execute(
                "UPDATE professional_items SET status = ?, text = ?, statement_id = ?, decided_by_user_id = ?, decided_at = ? WHERE id = ?",
                (ProfessionalItemStatus.KEPT.value, text, statement.id, viewer.id, now.isoformat(), item_id),
            )
        else:
            conn.execute(
                "UPDATE professional_items SET status = ?, decided_by_user_id = ?, decided_at = ? WHERE id = ?",
                (ProfessionalItemStatus.DROPPED.value, viewer.id, now.isoformat(), item_id),
            )
        if document is not None:
            waiting = conn.execute(
                "SELECT 1 FROM professional_items WHERE document_id = ? AND status = ? LIMIT 1",
                (document["id"], ProfessionalItemStatus.WAITING.value),
            ).fetchone()
            if not waiting:
                conn.execute(
                    "UPDATE professional_documents SET status = ? WHERE id = ?", (DocumentStatus.DONE.value, document["id"])
                )
    row = conn.execute("SELECT * FROM professional_items WHERE id = ?", (item_id,)).fetchone()
    return {"item": _item_view(conn, row)}


# ---- 取り込んだケアプランをチェックする ----


def plan_view_of(document: sqlite3.Row) -> dict:
    """取り込んだケアプランを、ケアプラン案の第1版の形にする。書かれた文のまま渡す。"""
    plan = json.loads(document["plan_json"])
    lines = []
    if plan.get("self_intent"):
        lines.append(f"本人の意向（第1表）：{plan['self_intent']}")
    if plan.get("family_intent"):
        lines.append(f"家族の意向（第1表）：{plan['family_intent']}")
    if plan.get("policy"):
        lines.append(f"総合的な援助の方針（第1表）：{plan['policy']}")
    items = []
    for need in plan.get("needs", []):
        support = "／".join(
            part
            for part in (
                f"長期目標：{need['long_term_goal']}" if need.get("long_term_goal") else "",
                f"短期目標：{need['short_term_goal']}" if need.get("short_term_goal") else "",
                f"サービス：{'、'.join(need['services'])}" if need.get("services") else "",
            )
            if part
        )
        items.append({"aim": f"ニーズ（第2表）：{need['need']}", "support": support})
    source = "取り込んだケアプラン"
    if document["author"] or document["document_date"]:
        source += f"（{'、'.join(part for part in (document['author'], document['document_date']) if part)}）"
    return {"source": source, "summary": "\n".join(lines) or (document["summary"] or ""), "items": items}


@router.post("/documents/{document_id}/check")
def check_care_plan(
    request: Request,
    document_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """取り込んだケアプランを、ツインがセルフをもとにチェックする。第1版として入れ、異議を作る。"""
    _require_document_tool(conn, viewer)
    document = _document_row(conn, document_id)
    if document["kind"] != DocumentKind.CARE_PLAN.value or not document["plan_json"]:
        raise HTTPException(status_code=400, detail="ケアプランの資料ではないので、チェックできません。")
    if document["status"] == DocumentStatus.NAME_CHECK.value:
        raise HTTPException(status_code=400, detail="先に、だれの資料かを確かめてください。")
    plan_id = advocacy_plan.add_imported_plan(conn, request, viewer, f"document-{document_id}", plan_view_of(document))
    plan = advocacy_plan.object_to_plan(advocacy_plan.StepInput(), request, plan_id, viewer, conn)["plan"]
    return {"plan": plan}
