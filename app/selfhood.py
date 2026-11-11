"""セルフの盤面。決まった手順の層で、AI を呼ばない。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：パズルの盤面（ピースの一覧）、根拠の追加、確からしさと状態の計算、読んでよい材料の絞り込み。
4つの極：3. 読み解き、積み上げる。4. 10年後に代弁する（土台）。
守る原則：2（今の本人を優先。古い根拠の重みを下げる）、3（出どころを分ける）、5（追記だけ）、
          9（人間性に近づける。本人の言葉は原文のまま）、10（本人だけの記録を読まない）、15（本人のいない部屋を読まない）。

セルフ生成エンジン：記録を読み、本人の人物像を盤面として組み上げる。AI の読みはすべて仮説として、本人の言葉とは分けて持つ。

1つの発言では本人は分からない。発言のたびに仮説を立て、次の発言で確かめ、外れたら直す。そのために、問いが来る前に
本人を盤面として組み上げておく。問いが来てから探す RAG との違いはここにある。

確からしさは AI に決めさせず、根拠の重みからプログラムが計算する。AI の自信を確からしさにすると、確かめようがないため。
性格の型や点数は作らない。ピースはその人の物語を文で書く。

人だけの部屋を読むと相手の言葉も API に送られ、取り消せない。実際に使うには相手の同意が要るので、このエンジンは
架空データでだけ動かす（refuse_real_records）。

関連するテスト：tests/test_selfhood.py
全体の図：docs/architecture.md の「セルフ生成エンジン」
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app import db
from app.schemas import (
    ConfirmStatus,
    EvidenceStance,
    EvidenceWeightKind,
    Petal,
    PieceStatus,
    ProfessionalItemKind,
    RoomKind,
    Role,
    ShareScope,
    Source,
)

OWN_RECORDS_FOLDER = "mine"
_THREE_FACES = ("何で満たされるか", "何で損なわれるか", "今どうか")
# 花びらの読みの画面（app/petals.py）も、同じ3つの面で並べる
THREE_FACES = _THREE_FACES
_CORE = (
    "譲れないこと",
    "条件つきで譲れること",
    "決めるときの決まり",
    "優先順位",
    "人に任せる条件",
    "恐れていること",
    "願っていること",
    "最初の反応と、あとの変化",
    "好きなこと",
    "嫌いなこと、避けること",
)
_RELATION = ("本人にとって何か", "どう接するか", "言えないこと", "その人のためなら何を曲げるか")
_STORY = ("転機", "悔い", "誇り", "役割の移り変わり", "くり返す型", "経歴の事実", "暮らしの決まり")
_VOICE = ("言い回し", "方言", "言葉の長さ", "冗談", "怒り方", "照れ方", "口にしないこと", "弱みの隠し方")
_TIME = ("変わったこと", "変わらない芯")
_NOW = ("今の様子と10年の本人像のずれ",)
SECTION_PETALS = "A 花びら"
SECTION_CORE = "B 決め方の芯"
SECTION_RELATION = "C 関係"
SECTION_STORY = "D 人生の物語"
SECTION_VOICE = "E 人柄と話し方"
SECTION_TIME = "F 時間"
SECTION_NOW = "G 今"


@dataclass(frozen=True)
class Tuning:
    """重みとしきい値。答え合わせを見て直す。計算し直すだけなので AI の費用はかからない。"""

    weights: dict[str, float] = field(
        default_factory=lambda: {
            EvidenceWeightKind.CONFIRMED.value: 4.0,
            EvidenceWeightKind.SELF_WORDS.value: 2.0,
            EvidenceWeightKind.DIALOGUE.value: 2.0,
            EvidenceWeightKind.OBSERVATION.value: 2.0,
            EvidenceWeightKind.TESTIMONY.value: 1.0,
            EvidenceWeightKind.INFERENCE.value: 0.5,
            # 架空の記録を作るときに AI が足した短い受け答え。本人の発言の表に入っていないので重みを下げる
            EvidenceWeightKind.ADDED_REPLY.value: 0.5,
            EvidenceWeightKind.CORRECTION.value: 4.0,
        }
    )
    half_life_years: float = 3.0
    core_span_years: float = 5.0
    likely_confidence: float = 0.7
    likely_support: float = 3.0
    wavering_low: float = 0.35
    wavering_high: float = 0.65
    recent_against_years: float = 1.0
    # 本人が確かめた根拠がこの数以上で「本人が確かめた」にする。1件では94ピース中49件がこの状態になり、区別にならなかった
    confirmed_min: int = 2


TUNING = Tuning()


@dataclass(frozen=True)
class Weighted:
    """計算に使う根拠の1行。"""

    stance: EvidenceStance
    kind: EvidenceWeightKind
    day: date


@dataclass(frozen=True)
class Standing:
    """ピースの計算の結果。"""

    status: PieceStatus
    confidence: float
    support: float
    against: float


class RealRecordsRefused(RuntimeError):
    """実データでセルフの処理を動かそうとしたとき。"""


# 経過の年数（「五年ほど」「3年前」「十年たつ」）。ピースは書いた年のまま残り、あとの年の人物像にも写されるため、
# 経過の年数は書いた時点でしか正しくない。和子さんが亡くなって「五年」と書いた2029年のピースが、2036年の人物像に
# そのまま写ったことがある。出来事の年（「2024年に」）で書かせ、ここで見つけたら書き直させる。
# 本人の言葉のかぎかっこの中は、本人がその日に言ったとおりなので数えない。関連するテスト：tests/test_selfhood.py
_ELAPSED_YEARS = re.compile(
    r"(?<![0-9０-９])(?:[一二三四五六七八九十数]+|[0-9０-９]{1,2})年(?:ほど|前|近く|あまり|余り|以上|ぶり|が(?:たち|たつ|たっ|経)|たっ|たち|たつ|経|になり|になる|になります)"
)
_QUOTED = re.compile(r"「[^「」]*」")


def elapsed_years(text: str) -> list[str]:
    """かぎかっこの外にある、経過の年数の言い方を返す。"""
    return [match.group(0) for match in _ELAPSED_YEARS.finditer(_QUOTED.sub("", text))]


def refuse_real_records(path: Path | str) -> None:
    """自分用の置き場を断る。人だけの部屋の相手の言葉を、同意なく API に送らないため。

    関連するテスト：tests/test_selfhood.py の test_実データの置き場では動かない
    """
    if OWN_RECORDS_FOLDER in {part.lower() for part in Path(path).resolve().parts}:
        raise RealRecordsRefused("このエンジンは架空データでだけ動きます。")


def _age_weight(kind: EvidenceWeightKind, day: date, as_of: date, keep_old: bool, tuning: Tuning) -> float:
    weight = tuning.weights[kind.value]
    if keep_old:
        return weight
    years = max((as_of - day).days, 0) / 365.25
    return weight * 0.5 ** (years / tuning.half_life_years)


def stand(evidence: list[Weighted], as_of: date, tuning: Tuning = TUNING, *, changed: bool = False) -> Standing:
    """根拠から確からしさと状態を計算する。

    確からしさ = (支え + 1) / (支え + 反証 + 2)
    古い根拠は半減する。長い年月にわたって支えが続くピースは「変わらない芯」として減らさない。
    関連するテスト：tests/test_selfhood.py
    """
    if not evidence:
        return Standing(PieceStatus.EMPTY, 0.5, 0.0, 0.0)
    supports = [item for item in evidence if item.stance is EvidenceStance.SUPPORT]
    span = (max(item.day for item in supports) - min(item.day for item in supports)).days / 365.25 if supports else 0
    keep_old = span >= tuning.core_span_years
    support = sum(_age_weight(item.kind, item.day, as_of, keep_old, tuning) for item in supports)
    against = sum(
        _age_weight(item.kind, item.day, as_of, False, tuning) for item in evidence if item.stance is EvidenceStance.AGAINST
    )
    confidence = (support + 1) / (support + against + 2)
    recent_against = any(
        item.stance is EvidenceStance.AGAINST and (as_of - item.day).days / 365.25 <= tuning.recent_against_years
        for item in evidence
    )
    if changed:
        status = PieceStatus.CHANGED
    elif sum(item.kind is EvidenceWeightKind.CONFIRMED and item.stance is EvidenceStance.SUPPORT for item in evidence) >= tuning.confirmed_min:
        status = PieceStatus.CONFIRMED
    elif all(item.kind is EvidenceWeightKind.INFERENCE for item in evidence):
        status = PieceStatus.INFERRED
    elif recent_against and tuning.wavering_low <= confidence <= tuning.wavering_high:
        status = PieceStatus.WAVERING
    elif confidence >= tuning.likely_confidence and support >= tuning.likely_support:
        status = PieceStatus.LIKELY
    else:
        status = PieceStatus.HYPOTHESIS
    return Standing(status, round(confidence, 3), round(support, 3), round(against, 3))


def starting_pieces(conn: sqlite3.Connection, as_of: date | None = None) -> list[dict]:
    """最初の盤面を返す。関係のピースは人ごとに増える。as_of を渡すと、その日に登録されていない人は入れない判断に使える。"""
    pieces = [
        {"key": f"A/{petal.value}/{face}", "section": SECTION_PETALS, "name": f"{petal.value}：{face}", "person": None}
        for petal in Petal
        for face in _THREE_FACES
    ]
    for section, names in (
        (SECTION_CORE, _CORE),
        (SECTION_STORY, _STORY),
        (SECTION_VOICE, _VOICE),
        (SECTION_TIME, _TIME),
        (SECTION_NOW, _NOW),
    ):
        pieces += [{"key": f"{section[0]}/{name}", "section": section, "name": name, "person": None} for name in names]
    for user in db.list_users(conn):
        if user.role is Role.SELF:
            continue
        pieces += [
            {"key": f"C/{user.id}/{facet}", "section": SECTION_RELATION, "name": f"{user.display_name}：{facet}", "person": user.id}
            for facet in _RELATION
        ]
    return pieces


def _self_id(conn: sqlite3.Connection) -> int | None:
    person = db.self_user(conn)
    return person.id if person else None


def rooms_with_self(conn: sqlite3.Connection) -> list[int]:
    """本人のいる部屋の番号を返す。本人のいない部屋は読まない（原則15）。"""
    me = _self_id(conn)
    return [row[0] for row in conn.execute("SELECT room_id FROM room_members WHERE user_id = ?", (me,))]


def dialogue_rows(conn: sqlite3.Connection, period: str | None = None) -> list[sqlite3.Row]:
    """段1b で読むメッセージを返す。本人のいる部屋の人の発言。お知らせは入れない。

    ツインとの部屋からは、発言の表に入っていない本人の短い受け答えだけを入れる。ほかの本人の言葉は言明として読むため。
    関連するテスト：tests/test_selfhood.py の test_本人のいない部屋は読まない
    """
    me = _self_id(conn)
    return conn.execute(
        """
        SELECT m.id, m.room_id, m.sender_user_id, m.text, m.sent_at, m.utterance_id, r.kind AS room_kind, r.name AS room_name
        FROM room_messages m JOIN rooms r ON r.id = m.room_id
        WHERE m.is_twin = 0 AND m.kind <> 'お知らせ' AND m.text IS NOT NULL AND m.text <> ''
          AND m.room_id IN (SELECT room_id FROM room_members WHERE user_id = ?)
          AND (r.kind <> ? OR (m.sender_user_id = ? AND m.utterance_id IS NULL))
          AND m.sent_at LIKE ?
        ORDER BY m.sent_at, m.rowid
        """,
        (me, RoomKind.TWIN.value, me, f"{period or ''}%"),
    ).fetchall()


def statement_rows(conn: sqlite3.Connection, period: str | None = None) -> list[sqlite3.Row]:
    """段2で読む言明を返す。いま有効な行だけ。「本人だけ」とお休みは読まず、推測もしない。AI の推測は材料にしない。

    「専門職だけ」も読まない。セルフは託された家族への代弁と、家族が確かめる手紙にも使うので、そこから家族に漏れるため。
    段3〜6は、この段の答え（ピース）だけを読むので、ここで外せば後の段にも入らない。
    関連するテスト：tests/test_selfhood.py の test_本人だけとお休みの記録は読まない、test_専門職だけの記録はセルフに読まない
    """
    return conn.execute(
        """
        SELECT s.id, s.body, s.effective_date, s.source, s.speaker_user_id, s.petals, s.replaces_id, s.confirm_status
        FROM statements s
        WHERE s.source IN (?, ?, ?, ?, ?)
          AND s.confirm_status <> ?
          AND s.share_scope NOT IN (?, ?)
          AND s.id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
          AND s.effective_date LIKE ?
        ORDER BY s.effective_date, s.rowid
        """,
        (
            Source.SELF_WORDS.value,
            Source.SELF_CORRECTION.value,
            Source.FAMILY_TESTIMONY.value,
            Source.CONNECTED_TESTIMONY.value,
            Source.OBSERVATION.value,
            ConfirmStatus.RESTING.value,
            ShareScope.SELF_ONLY.value,
            ShareScope.PROFESSIONAL_ONLY.value,
            f"{period or ''}%",
        ),
    ).fetchall()


def reading_of(conn: sqlite3.Connection, statement_id: int) -> str | None:
    """言明につながる AI の読みを返す。読みは訂正の連鎖のいちばん元の行にある。"""
    root = statement_id
    while True:
        row = conn.execute("SELECT replaces_id FROM statements WHERE id = ?", (root,)).fetchone()
        if row is None or row[0] is None:
            break
        root = row[0]
    row = conn.execute("SELECT body FROM interpretations WHERE statement_id = ? LIMIT 1", (root,)).fetchone()
    return row[0] if row else None


# 専門職が伝えた項目のうち、聞き取りの種類。資料の中の「本人：〜」は、専門職が聞いて書いたもので、本人の言葉ではない
_HEARD_KINDS = (ProfessionalItemKind.SELF_HEARD.value, ProfessionalItemKind.FAMILY_HEARD.value)


def heard_statement_ids(conn: sqlite3.Connection) -> set[int]:
    """専門職の聞き取りから台帳に入った言明の番号を返す。表がない古いデータベースでは空。"""
    marks = ", ".join("?" for _ in _HEARD_KINDS)
    try:
        rows = conn.execute(
            f"SELECT statement_id FROM professional_items WHERE kind IN ({marks}) AND statement_id IS NOT NULL", _HEARD_KINDS
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {row[0] for row in rows}


def weight_kind_of(
    source: str, *, confirmed: bool = False, added: bool = False, dialogue: bool = False, heard: bool = False
) -> EvidenceWeightKind:
    """出どころから根拠の種類を決める。

    heard は、専門職の聞き取り（本人や家族の思いを専門職が聞いて書いたもの）から入った言明。台帳では今の様子として入るが、
    目で見た様子ではなく人づての言葉なので、証言と同じ重み1で数える（tyumajinさんの決定）。
    関連するテスト：tests/test_selfhood.py の test_専門職の聞き取りは証言と同じ重みで数える
    """
    if added:
        return EvidenceWeightKind.ADDED_REPLY
    if dialogue:
        return EvidenceWeightKind.DIALOGUE
    if source == Source.SELF_CORRECTION.value:
        return EvidenceWeightKind.CORRECTION
    if heard:
        return EvidenceWeightKind.TESTIMONY
    if source == Source.OBSERVATION.value:
        return EvidenceWeightKind.OBSERVATION
    if source in {Source.FAMILY_TESTIMONY.value, Source.CONNECTED_TESTIMONY.value}:
        return EvidenceWeightKind.TESTIMONY
    return EvidenceWeightKind.CONFIRMED if confirmed else EvidenceWeightKind.SELF_WORDS


def accepts(piece_key: str, kind: EvidenceWeightKind, speaker_is_self: bool) -> bool:
    """この根拠をこのピースに入れてよいかを返す。

    話した人と誰の話かを分ける：美穂さんの発言の気持ちとニーズは美穂さんのもの。本人の気持ちに数えず、関係のピースにだけ入れる。
    関連するテスト：tests/test_selfhood.py の test_家族の発言は本人像のピースに入らない
    """
    if speaker_is_self:
        return True
    if kind is EvidenceWeightKind.OBSERVATION:
        return piece_key.endswith("今どうか") or piece_key.startswith(("C/", "G/"))
    return piece_key.startswith(("C/", "G/"))


def add_piece_version(
    conn: sqlite3.Connection,
    *,
    run: str,
    key: str,
    section: str,
    name: str,
    person: int | None,
    hypotheses: list[str],
    standing: Standing,
    replay_date: str,
    model: str | None = None,
) -> int:
    """ピースの新しい版を足す。前の版は消さない。"""
    version = latest_version(conn, run, key) + 1
    return db.insert_row(
        conn,
        "selfhood_pieces",
        {
            "run": run,
            "piece_key": key,
            "section": section,
            "name": name,
            "about_user_id": person,
            "version": version,
            "hypotheses": json.dumps(hypotheses[:3], ensure_ascii=False),
            "status": standing.status.value,
            "confidence": standing.confidence,
            "support": standing.support,
            "against": standing.against,
            "replay_date": replay_date,
            "model": model,
            "created_at": datetime.now().astimezone().isoformat(),
        },
    )


def latest_version(conn: sqlite3.Connection, run: str, key: str) -> int:
    row = conn.execute("SELECT MAX(version) FROM selfhood_pieces WHERE run = ? AND piece_key = ?", (run, key)).fetchone()
    return row[0] or 0


def add_evidence(
    conn: sqlite3.Connection,
    *,
    run: str,
    key: str,
    stance: EvidenceStance,
    kind: EvidenceWeightKind,
    source_table: str,
    source_id: int | None,
    day: str,
    note: str | None = None,
) -> int:
    """根拠を足す。消さない。"""
    return db.insert_row(
        conn,
        "selfhood_evidence",
        {
            "run": run,
            "piece_key": key,
            "stance": stance.value,
            "kind": kind.value,
            "source_table": source_table,
            "source_id": source_id,
            "effective_date": day,
            "note": note,
            "created_at": datetime.now().astimezone().isoformat(),
        },
    )


def evidence_of(conn: sqlite3.Connection, run: str, key: str, as_of: str | None = None) -> list[Weighted]:
    rows = conn.execute(
        "SELECT stance, kind, effective_date FROM selfhood_evidence WHERE run = ? AND piece_key = ? AND effective_date <= ?",
        (run, key, as_of or "9999-12-31"),
    ).fetchall()
    return [
        Weighted(EvidenceStance(row[0]), EvidenceWeightKind(row[1]), date.fromisoformat(row[2][:10]))
        for row in rows
    ]


def board(conn: sqlite3.Connection, run: str, as_of: str | None = None, tuning: Tuning = TUNING) -> list[dict]:
    """その日付の盤面を返す。切り口ごとに再生し直さず、その日までの版と根拠から引く。確からしさは計算し直す。

    関連するテスト：tests/test_selfhood.py の test_その日までの版と根拠で盤面を引く
    """
    day = as_of or "9999-12-31"
    rows = conn.execute(
        """
        SELECT p.* FROM selfhood_pieces p
        WHERE p.run = ? AND p.replay_date <= ?
          AND p.version = (
              SELECT MAX(x.version) FROM selfhood_pieces x WHERE x.run = p.run AND x.piece_key = p.piece_key AND x.replay_date <= ?
          )
        ORDER BY p.section, p.rowid
        """,
        (run, day, day),
    ).fetchall()
    result = []
    as_of_date = date.fromisoformat(day) if as_of else date.today()
    for row in rows:
        standing = stand(evidence_of(conn, run, row["piece_key"], day), as_of_date, tuning, changed=row["status"] == PieceStatus.CHANGED.value)
        result.append(
            {
                "key": row["piece_key"],
                "section": row["section"],
                "name": row["name"],
                "person": row["about_user_id"],
                "hypotheses": json.loads(row["hypotheses"]),
                "status": standing.status.value,
                "confidence": standing.confidence,
                "support": standing.support,
                "against": standing.against,
                "version": row["version"],
            }
        )
    return result


def active_run(conn: sqlite3.Connection) -> str | None:
    """使う再生の名前を返す。セルフの文書がいちばん新しい再生。読めなければ None。"""
    try:
        row = conn.execute("SELECT run FROM selfhood_documents WHERE kind = 'セルフ' ORDER BY rowid DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def selfhood_text(conn: sqlite3.Connection, run: str, as_of: str | None = None) -> str | None:
    """その日付までで最新のセルフの全文を返す。"""
    row = conn.execute(
        "SELECT body FROM selfhood_documents WHERE run = ? AND kind = 'セルフ' AND as_of <= ? ORDER BY as_of DESC, rowid DESC LIMIT 1",
        (run, as_of or "9999-12-31"),
    ).fetchone()
    return row[0] if row else None


def piece_lines(pieces: list[dict], *, with_hypotheses: bool = True) -> list[str]:
    """盤面を番号つきの行にする。番号は渡した並びの順。"""
    lines = []
    for number, piece in enumerate(pieces, start=1):
        if piece["status"] == PieceStatus.EMPTY.value or not piece["hypotheses"]:
            detail = "（空き）"
        elif with_hypotheses:
            detail = "／".join(piece["hypotheses"])
        else:
            detail = piece["hypotheses"][0][:50]
        lines.append(f"[{number}] {piece['section']}｜{piece['name']}｜{piece['status']} {piece['confidence']}：{detail}")
    return lines


__all__ = [
    "RealRecordsRefused",
    "Standing",
    "TUNING",
    "Tuning",
    "Weighted",
    "accepts",
    "active_run",
    "add_evidence",
    "add_piece_version",
    "board",
    "dialogue_rows",
    "evidence_of",
    "selfhood_text",
    "piece_lines",
    "reading_of",
    "refuse_real_records",
    "rooms_with_self",
    "stand",
    "starting_pieces",
    "statement_rows",
    "weight_kind_of",
]
