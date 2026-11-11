"""本人が送る手紙。

役割：本人のホームの「手紙を書く」から使う。期間（今週・ひと月・期間を選ぶ）と送る相手（1人）と、あればあらすじを受け取り、
     ツインがその期間のツインとの会話と、相手との1対1のやり取りと、セルフをもとに、本人の人柄が伝わる標準語で下書きを書く。
     本人は文面を直し、ツインに校正してもらい、前の版に戻せる。いらない下書きは捨てられる。
     送ると相手が読めるようになり、本人はあとで見返せる。
4つの極：3. 読み解き、積み上げる（セルフを通して書く）、4. 10年後に代弁する。
守る原則：1（送るかは本人が決める）、5（版を残し、いつでも戻せる）、12（紙に「ツイン（AI）」と出す）、
          15（人だけの部屋の言葉を引かない。渡すのは送る相手との1対1のやり取りだけ）、17（診断と服薬の指示はしない）。

決まり（tyumajinさんの依頼、2026-11-06）
- 手紙の中身は、その期間にツインと話したことが土台。だれ宛てかの関係を考え、セルフを通して、その人らしい手紙にする。
- あらすじが入っていれば、それも踏まえる。挿絵は前もって作った水彩の絵の一覧から、AI が番号で選ぶ（app/advocacy_letter.py と同じ）。
- 版は足すだけ（self_letter_versions）。下書き、直し、校正、戻すのたびに1行足す。送った印も足すだけ（self_letter_sends）。
- 作る、直す、送るは本人だけ。送った手紙は、相手も読める。亡くなった方にも書ける（届けはしない）。
- 送った手紙の文は、今は台帳に本人の言葉として入れない。

直し（tyumajinさんの依頼、2026-11-07）
- 手紙は標準語で書く。会話とセルフの方言は人柄を読むために使い、手紙には写さない（app/self_letter_prompts.py）。
- 校正は文を整えるだけで、作り直さない。直す欄の文を受け取ったら、先に「直し」の版として残してから校正する。
  校正で段落が減ったり、本文が大きく短くなったりしたら、その校正は使わない（_kept_shape）。
- 挿絵は手紙ごとに描く。ツインが、これまでの水彩の挿絵を見本に、手紙の文に合う絵を SVG で描く（Sonnet）。
  描いた SVG は使ってよい要素だけに組み直してから残す（app/svg_art.py）。描き直すたびに1行足す（self_letter_arts）。
  描く前と、描けなかったときは、前もって作った水彩の絵から1つ出す（advocacy_letter.fallback_art）。挿絵なしにはしない。
- 送っていない下書きは捨てられる。行も版も消さず、印（self_letter_discards）を足して一覧と画面から外す。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（下書きは Opus、校正は Sonnet。app/config.py）、
プロンプトキャッシュ（セルフのブロック）、考える深さの指定。

関連するテスト：tests/test_self_letters.py
"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from app import advocacy, advocacy_letter, advocacy_prompts, advocacy_voice, ai, clock, db, ledger, selfhood_prompts, self_letter_prompts, svg_art
from app.auth import NOT_FOUND, current_user, get_conn, require_self
from app.schemas import InferenceKind, Place, Role, RoomKind, User

router = APIRouter(prefix="/api/self-letters")

AI_FAILED_MESSAGE = "いま、手紙を書けませんでした。少し時間をおいて、もう一度お試しください。"
PROOF_FAILED_MESSAGE = "いま、校正できませんでした。少し時間をおいて、もう一度お試しください。"
ART_FAILED_MESSAGE = "いま、挿絵を描けませんでした。前もって用意した絵のままにしています。もう一度お試しください。"
# 挿絵の見本にする、これまでの水彩の絵。ファイルがなければ見本なしで描く
ART_SAMPLE_FILE = "01.webp"
NO_TALK_MESSAGE = "この期間に、ツイン（AI）と話した記録がありません。期間を広げてください。"
SENT_MESSAGE = "この手紙は、もう送りました。送った手紙は修正できません。"
# 校正のあとの本文が、前の何割より短くなったら使わないか。標準語に整えるだけなら、ここまでは縮まない
KEPT_BODY_RATIO = 0.85
# 期間の上限の日数。読む量を増やしすぎないため
MAX_SPAN_DAYS = 92
# AI に渡す会話の文字数の上限。本人の言葉を先に残し、あふれたらツインの言葉から外す
TALK_CHARS = 9000
PAIR_CHARS = 3000
# 関係の書き方がないときの、立場ごとの言い方
_RELATION_BY_ROLE = {Role.FAMILY: "家族", Role.CONNECTED: "知り合い", Role.PROFESSIONAL: "担当の専門職"}


class DraftInput(BaseModel):
    """下書きを頼む入力。period が「期間」のときだけ start と end を使う。"""

    model_config = ConfigDict(extra="forbid")

    period: Literal["今週", "ひと月", "期間"]
    start: date | None = None
    end: date | None = None
    recipient_id: int = Field(ge=1, le=db.MAX_ID)
    outline: str = Field(default="", max_length=600)
    client_key: str = Field(min_length=1, max_length=64)


class PartsInput(BaseModel):
    """本人が直した手紙の4つの部分。"""

    model_config = ConfigDict(extra="forbid")

    salutation: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=1600)
    closing: str = Field(default="", max_length=200)
    signature: str = Field(default="", max_length=40)


class RevertInput(BaseModel):
    """戻す先の版。"""

    model_config = ConfigDict(extra="forbid")

    to_version: int = Field(ge=1, le=10000)


# ---- 期間と材料 ----


def period_of(body: DraftInput, today: date) -> tuple[date, date]:
    """期間の始まりと終わり。今日は物語の日付（clock）。今週は今日までの7日、ひと月は今日までの30日。

    関連するテスト：tests/test_self_letters.py の test_期間は今週ひと月選ぶの3つで_選ぶときは確かめる
    """
    if body.period == "今週":
        return today - timedelta(days=6), today
    if body.period == "ひと月":
        return today - timedelta(days=29), today
    if body.start is None or body.end is None:
        raise HTTPException(status_code=400, detail="期間の始まりと終わりを入れてください。")
    if body.start > body.end:
        raise HTTPException(status_code=400, detail="期間の始まりは、終わりより前の日にしてください。")
    if body.end > today:
        raise HTTPException(status_code=400, detail="期間の終わりは、今日までにしてください。")
    if (body.end - body.start).days >= MAX_SPAN_DAYS:
        raise HTTPException(status_code=400, detail=f"期間は{MAX_SPAN_DAYS}日までにしてください。")
    return body.start, body.end


def _short_date(iso: str) -> str:
    return f"{int(iso[5:7])}月{int(iso[8:10])}日"


def _within(lines: list[tuple[bool, str]], limit: int) -> list[str]:
    """上限の文字数に収める。あふれたらツインの言葉から外し、それでもあふれたら古いほうから外す。"""
    kept = list(lines)
    if sum(len(text) for _, text in kept) > limit:
        kept = [(is_self, text) for is_self, text in kept if is_self]
    while kept and sum(len(text) for _, text in kept) > limit:
        kept.pop(0)
    return [text for _, text in kept]


def talk_lines(conn: sqlite3.Connection, self_user: User, start: date, end: date) -> list[str]:
    """期間の、本人とツインの会話（日記と会話）を古い順に。ツインの部屋と確かめの会話だけを読む。

    ほかの人の発言は入れない。家族がツインに話したことを、本人の手紙に書かないため。
    """
    rows = conn.execute(
        """
        SELECT u.text, u.spoken_at, u.speaker_is_twin
        FROM utterances u
        JOIN conversations c ON c.id = u.conversation_id
        LEFT JOIN rooms r ON r.id = c.room_id
        WHERE u.place = ? AND substr(u.spoken_at, 1, 10) BETWEEN ? AND ?
          AND (u.speaker_is_twin = 1 OR u.speaker_user_id = ?)
          AND (c.room_id IS NULL OR r.kind = ?)
        ORDER BY u.spoken_at, u.rowid
        """,
        (Place.DIARY_AND_TALK.value, start.isoformat(), end.isoformat(), self_user.id, RoomKind.TWIN.value),
    ).fetchall()
    name = self_user.display_name
    lines = [
        (not row["speaker_is_twin"], f"{_short_date(row['spoken_at'])} {'ツイン' if row['speaker_is_twin'] else name}：{row['text']}")
        for row in rows
    ]
    return _within(lines, TALK_CHARS)


def pair_lines(conn: sqlite3.Connection, self_user: User, recipient: User, start: date, end: date) -> list[str]:
    """期間の、本人と送る相手の1対1の部屋のやり取りを古い順に。ほかの部屋は読まない（原則15）。"""
    room = conn.execute(
        """
        SELECT r.id FROM rooms r
        WHERE r.kind = ?
          AND EXISTS (SELECT 1 FROM room_members m WHERE m.room_id = r.id AND m.user_id = ?)
          AND EXISTS (SELECT 1 FROM room_members m WHERE m.room_id = r.id AND m.user_id = ?)
        """,
        (RoomKind.PAIR.value, self_user.id, recipient.id),
    ).fetchone()
    if room is None:
        return []
    rows = conn.execute(
        """
        SELECT m.sender_user_id, COALESCE(m.text, u.text) AS text, m.sent_at
        FROM room_messages m LEFT JOIN utterances u ON u.id = m.utterance_id
        WHERE m.room_id = ? AND m.is_twin = 0 AND substr(m.sent_at, 1, 10) BETWEEN ? AND ?
          AND COALESCE(m.text, u.text) IS NOT NULL
        ORDER BY m.sent_at, m.rowid
        """,
        (room["id"], start.isoformat(), end.isoformat()),
    ).fetchall()
    names = {self_user.id: self_user.display_name, recipient.id: recipient.display_name}
    lines = [(True, f"{_short_date(row['sent_at'])} {names.get(row['sender_user_id'], '')}：{row['text']}") for row in rows]
    return _within(lines, PAIR_CHARS)


_HONORIFICS = ("さん", "くん", "ちゃん", "先生", "様")


def called(name: str) -> str:
    """呼び方。敬称が付いていればそのまま、なければ「さん」を付ける（健太くん、井ノ口先生に「さん」を重ねない）。"""
    return name if name.endswith(_HONORIFICS) else f"{name}さん"


def relation_of(user: User) -> str:
    """本人から見た相手の関係。書いてなければ立場から言う。"""
    return (user.relation or "").strip() or _RELATION_BY_ROLE.get(user.role, "知り合い")


def _recipient(conn: sqlite3.Connection, viewer: User, recipient_id: int) -> User:
    user = db.get_user(conn, recipient_id)
    if user is None or user.id == viewer.id or user.removed_at is not None:
        raise HTTPException(status_code=400, detail="送る相手が見つかりません。")
    return user


# ---- 見せる形 ----


_NOT_DISCARDED = "NOT EXISTS (SELECT 1 FROM self_letter_discards d WHERE d.letter_id = l.id)"


def _letter_row(conn: sqlite3.Connection, letter_id: int) -> sqlite3.Row:
    """手紙の行。捨てた下書きは、ないものとして答える。"""
    row = conn.execute(f"SELECT * FROM self_letters l WHERE l.id = ? AND {_NOT_DISCARDED}", (letter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return row


def _versions(conn: sqlite3.Connection, letter_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM self_letter_versions WHERE letter_id = ? ORDER BY version", (letter_id,)).fetchall()


def _sent(conn: sqlite3.Connection, letter_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM self_letter_sends WHERE letter_id = ?", (letter_id,)).fetchone()


def label_for(author_name: str, sent: bool) -> str:
    """紙の札。ツイン（AI）が下書きを手伝ったことを必ず出す（原則12）。"""
    name = called(author_name)
    return f"ツイン（AI）が下書きを手伝い、{name}が確かめて送りました。" if sent else "ツイン（AI）が下書きを手伝っています。まだ送っていません。"


def _drawn_art(conn: sqlite3.Connection, letter_id: int) -> sqlite3.Row | None:
    """ツインが描いた、いちばん新しい挿絵。"""
    return conn.execute(
        "SELECT * FROM self_letter_arts WHERE letter_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (letter_id,)
    ).fetchone()


def _illustration(conn: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    """紙に出す挿絵。ツインが描いた絵があればそれ、なければ前もって作った水彩の絵（挿絵なしの手紙にしない）。"""
    drawn = _drawn_art(conn, row["id"])
    if drawn is not None:
        return {"src": svg_art.data_url(drawn["svg"]), "title": drawn["title"], "drawn": True}
    view = advocacy_letter.art_view(row["illustration"] or advocacy_letter.fallback_art(advocacy_letter.art_list()))
    return {**view, "drawn": False} if view else None


def letter_view(conn: sqlite3.Connection, row: sqlite3.Row, viewer: User) -> dict:
    """手紙を画面に返す形。書いた本人には版の一覧とあらすじも返す。"""
    versions = _versions(conn, row["id"])
    sent = _sent(conn, row["id"])
    shown = next((item for item in versions if sent and item["version"] == sent["version"]), versions[-1])
    author = db.get_user(conn, row["author_user_id"])
    recipient = db.get_user(conn, row["recipient_user_id"])
    view = {
        "id": row["id"],
        "author": author.display_name if author else "",
        "recipient": {
            "id": recipient.id,
            "name": recipient.display_name,
            "relation": relation_of(recipient),
            "is_deceased": recipient.is_deceased,
        }
        if recipient
        else None,
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "created_at": row["created_at"],
        "status": "送った" if sent else "下書き",
        "sent_at": sent["sent_at"] if sent else None,
        "illustration": _illustration(conn, row),
        "content": json.loads(shown["content"]),
        "version": shown["version"],
        "kind": shown["kind"],
        "notes": json.loads(shown["notes"]),
        "label": label_for(author.display_name if author else "本人", bool(sent)),
    }
    if author and viewer.id == author.id:
        view["outline"] = row["outline"]
        view["versions"] = [{"version": item["version"], "kind": item["kind"], "created_at": item["created_at"]} for item in versions]
    return view


def _summary(conn: sqlite3.Connection, row: sqlite3.Row, viewer: User) -> dict:
    """一覧の1行。本文の書き出しも付ける。"""
    view = letter_view(conn, row, viewer)
    first = next((line.strip() for line in view["content"]["body"].splitlines() if line.strip()), "")
    return {
        "id": view["id"],
        "author": view["author"],
        "recipient": view["recipient"],
        "status": view["status"],
        "created_at": view["created_at"],
        "sent_at": view["sent_at"],
        "period_start": view["period_start"],
        "period_end": view["period_end"],
        "first_line": first[:60],
        # 受け取った人のホームの知らせで、行の頭に書いた人の顔を出すため。画面は番号で人を探す（static/letter_notice.js）
        "author_id": row["author_user_id"],
    }


def _own_draft(conn: sqlite3.Connection, viewer: User, letter_id: int) -> sqlite3.Row:
    """本人が書いた、まだ送っていない手紙。ほかの人の手紙は、ないものとして答える。"""
    row = _letter_row(conn, letter_id)
    if row["author_user_id"] != viewer.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if _sent(conn, letter_id) is not None:
        raise HTTPException(status_code=409, detail=SENT_MESSAGE)
    return row


def _add_version(conn: sqlite3.Connection, request: Request, letter_id: int, kind: str, parts: dict, notes: list[str] | None = None, log_id: int | None = None) -> None:
    latest = _versions(conn, letter_id)[-1]
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "self_letter_versions",
            {
                "letter_id": letter_id,
                "version": latest["version"] + 1,
                "kind": kind,
                "content": json.dumps(parts, ensure_ascii=False),
                "notes": json.dumps(notes or [], ensure_ascii=False),
                "log_id": log_id,
                "created_at": clock.now(request.app).isoformat(),
            },
        )


def _parts(value) -> dict:
    return {"salutation": value.salutation.strip(), "body": value.body.strip(), "closing": value.closing.strip(), "signature": value.signature.strip()}


def _paragraphs(body: str) -> list[str]:
    return [part for part in (piece.strip() for piece in body.replace("\r\n", "\n").split("\n\n")) if part]


def _kept_shape(before: dict, after: dict) -> bool:
    """校正が文を整えただけかを見る。段落の数が同じで、本文が大きく縮んでいなければ整えただけとする。

    校正は作り直しではない。本人が足した文が消えた校正は使わない。
    関連するテスト：tests/test_self_letters.py の test_校正で段落が減ったら使わず_文面はそのまま
    """
    old, new = _paragraphs(before["body"]), _paragraphs(after["body"])
    return len(new) == len(old) and len(after["body"]) >= len(before["body"]) * KEPT_BODY_RATIO


# ---- 道 ----


@router.get("/recipients")
def recipients(viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """送る相手の候補。本人のほかの、はずしていない人。亡くなった方も入る。"""
    return {
        "items": [
            {"id": user.id, "name": user.display_name, "relation": relation_of(user), "role": user.role.value, "is_deceased": user.is_deceased}
            for user in db.list_users(conn)
            if user.id != viewer.id
        ]
    }


@router.get("")
def list_letters(viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """本人が書いた手紙を、新しい順に。下書きと送ったものの両方。"""
    rows = conn.execute(
        f"SELECT * FROM self_letters l WHERE l.author_user_id = ? AND {_NOT_DISCARDED} ORDER BY l.created_at DESC, l.rowid DESC", (viewer.id,)
    ).fetchall()
    return {"items": [_summary(conn, row, viewer) for row in rows]}


@router.get("/inbox")
def inbox(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人に届いた手紙（送ったものだけ）を、新しい順に。"""
    rows = conn.execute(
        """
        SELECT l.* FROM self_letters l JOIN self_letter_sends s ON s.letter_id = l.id
        WHERE l.recipient_user_id = ? ORDER BY s.sent_at DESC, l.rowid DESC
        """,
        (viewer.id,),
    ).fetchall()
    return {"items": [_summary(conn, row, viewer) for row in rows]}


@router.get("/{letter_id}")
def read_letter(
    letter_id: int = Path(ge=1, le=db.MAX_ID), viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """手紙の中身。書いた本人か、送ったあとの相手だけが読める。"""
    row = _letter_row(conn, letter_id)
    is_author = row["author_user_id"] == viewer.id
    is_recipient = row["recipient_user_id"] == viewer.id and _sent(conn, letter_id) is not None
    if not (is_author or is_recipient):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"letter": letter_view(conn, row, viewer)}


@router.post("", status_code=201)
def make_draft(
    body: DraftInput, request: Request, viewer: User = Depends(require_self), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """下書きを作る。同じ client_key なら AI を呼ばずに返す。"""

    def existing() -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM self_letters WHERE author_user_id = ? AND client_key = ?", (viewer.id, body.client_key)).fetchone()

    row = existing()
    if row is not None:
        return {"letter": letter_view(conn, row, viewer)}
    recipient = _recipient(conn, viewer, body.recipient_id)
    now = clock.now(request.app)
    start, end = period_of(body, now.date())
    talk = talk_lines(conn, viewer, start, end)
    if not talk:
        raise HTTPException(status_code=400, detail=NO_TALK_MESSAGE)
    pair = pair_lines(conn, viewer, recipient, start, end)
    self_name = called(viewer.display_name)
    to_name = called(recipient.display_name)
    relation = relation_of(recipient)
    arts = advocacy_letter.art_list()
    task = f"{self_name}が{to_name}（{relation}）へ送る手紙。{start.isoformat()}〜{end.isoformat()}にツインと話したことをもとに書く。"
    with request.app.state.voice_lock:
        row = existing()
        if row is not None:
            return {"letter": letter_view(conn, row, viewer)}
        try:
            material = advocacy_voice.gather(conn, viewer, task)
            if material.selfhood_body:
                model_text = selfhood_prompts.answer_model_text(material.selfhood_body, material.element_lines[1:], material.heart_lines)
            else:
                model_text = advocacy_prompts.voice_model_text(material.element_lines)
            system = self_letter_prompts.draft_system(self_name, to_name, relation, recipient.is_deceased)
            text = self_letter_prompts.draft_user_text(
                today=now.date(),
                start=start,
                end=end,
                recipient=to_name,
                relation=relation,
                deceased=recipient.is_deceased,
                outline=body.outline,
                talk_lines=talk,
                pair_lines=pair,
                art_lines=advocacy_letter.art_lines(arts),
            )
            result = advocacy_voice.ask_without_medical_orders(
                conn,
                lambda extra: ai.write_self_letter(system=system, model_text=model_text, text=text + extra),
                "本人の手紙",
                "本人の手紙",
            )
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
        draft = result.value
        parts = _parts(draft)
        log_id = advocacy._log_inference(conn, InferenceKind.SELF_LETTER, task, parts, material.elements, [], result)
        with ledger.atomic(conn):
            letter_id = db.insert_row(
                conn,
                "self_letters",
                {
                    "author_user_id": viewer.id,
                    "recipient_user_id": recipient.id,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "outline": body.outline.strip(),
                    "illustration": advocacy_letter._picked_art(arts, draft.illustration_number),
                    "draft_log_id": log_id,
                    "client_key": body.client_key,
                    "created_at": now.isoformat(),
                },
            )
            db.insert_row(
                conn,
                "self_letter_versions",
                {
                    "letter_id": letter_id,
                    "version": 1,
                    "kind": "下書き",
                    "content": json.dumps(parts, ensure_ascii=False),
                    "notes": "[]",
                    "log_id": log_id,
                    "created_at": now.isoformat(),
                },
            )
        row = existing()
    return {"letter": letter_view(conn, row, viewer)}


@router.post("/{letter_id}/edit")
def edit_letter(
    body: PartsInput,
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """本人が直した文面を、新しい版として足す。前と同じなら足さない。"""
    row = _own_draft(conn, viewer, letter_id)
    parts = _parts(body)
    if json.loads(_versions(conn, letter_id)[-1]["content"]) != parts:
        _add_version(conn, request, letter_id, "直し", parts)
    return {"letter": letter_view(conn, row, viewer)}


@router.post("/{letter_id}/proofread")
def proofread(
    request: Request,
    body: PartsInput | None = None,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """ツインが今の文面を校正し、新しい版として足す。直す所がなければ版を足さない。

    直す欄の文（body）が来たら、先に「直し」の版として残してから校正する。本人が足した文を失わないため。
    校正で段落が減ったり本文が大きく縮んだりしたら、その校正は使わず、文面はそのままにする（proofread_dropped）。
    """
    row = _own_draft(conn, viewer, letter_id)
    if body is not None:
        typed = _parts(body)
        if json.loads(_versions(conn, letter_id)[-1]["content"]) != typed:
            _add_version(conn, request, letter_id, "直し", typed)
    current = json.loads(_versions(conn, letter_id)[-1]["content"])
    recipient = db.get_user(conn, row["recipient_user_id"])
    to_name = called(recipient.display_name) if recipient else "相手"
    with request.app.state.voice_lock:
        try:
            result = ai.proofread_letter(
                system=self_letter_prompts.proofread_system(called(viewer.display_name)),
                text=self_letter_prompts.proofread_user_text(to_name, relation_of(recipient) if recipient else "", current),
            )
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=PROOF_FAILED_MESSAGE) from error
        advocacy._log_call(conn, "手紙の校正", result, "手紙の校正")
        fixed = _parts(result.value)
        notes = [note.strip() for note in result.value.notes if note.strip()]
        dropped = fixed != current and not _kept_shape(current, fixed)
        changed = fixed != current and not dropped
        if changed:
            log_id = advocacy._log_inference(conn, InferenceKind.LETTER_PROOFREAD, "手紙の校正", {**fixed, "notes": notes}, [], [], result)
            _add_version(conn, request, letter_id, "校正", fixed, notes, log_id)
    view = letter_view(conn, row, viewer)
    view["proofread_changed"] = changed
    view["proofread_dropped"] = dropped
    return {"letter": view}


@router.post("/{letter_id}/revert")
def revert(
    body: RevertInput,
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """前の版の文面に戻す。戻したことも新しい版として足すので、戻す前の版にもまた戻れる。"""
    row = _own_draft(conn, viewer, letter_id)
    target = next((item for item in _versions(conn, letter_id) if item["version"] == body.to_version), None)
    if target is None:
        raise HTTPException(status_code=400, detail="その版が見つかりません。")
    _add_version(conn, request, letter_id, "戻す", json.loads(target["content"]))
    return {"letter": letter_view(conn, row, viewer)}


@router.post("/{letter_id}/send")
def send(
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """送る。いちばん新しい版を送った版にする。送ったあとは直せない。"""
    row = _own_draft(conn, viewer, letter_id)
    latest = _versions(conn, letter_id)[-1]
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "self_letter_sends",
            {"letter_id": letter_id, "version": latest["version"], "sent_at": clock.now(request.app).isoformat()},
        )
    return {"letter": letter_view(conn, row, viewer)}


def _art_sample() -> str | None:
    """見本の絵を base64 にする。ファイルがなければ空。"""
    path = advocacy_letter.ART_DIR / ART_SAMPLE_FILE
    return base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None


@router.post("/{letter_id}/art")
def draw_art(
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """ツインが、今の文面に合う挿絵を描く。描き直すたびに新しい絵を足す。

    描いた SVG は使ってよい要素だけに組み直す。だめなものが入っていたら、その絵は使わず 503 にする（前の絵のまま）。
    """
    row = _own_draft(conn, viewer, letter_id)
    current = json.loads(_versions(conn, letter_id)[-1]["content"])
    try:
        result = ai.draw_letter_art(
            system=self_letter_prompts.art_system(), text=self_letter_prompts.art_user_text(current), sample_webp_base64=_art_sample()
        )
    except advocacy._AI_ERRORS as error:
        raise HTTPException(status_code=503, detail=ART_FAILED_MESSAGE) from error
    advocacy._log_call(conn, "手紙の挿絵", result, "手紙の挿絵")
    try:
        svg = svg_art.clean_svg(result.value.svg)
    except svg_art.UnsafeSvg as error:
        raise HTTPException(status_code=503, detail=ART_FAILED_MESSAGE) from error
    with ledger.atomic(conn):
        db.insert_row(
            conn,
            "self_letter_arts",
            {"letter_id": letter_id, "title": result.value.title.strip()[:40], "svg": svg, "created_at": clock.now(request.app).isoformat()},
        )
    return {"letter": letter_view(conn, row, viewer)}


@router.delete("/{letter_id}")
def discard(
    request: Request,
    letter_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(require_self),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """送っていない下書きを捨てる。行も版も消さず、印を足して一覧と画面から外す。送った手紙は捨てられない。"""
    _own_draft(conn, viewer, letter_id)
    with ledger.atomic(conn):
        db.insert_row(conn, "self_letter_discards", {"letter_id": letter_id, "discarded_at": clock.now(request.app).isoformat()})
    return {"discarded": letter_id}
