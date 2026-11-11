"""本人の今を代弁する手紙。

役割：専門職が頼むと、ツインがセルフをもとに、本人の今の暮らしと今の気持ちを本人として書く。本人か、託された家族が
     読んで直してから「返す」を押すと、専門職に届く。A4 1枚で印刷でき、内容に合った水彩の挿絵を入れる。
4つの極：4. 10年後に代弁する。
守る原則：1（決めるのは人。返すか出さないかは本人と託された家族が決める）、3（ツインの文と記録を分ける）、
          9（本人の言葉は番号で指し、本文は記録のまま）、12（紙に必ず「ツイン（AI）」と出す）、16（言葉を並べず代弁する）、
          17（診断と服薬の指示はしない。体や通院のことは、今の暮らしと気持ちとして書いてよい）。

決まり（tyumajinさんの決定、2026-11-06）
- 確かめていない手紙は、専門職に届かない。専門職には「確かめています」とだけ見せる。
- 本人か託された家族が「出さない」と押したら出さない。
- 紙の札はプログラムが付ける。「ツイン（AI）が◯◯さんに代わって書きました。」に、確かめた人で「◯◯さんも」「ご家族も」
  「◯◯さんとご家族も確認されました。」を続ける。
- 本人の直しは、直した文を本人の言葉として台帳に足し、本人が返したときに確認済みにする（セルフの重み4）。
  「本人の訂正」は元の行を指す訂正の処理からしか作れないので使わない。家族の直しは家族の証言として足す（話した人と、誰の話かを分ける）。
  場所は「チャット」にする。「日記と会話」にすると、ツインにだけ話した言葉と見なされ、専門職から見えなくなるため。
- 挿絵は static/letter_art.json の一覧から AI が番号で選ぶ。選ばなかったときは通年の絵を入れる。絵のファイルが1枚もなければ、挿絵なしで出す。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（Opus。app/config.py）、
プロンプトキャッシュ（セルフのブロック）、考える深さの指定。

関連するテスト：tests/test_advocacy_letter.py
設計：docs/decisions.md の「本人の今を代弁する手紙」
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import (
    advocacy,
    advocacy_prompts,
    advocacy_voice,
    ai,
    clock,
    config,
    consent,
    db,
    ledger,
    observations,
    professional_prompts,
    professional_records,
    professions,
    selfhood_prompts,
)
from app.auth import NOT_FOUND, current_user, get_conn
from app import made_documents
from app.schemas import (
    ConversationDraft,
    DocumentStatus,
    InferenceKind,
    LetterStatus,
    MadeDocumentKind,
    Period,
    Place,
    Role,
    ShareScope,
    Source,
    StatementDraft,
    Theme,
    User,
    UtteranceDraft,
)

router = APIRouter(prefix="/api")

PURPOSES = ("今月の様子", "会議の前に", "新しい人が来る前に", "そのほか")
EDIT_TRIGGER = "手紙の直し"
AI_FAILED_MESSAGE = "いま、手紙を書けませんでした。少し時間をおいて、もう一度お試しください。"
ART_LIST = config.STATIC_DIR / "letter_art.json"
ART_DIR = config.STATIC_DIR / "letter_art"
DOCUMENT_LINES = 5
_SENTENCE_END = re.compile(r"(?<=[。！？!?])|\n")


class LetterInput(BaseModel):
    """手紙を頼む入力。"""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["今月の様子", "会議の前に", "新しい人が来る前に", "そのほか"] = "今月の様子"
    client_key: str = Field(min_length=1, max_length=64)


class EditInput(BaseModel):
    """本人か託された家族が直した手紙。"""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1200)
    requests: list[str] = Field(default=[], max_length=3)


def honorific(name: str) -> str:
    """名前に「さん」を付ける。付いていれば、そのまま。"""
    return name if name.endswith("さん") else f"{name}さん"


def label_for(self_name: str, roles: list[str]) -> str:
    """紙に印刷する札。確かめた人で書き分ける。

    関連するテスト：tests/test_advocacy_letter.py の test_札は確かめた人で書き分ける
    """
    name = honorific(self_name)
    head = f"ツイン（AI）が{name}に代わって書きました。"
    has_self = Role.SELF.value in roles
    has_family = Role.FAMILY.value in roles
    if has_self and has_family:
        return f"{head}{name}とご家族も確認されました。"
    if has_family:
        return f"{head}ご家族も確認されました。"
    if has_self:
        return f"{head}{name}も確認されました。"
    return head


# ---- 挿絵 ----


def art_list() -> list[dict]:
    """挿絵の一覧を読む。一覧がなければ空。"""
    if not ART_LIST.exists():
        return []
    return json.loads(ART_LIST.read_text(encoding="utf-8"))


def art_lines(arts: list[dict]) -> list[str]:
    """AI に渡す挿絵の一覧。絵のファイルがまだないものは渡さない。選ばれても出せないため。"""
    return [
        f"[{number}] {art['title']}（{art['season']}）：{art['topics']}"
        for number, art in enumerate(arts, start=1)
        if (ART_DIR / art["file"]).exists()
    ]


def art_view(file: str | None) -> dict | None:
    """挿絵を画面に返す形にする。ファイルがなければ空にして、挿絵なしで出す。"""
    if not file or not (ART_DIR / file).exists():
        return None
    title = next((art["title"] for art in art_list() if art["file"] == file), "")
    return {"src": f"letter_art/{file}", "title": title}


def fallback_art(arts: list[dict]) -> str | None:
    """AI が絵を選ばなかったときの絵。ファイルのある通年の絵の先頭、なければファイルのある絵の先頭。

    挿絵のない手紙にしないため（ご本人の依頼、2026-11-07）。絵のファイルが1枚もなければ空。
    """
    existing = [art for art in arts if (ART_DIR / art["file"]).exists()]
    year_round = [art for art in existing if art.get("season") == "通年"]
    chosen = (year_round or existing)[:1]
    return chosen[0]["file"] if chosen else None


def _picked_art(arts: list[dict], number: int | None) -> str | None:
    """AI が番号で選んだ絵。選ばなかったか、その絵のファイルがなければ、通年の絵を入れる。"""
    if number is not None and 1 <= number <= len(arts):
        file = arts[number - 1]["file"]
        if (ART_DIR / file).exists():
            return file
    return fallback_art(arts)


# ---- 書く ----


def _document_lines(conn: sqlite3.Connection) -> list[str]:
    """専門職の資料の要点を、新しい順に渡す。名前の確かめ待ちは渡さない。

    ご家族も受け取る資料（ケアプランなど）だけを渡す。手紙の下書きは託された家族が確かめることがあり、
    アセスメントなどの中身を家族に見せないため（tyumajinさんの決定）。
    """
    kinds = sorted(professional_records.FAMILY_VISIBLE_KINDS)
    rows = conn.execute(
        f"SELECT * FROM professional_documents WHERE status IN (?, ?) AND kind IN ({', '.join('?' for _ in kinds)}) "
        "ORDER BY rowid DESC LIMIT ?",
        (DocumentStatus.WAITING.value, DocumentStatus.DONE.value, *kinds, DOCUMENT_LINES),
    ).fetchall()
    return [f"{row['document_date'] or row['created_at'][:10]}／{row['kind']}（{row['author'] or '作成者不明'}）：{row['summary']}" for row in rows]


def strip_address_and_name(body: str, recipient: str, self_name: str) -> str:
    """本文の頭の宛名と、最後の署名を外す。宛名と札は紙にプログラムが印刷し、本人の名前で署名しないため。

    AI に頼んでも、宛名と署名を書いた回があった（2026-11-06 の試し）。
    関連するテスト：tests/test_advocacy_letter.py の test_本文の宛名と署名は外す
    """
    lines = body.strip().splitlines()
    plain_recipient = recipient.removesuffix("さん")
    plain_self = self_name.removesuffix("さん")
    if lines and lines[0].strip().startswith(plain_recipient) and lines[0].strip().rstrip("。、").endswith("へ"):
        lines = lines[1:]
    if lines and plain_self in lines[-1] and len(lines[-1].strip()) <= len(plain_self) + 4:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def without_professional_only(conn: sqlite3.Connection, material: advocacy_voice.Material, viewer: User) -> None:
    """手紙の材料から、専門職だけに見せる記録を外す。手紙は託された家族も確かめるため（tyumajinさんの決定）。

    関連するテスト：tests/test_advocacy_letter.py の test_手紙の材料に専門職だけの記録を入れない
    """
    hidden = ShareScope.PROFESSIONAL_ONLY.value
    material.now_lines = [
        f"{item['date']}／{item['speaker_label'] or item['speaker']}：{item['body']}"
        for item in observations.now_view(conn, viewer, config.MODEL_LIMITS["now"])
        if conn.execute("SELECT share_scope FROM statements WHERE id = ?", (item["statement_id"],)).fetchone()[0] != hidden
    ]
    kept = [item for item in material.statements if item.share_scope is not ShareScope.PROFESSIONAL_ONLY]
    if len(kept) != len(material.statements):
        material.statements = kept
        material.word_lines = advocacy_voice._word_lines(conn, kept)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text) if part and part.strip()]


def _letter_row(conn: sqlite3.Connection, letter_id: int) -> sqlite3.Row:
    # 削除の印のある手紙は、頼んだ人にも確かめる人にも、ないものとして扱う（app/made_documents.py）
    row = conn.execute(f"SELECT * FROM advocacy_letters WHERE id = ? AND {made_documents.not_deleted(MadeDocumentKind.LETTER)}", (letter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def _versions(conn: sqlite3.Connection, letter_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM advocacy_letter_versions WHERE letter_id = ? ORDER BY version", (letter_id,)
    ).fetchall()


def _draft(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["draft_log_id"],)).fetchone()
    return json.loads(log["result"])


def _editor_roles(conn: sqlite3.Connection, letter_id: int) -> list[str]:
    roles = []
    for version in _versions(conn, letter_id):
        if version["edited_by_user_id"]:
            editor = db.get_user(conn, version["edited_by_user_id"])
            if editor and editor.role.value not in roles:
                roles.append(editor.role.value)
    return roles


def _paper(conn: sqlite3.Connection, row: sqlite3.Row, roles: list[str]) -> dict:
    """紙の中身。いちばん新しい版の本文と、引いた言葉と、挿絵と、札。"""
    latest = _versions(conn, row["id"])[-1]
    draft = _draft(conn, row)
    requester = db.get_user(conn, row["requester_user_id"])
    self_name = advocacy_voice._self_name(conn)
    return {
        "id": row["id"],
        "purpose": row["purpose"],
        "status": row["status"],
        "recipient": honorific(requester.display_name) if requester else "専門職",
        "self_name": honorific(self_name),
        "written_on": row["created_at"][:10],
        "body": latest["body"],
        "requests": json.loads(latest["requests"]),
        "quote": draft.get("quote"),
        "illustration": art_view(row["illustration"]),
        "label": label_for(self_name, roles),
        "edited": len(_versions(conn, row["id"])) > 1,
    }


def _requester_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """専門職に見せる形。返されるまでは、状態だけを見せる。"""
    base = {"id": row["id"], "purpose": row["purpose"], "status": row["status"], "created_at": row["created_at"], "returned_at": row["returned_at"]}
    if row["status"] != LetterStatus.RETURNED.value:
        return base
    draft = _draft(conn, row)
    roles = json.loads(row["checked_roles"] or "[]")
    return {**base, "paper": _paper(conn, row, roles), "pieces": draft.get("pieces", []), "reading": draft.get("reading")}


@router.post("/advocacy/letters", status_code=201)
def make_letter(
    body: LetterInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """手紙を頼む。ツインが下書きを書き、本人の確かめ待ちにする。同じ client_key なら AI を呼ばずに返す。"""
    advocacy._require_tool(conn, viewer, professions.LETTER)

    def existing() -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM advocacy_letters WHERE requester_user_id = ? AND client_key = ?", (viewer.id, body.client_key)
        ).fetchone()

    with request.app.state.voice_lock:
        row = existing()
        if row is None:
            if not advocacy_voice.has_material(conn, viewer):
                raise HTTPException(status_code=400, detail=advocacy.NOT_YET)
            now = clock.now(request.app)
            self_name = advocacy_voice._self_name(conn)
            recipient = honorific(viewer.display_name)
            task = f"{recipient}に、{honorific(self_name)}の今の暮らしと今の気持ちを伝える手紙を書いてください。目的：{body.purpose}"
            arts = art_list()
            try:
                material = advocacy_voice.gather(conn, viewer, task)
                without_professional_only(conn, material, viewer)
                system = professional_prompts.letter_system(honorific(self_name), recipient)
                if material.selfhood_body:
                    model_text = selfhood_prompts.answer_model_text(material.selfhood_body, material.element_lines[1:], material.heart_lines)
                else:
                    model_text = advocacy_prompts.voice_model_text(material.element_lines)
                text = professional_prompts.letter_user_text(
                    body.purpose, now.date(), material.now_lines, material.word_lines, art_lines(arts), _document_lines(conn)
                )
                result = advocacy_voice.ask_without_medical_orders(
                    conn,
                    lambda extra: ai.write_letter(system=system, model_text=model_text, text=text + extra),
                    "手紙",
                    "手紙",
                )
            except advocacy._AI_ERRORS as error:
                raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
            draft = result.value
            quotes = advocacy_voice.pick_quotes([draft.quote_number] if draft.quote_number else [], material.statements)
            quote = advocacy.statement_view(conn, quotes[0], self_name) if quotes else None
            pieces = (
                advocacy_voice._piece_views(draft.piece_numbers, material.pieces)
                if material.pieces
                else advocacy_voice._element_views(conn, draft.piece_numbers, material.elements)
            )
            view = {
                "body": strip_address_and_name(draft.body, recipient, self_name),
                "requests": [item.strip() for item in draft.requests],
                "quote": quote,
                "pieces": pieces,
                "reading": advocacy_voice.reading_note(material),
            }
            log_id = advocacy._log_inference(
                conn, InferenceKind.LETTER, task, view, material.elements, [quotes[0].id] if quotes else [], result
            )
            with ledger.atomic(conn):
                letter_id = db.insert_row(
                    conn,
                    "advocacy_letters",
                    {
                        "requester_user_id": viewer.id,
                        "purpose": body.purpose,
                        "status": LetterStatus.CHECKING.value,
                        "draft_log_id": log_id,
                        "illustration": _picked_art(arts, draft.illustration_number),
                        "quote_statement_id": quotes[0].id if quotes else None,
                        "client_key": body.client_key,
                        "created_at": now.isoformat(),
                    },
                )
                db.insert_row(
                    conn,
                    "advocacy_letter_versions",
                    {
                        "letter_id": letter_id,
                        "version": 1,
                        "body": view["body"],
                        "requests": json.dumps(view["requests"], ensure_ascii=False),
                        "edited_by_user_id": None,
                        "created_at": now.isoformat(),
                    },
                )
            row = existing()
    return {"letter": _requester_view(conn, row)}


@router.get("/advocacy/letters")
def list_letters(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人が頼んだ手紙を、新しい順に返す。返された手紙だけ中身を見せる。"""
    advocacy._require_tool(conn, viewer, professions.LETTER)
    rows = conn.execute(
        f"SELECT * FROM advocacy_letters WHERE requester_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.LETTER)} ORDER BY rowid DESC", (viewer.id,)
    ).fetchall()
    return {"items": [_requester_view(conn, row) for row in rows]}


@router.get("/advocacy/letters/{letter_id}")
def read_letter(
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    advocacy._require_tool(conn, viewer, professions.LETTER)
    row = _letter_row(conn, letter_id)
    if row["requester_user_id"] != viewer.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"letter": _requester_view(conn, row)}


# ---- 本人と託された家族が確かめる ----


def _require_checker(conn: sqlite3.Connection, viewer: User) -> None:
    """手紙を確かめられるのは、本人と、立場が家族の託された人だけ。"""
    if not consent.can_act_for_self(conn, viewer):
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def _checking_row(conn: sqlite3.Connection, letter_id: int) -> sqlite3.Row:
    row = _letter_row(conn, letter_id)
    if row["status"] != LetterStatus.CHECKING.value:
        raise HTTPException(status_code=400, detail="この手紙は、もう確かめが終わっています。")
    return row


def _check_view(conn: sqlite3.Connection, row: sqlite3.Row, viewer: User) -> dict:
    roles = _editor_roles(conn, row["id"])
    if viewer.role.value not in roles:
        roles.append(viewer.role.value)
    return {"letter": {**_paper(conn, row, roles), "checker_role": viewer.role.value}}


@router.get("/letters/pending")
def pending_letters(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """確かめ待ちの手紙を返す。本人の画面のお知らせに使う。"""
    _require_checker(conn, viewer)
    rows = conn.execute(
        f"SELECT * FROM advocacy_letters WHERE status = ? AND {made_documents.not_deleted(MadeDocumentKind.LETTER)} ORDER BY rowid DESC",
        (LetterStatus.CHECKING.value,),
    ).fetchall()
    items = []
    for row in rows:
        requester = db.get_user(conn, row["requester_user_id"])
        items.append(
            {
                "id": row["id"],
                "recipient": honorific(requester.display_name) if requester else "専門職",
                # 知らせの行の頭に、頼んだ人の顔のアイコンを出すため。画面は番号で人を探す（static/letter_notice.js）
                "requester_id": requester.id if requester else None,
                "purpose": row["purpose"],
                "created_at": row["created_at"],
            }
        )
    return {"items": items}


@router.get("/letters/{letter_id}/check")
def check_letter(
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """確かめる画面の中身。いちばん新しい版を返す。札は、今確かめている人を入れた形で見せる。"""
    _require_checker(conn, viewer)
    return _check_view(conn, _letter_row(conn, letter_id), viewer)


@router.post("/letters/{letter_id}/edit")
def edit_letter(
    body: EditInput,
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """直した手紙を、新しい版として足す。前の版は残す。"""
    _require_checker(conn, viewer)
    row = _checking_row(conn, letter_id)
    text = body.body.strip()
    if "「" in text or "『" in text:
        raise HTTPException(status_code=400, detail="鉤括弧は使わずに書いてください。引いた言葉は、記録のまま下に載ります。")
    versions = _versions(conn, letter_id)
    requests = [item.strip() for item in body.requests if item.strip()]
    if versions[-1]["body"] != text or json.loads(versions[-1]["requests"]) != requests:
        with ledger.atomic(conn):
            db.insert_row(
                conn,
                "advocacy_letter_versions",
                {
                    "letter_id": letter_id,
                    "version": versions[-1]["version"] + 1,
                    "body": text,
                    "requests": json.dumps(requests, ensure_ascii=False),
                    "edited_by_user_id": viewer.id,
                    "created_at": clock.now(request.app).isoformat(),
                },
            )
    return _check_view(conn, row, viewer)


def _record_edits(conn: sqlite3.Connection, letter_id: int, returner: User, at) -> list[int]:
    """直しで足された文を、直した人の言葉として台帳に足す。本人の直しは、本人が返したときに確認済みにする。

    版ごとに、前の版になかった文を、その版を直した人のものとする。AI が書いた最初の版の文は足さない。
    関連するテスト：tests/test_advocacy_letter.py の test_本人の直しは本人の言葉として台帳に入り返すと確認済みになる
    """
    self_user = db.self_user(conn)
    if self_user is None:
        return []
    added: list[int] = []
    versions = _versions(conn, letter_id)
    for before, after in zip(versions, versions[1:]):
        editor = db.get_user(conn, after["edited_by_user_id"]) if after["edited_by_user_id"] else None
        if editor is None or editor.role not in (Role.SELF, Role.FAMILY):
            continue
        old = set(_sentences(before["body"])) | set(json.loads(before["requests"]))
        new = [part for part in [*_sentences(after["body"]), *json.loads(after["requests"])] if part not in old]
        if not new:
            continue
        conversation = ledger.add_conversation(
            conn,
            ConversationDraft(
                conversation_date=at.date(),
                trigger=EDIT_TRIGGER,
                theme=Theme.HOME,
                closed_at=at,
                life_stage=ledger.life_stage_on(conn, at.date()),
            ),
        )
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation.id,
                speaker_user_id=editor.id,
                text="\n".join(new),
                spoken_at=at,
                place=Place.CHAT,
            ),
        )
        source = Source.SELF_WORDS if editor.role is Role.SELF else Source.FAMILY_TESTIMONY
        for sentence in new:
            statement = ledger.append_statement(
                conn,
                StatementDraft(
                    source_utterance_ids=[utterance.id],
                    subject_user_id=self_user.id,
                    speaker_user_id=editor.id,
                    source=source,
                    body=sentence,
                    period=Period.NOW,
                    theme=Theme.HOME,
                    share_scope=ShareScope.DECISION_SUPPORT,
                    effective_date=at.date(),
                    created_at=at,
                ),
            )
            if source is Source.SELF_WORDS and returner.role is Role.SELF:
                statement = ledger.confirm_statement(conn, statement.id, actor_user_id=returner.id, at=at)
            added.append(statement.id)
    return added


@router.post("/letters/{letter_id}/return")
def return_letter(
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """確かめた手紙を専門職へ返す。直した文は台帳に足す。"""
    _require_checker(conn, viewer)
    _checking_row(conn, letter_id)
    now = clock.now(request.app)
    roles = _editor_roles(conn, letter_id)
    if viewer.role.value not in roles:
        roles.append(viewer.role.value)
    with ledger.atomic(conn):
        _record_edits(conn, letter_id, viewer, now)
        conn.execute(
            "UPDATE advocacy_letters SET status = ?, returned_by_user_id = ?, checked_roles = ?, returned_at = ? WHERE id = ?",
            (LetterStatus.RETURNED.value, viewer.id, json.dumps(roles, ensure_ascii=False), now.isoformat(), letter_id),
        )
    return _check_view(conn, _letter_row(conn, letter_id), viewer)


@router.post("/letters/{letter_id}/decline")
def decline_letter(
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """手紙を出さない。出さない手紙は専門職に届かず、あとから返すこともできない。"""
    _require_checker(conn, viewer)
    _checking_row(conn, letter_id)
    with ledger.atomic(conn):
        conn.execute("UPDATE advocacy_letters SET status = ? WHERE id = ?", (LetterStatus.DECLINED.value, letter_id))
    return _check_view(conn, _letter_row(conn, letter_id), viewer)
