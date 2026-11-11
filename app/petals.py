"""花びらの読み（専門職の「記録」の上と、本人のホームの「5つの花びら」）。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：5つの花びら（キットウッドの5つの心の求め）ごとに、本人が何で満たされ、何で損なわれ、今どうかを、
     セルフに保存してあるツイン（AI）の読みから返す。専門職の「記録」の画面で、3Dのツインのまわりのボタンから読む。
     本人も、ホームの「5つの花びら」から同じ読みを見られる（ご本人の依頼、2026-11-07）。家族には返さない。
4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
守る原則：4（読みは推測として出す）、9（本人の言葉は記録の本文のまま、日付と出どころを添える）、
          10（根拠の言葉は見る人に見えるものだけ）、13（花びらの名前と根拠は専門職の画面だけ。数字を出さない）。

AI はここでは呼ばない。読みの文は、セルフを作ったときに AI が書いて保存した仮説（selfhood_pieces）をそのまま返す。
画面に AI の文を書き込まず、セルフを作り直せば読みも新しくなるようにするため。読む量は15か所と根拠の言葉だけで、
記録が増えても変わらない。セルフの仕組みは docs/architecture.md にある。

関連するテスト：tests/test_petals.py
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from app import advocacy, clock, consent, observations, selfhood
from app.auth import current_user, get_conn
from app.schemas import EvidenceWeightKind, Petal, Role, User

router = APIRouter(prefix="/api/twin")

LABEL = "ツイン（AI）の読み（推測を含みます）"
# 1つの面に添える根拠の本人の言葉の数。読む人が一目で追える数にとどめる
_WORDS_PER_FACE = 2
# 本人の言葉として添えてよい根拠の種類。証言や今の様子は本人の言葉ではないので添えない（原則3）
_WORD_KINDS = (EvidenceWeightKind.SELF_WORDS.value, EvidenceWeightKind.CONFIRMED.value)
_SELF_SOURCES = ("本人の言葉", "本人の訂正")
# 文の切れ目とかぎかっこ。かぎかっこの中の句点では切らない（app/heart.py の check_reply と同じ切り方）
_SENTENCE = re.compile(r"(?:「[^」]*」?|[^。！？!?「])+[。！？!?]?")
_QUOTE = re.compile(r"「([^」]*)」")


def _require_reader(conn: sqlite3.Connection, viewer: User) -> None:
    """本人と、託されている専門職だけを通す。家族と、託されていない専門職には返さない。

    本人も、自分のホームの「5つの花びら」から読める（ご本人の依頼、2026-11-07）。根拠の言葉と、読みのかぎかっこの文は、
    見る人に見える記録だけで確かめるので、本人には本人が見られる言葉だけが出る。
    """
    if viewer.role is Role.SELF:
        return
    if viewer.role is not Role.PROFESSIONAL or not observations.can_use_supporter_screen(conn, viewer):
        raise HTTPException(status_code=404, detail=advocacy.NOT_FOUND)


def _checked_readings(readings: list[str], visible_text: str) -> list[str]:
    """読みの文を段落に分け、見る人に見える記録で確かめられないかぎかっこの文を落とす。全部落ちた段落は返さない。

    かぎかっこは本人の言葉に見える。セルフは人だけの部屋（1対1）の発言も読んで作られており、そこの言葉を
    かぎかっこで引いた読みがあった（「まだ開けられん」など、2026-11-06 の確かめ）。人だけの部屋の言葉は引かない（原則15）、
    記録にない言葉を人の言葉として出さない（原則9）ので、見る人の「記録」の一覧にある本文で確かめられる引用だけを通す。
    関連するテスト：tests/test_petals.py の test_見える記録にないかぎかっこの文は読みから落とす
    """
    kept: list[str] = []
    # セルフの読みは、版をまとめるときに段落を「／」でつないでいる。画面では段落に分けて出す
    pieces = [part.strip() for reading in readings for part in reading.split("／")]
    for reading in pieces:
        sentences = [
            sentence
            for sentence in _SENTENCE.findall(reading)
            if all(quote in visible_text for quote in _QUOTE.findall(sentence))
        ]
        text = "".join(sentences).strip()
        if text:
            kept.append(text)
    return kept


def _latest_evidence(conn: sqlite3.Connection, run: str, key: str, as_of: str) -> str | None:
    """その面を支える根拠のうち、いちばん新しい日を返す。「今どうか」が、いつまでの記録からの読みかを示すため。

    札の期間（as_of）は盤面の日付で、根拠がそこまで新しいとは限らない（UIUX の確かめで、今どうかの根拠が数年前だった）。
    """
    row = conn.execute(
        "SELECT MAX(effective_date) FROM selfhood_evidence WHERE run = ? AND piece_key = ? AND stance = '支える' AND effective_date <= ?",
        (run, key, as_of),
    ).fetchone()
    return row[0][:10] if row and row[0] else None


def _words(conn: sqlite3.Connection, run: str, key: str, as_of: str, visible: dict[int, object]) -> list[dict]:
    """その面の根拠になった本人の言葉を、新しい順に返す。見えない言葉と、いま有効でない版は返さない。"""
    marks = ", ".join("?" for _ in _WORD_KINDS)
    rows = conn.execute(
        f"""
        SELECT source_id FROM selfhood_evidence
        WHERE run = ? AND piece_key = ? AND source_table = 'statements' AND stance = '支える'
          AND kind IN ({marks}) AND effective_date <= ?
        ORDER BY effective_date DESC, rowid DESC
        """,
        (run, key, *_WORD_KINDS, as_of),
    ).fetchall()
    words: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        statement = visible.get(row["source_id"])
        if statement is None or statement.source.value not in _SELF_SOURCES or statement.body in seen:
            continue
        seen.add(statement.body)
        words.append(
            {
                "statement_id": statement.id,
                "body": statement.body,
                "effective_date": statement.effective_date.isoformat(),
                "source": statement.source.value,
            }
        )
        if len(words) >= _WORDS_PER_FACE:
            break
    return words


@router.get("/petals")
def read_petals(request: Request, viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """5つの花びらの読みを返す。セルフがまだなければ空の一覧を返す。

    関連するテスト：tests/test_petals.py の test_専門職には5つの花びらを3つの面ずつ返す
    """
    _require_reader(conn, viewer)
    # デモの今日の日付で引く。省くと実際の今日（2026年）を基準に確からしさを計算してしまう
    as_of = clock.now(request.app).date().isoformat()
    run = selfhood.active_run(conn)
    if run is None:
        return {"as_of": as_of, "label": LABEL, "petals": []}
    pieces = {piece["key"]: piece for piece in selfhood.board(conn, run, as_of) if piece["section"] == selfhood.SECTION_PETALS}
    visible = {statement.id: statement for statement in consent.listable_statements(conn, viewer)}
    visible_text = "\n".join(statement.body for statement in visible.values())
    petals = []
    for order, petal in enumerate(Petal):
        faces = []
        for face in selfhood.THREE_FACES:
            key = f"A/{petal.value}/{face}"
            piece = pieces.get(key)
            faces.append(
                {
                    "face": face,
                    "status": piece["status"] if piece else "空き",
                    "readings": _checked_readings(piece["hypotheses"], visible_text) if piece else [],
                    "words": _words(conn, run, key, as_of, visible) if piece else [],
                    "latest_evidence_date": _latest_evidence(conn, run, key, as_of) if piece else None,
                }
            )
        petals.append({"order": order, "name": petal.value, "faces": faces})
    return {"as_of": as_of, "label": LABEL, "petals": petals}
