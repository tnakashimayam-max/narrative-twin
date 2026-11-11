"""ツインのアドボカシー。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：意思モデルと AI の読みをもとに、ツインが自分の言葉で主張する。補強に引く本人の言葉は、プログラムが記録の本文のまま添える。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）、10（材料は見える範囲の関数から取る）、
          12（必ず「ツイン（AI）」と出す）、16（答えは考えで返し、言葉は補強するときだけ引く）。

処理は2段に分ける。
1. 集める（役割：ツインの答え）：年と人生の段階の要素を古い順に読み、材料になる要素の番号を選ぶ。選んだ要素の根拠と、
   届けた言葉を記録として集める。語の一致では探さない。質問のたびに似た文を探す方式にしないため（CLAUDE.md の「方式」）。
2. 主張する（役割：ツインのアドボカシー）：意思モデルの要素、AI の読み（言葉から分かる本人の考えの仮説）、今の様子、
   記録を読み、ツイン自身の文と、もとにした要素の番号、補強に引く記録の番号を返す。
   番号はプログラムが渡したものにあるかを確かめて本文に戻す。AI の文が本人の言葉の本文になる道はない。

推定意思（app/advocacy.py）は、会議の画面と、ツールを使えない人の答えに残している。

使っている Claude API の機能：JSON 出力と Pydantic での検証、モデルの使い分け（app/config.py）、
プロンプトキャッシュ（意思モデルのブロック）、考える深さの指定。

関連するテスト：tests/test_advocacy_voice.py
決めた経緯：docs/decisions.md、API の取り決め：docs/api.md
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from app import advocacy, advocacy_prompts, ai, clock, config, consent, db, heart, ledger, medical, model_store, observations, selfhood, selfhood_prompts, professions
from app.auth import NOT_FOUND, current_user, get_conn
from app.schemas import AdvocacyDraft, AnswerRead, ConfirmStatus, InferenceKind, ModelElement, Place, Role, Source, Statement, User

router = APIRouter(prefix="/api/advocacy")

LIMITS = config.MODEL_LIMITS
_SELF_SOURCES = {Source.SELF_WORDS, Source.SELF_CORRECTION}
_TESTIMONY_SOURCES = {Source.FAMILY_TESTIMONY, Source.CONNECTED_TESTIMONY}
AI_FAILED_MESSAGE = "いま、ツインの考えをお伝えできませんでした。少し時間をおいて、もう一度お試しください。"
# ツインの文と記録を読み分けられるように、決まった1文を添える
LABEL = "ツイン（AI）が本人として代弁しています。推測を含みます。記録された本人の言葉ではありません。"
# 問いに合う本人の言葉が記録にないとき、代弁の前に出す決まった1文（tyumajinさんの決定、2026-11-07、案A）。
# 記録にない問い（「犬や猫を飼いたいと話していましたか」）にも、ツインが本人として言い切っていた。
# 代弁はセルフから答えてよいが、記録の言葉とツインの推測の境目を、聞いた人が見分けられるようにする（原則3、9）。
# 言葉があるかは、同じ問いで本人の言葉を選んだ結果（app/advocacy.py の not_yet）で決める。AI に新しく判断させない
OFF_RECORD = "この話は、記録にはありません。ここからはツインの推測です。"
# 決めるのは人だと示す1文。当たり前のことなので、2026-11-06 から画面と紙には出していない。
# 答えの形（decide）を変えないため、値は残す
DECIDE = "決めるのは、ご本人とご家族と専門職です。"
# 健康や病気に関わる問いに添える決まり。止めずに答え、診断と服薬の指示だけをしない（原則17、app/medical.py）
HEALTH_CAVEAT = medical.CAVEAT
OWN_WORDS_LIMIT = 2
# 託された家族の代弁に、理由ごとにプログラムが足す本人の元の発言の数
OWN_WORDS_PER_REASON = 1
_ONLY_TO_TWIN = "ツインにだけ話した"


class VoiceInput(BaseModel):
    """ツインのアドボカシーの入力。again は、前の主張を残したまま作り直すときに使う。"""

    model_config = ConfigDict(extra="forbid")

    again: bool = False


@dataclass
class Material:
    """主張の材料。番号（1始まり）から元の行を引く。"""

    task: str
    now_lines: list[str]
    elements: list[ModelElement]
    element_lines: list[str]
    statements: list[Statement]
    word_lines: list[str]
    reading_lines: list[str] = field(default_factory=list)
    # セルフで答えるとき。問いが来る前に組み上げた本人を土台にする（app/selfhood.py）
    pieces: list[dict] = field(default_factory=list)
    selfhood_body: str | None = None
    # 心の目次の読みの文（app/heart.py の readings_for_supporters）。本人の言葉の本文は入らない
    heart_lines: list[str] = field(default_factory=list)
    # ピースごとの、引いてよい本人の言葉の番号。託された家族の答えに、理由ごとの元の発言を添えるため
    piece_words: dict[str, list[int]] = field(default_factory=dict)
    # 「問いを読む」段が読んだ、聞いた人のねらい、場面、だれの話か。本人の思いではないので、本人の材料とは分けて渡す
    question_reading: list[str] = field(default_factory=list)


def has_material(conn: sqlite3.Connection, viewer: User) -> bool:
    """この人に見える要素か記録があるかを返す。何もなければ、考えを作らず「まだ聞けていません」と答えるため。"""
    return bool(model_store.visible_elements(conn, viewer)) or bool(consent.visible_statements(conn, viewer))


def medical_orders_in(draft: BaseModel) -> list[str]:
    """ツインの下書きに出てきた、診断や服薬の指示を言い切る形を返す。健康の話題の語では見ない（原則17）。"""
    return medical.orders_in(json.dumps(draft.model_dump(mode="json"), ensure_ascii=False))


def ask_without_medical_orders(
    conn: sqlite3.Connection,
    call: Callable[[str], ai.AIResult],
    role: str,
    target: str,
    check: Callable[[BaseModel], str | None] | None = None,
) -> ai.AIResult:
    """AI を呼び、ツインの文に診断や服薬の指示の言い切りがあれば理由を伝えて1回やり直す。やり直しても残れば出さない。

    call は、本文に足す文を受け取って AI を呼ぶ関数。
    血圧や減塩のような健康の話はしてよいので、話題の語では止めない。見るのは、病気を推す、急ぐかどうかを決める、
    薬の量や飲み続けるかを決める、薬の名前を推して直す、の言い切りの形だけ（app/medical.py）。
    やり直しても残ったときは失敗にする。診断と服薬の指示を人の目に出さないことを、言葉が返らないことより先に守るため。
    言い切りの形だけを見るので、健康の話の答えが止まることはない。
    check は、ほかに直してほしいことがあれば文で返す関数。同じ1回のやり直しで伝える。やり直しても残ったときは、そのまま出す。
    関連するテスト：tests/test_advocacy_voice.py の test_ツインの文に服薬の指示があればやり直す
    """
    result = call("")
    advocacy._log_call(conn, role, result, target)
    found = medical_orders_in(result.value)
    problem = check(result.value) if check else None
    if not found and not problem:
        return result
    fixes = []
    if found:
        words = "」「".join(found)
        fixes.append(f"「{words}」のように、病気の診断や服薬の指示を書かないでください。{medical.RULE}")
    if problem:
        fixes.append(problem)
    result = call("\n\n## 前の文の直すところ\n" + "\n".join(fixes))
    advocacy._log_call(conn, role, result, target)
    if medical_orders_in(result.value):
        raise ai.AIResultError("ツインの文に診断か服薬の指示が残りました", result.model, result.input_tokens, result.output_tokens)
    return result


def reading_note(material: "Material") -> str:
    """読んだ記録の期間と件数を、プログラムが文にする。ツインの文は変えない。

    少ない記録でも同じ強さで言い切るので、どれだけ読んで言っているかを人が見られるようにする。
    関連するテスト：tests/test_advocacy_voice.py の test_ツインの考えに読んだ記録の期間と件数を添える
    """
    parts = []
    if material.selfhood_body:
        parts.append(f"セルフと、関わるピース{len(material.pieces)}件")
    if material.heart_lines:
        parts.append(f"心の目次の読み{sum(line.startswith('### ') for line in material.heart_lines)}件")
    years = sorted(element.valid_from.year for element in material.elements)
    if years:
        parts.append(f"意思モデルの要素{len(years)}件（{years[0]}年〜{years[-1]}年）")
    if material.reading_lines:
        parts.append(f"AI の読み{len(material.reading_lines)}件")
    days = sorted(item.effective_date for item in material.statements)
    if days:
        parts.append(f"言葉{len(days)}件（{_date_words(days[0])}〜{_date_words(days[-1])}）")
    return f"読んだ記録：{'、'.join(parts)}" if parts else "読んだ記録：ありません"


def _date_words(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def own_words(quotes: list[dict]) -> list[dict]:
    """根拠の言葉から、本人の言葉だけを選ぶ。本人の意向の欄の上に原文で載せるため。"""
    seen: dict[int, dict] = {}
    for quote in quotes:
        if quote["source"] in {source.value for source in _SELF_SOURCES}:
            seen.setdefault(quote["statement_id"], quote)
    return list(seen.values())[:OWN_WORDS_LIMIT]


def _self_name(conn: sqlite3.Connection) -> str:
    self_user = db.self_user(conn)
    return self_user.display_name if self_user else "本人"


def _now_lines(conn: sqlite3.Connection, viewer: User) -> list[str]:
    return [
        f"{item['date']}／{item['speaker_label'] or item['speaker']}：{item['body']}"
        for item in observations.now_view(conn, viewer, LIMITS["now"])
    ]


def _only_to_twin(conn: sqlite3.Connection, statement: Statement) -> bool:
    """ツインとの日記と会話で話した本人の言葉かを返す。他人に聞かせるつもりがなかったかもしれない言葉を、AI が見分けるため。"""
    if statement.source not in _SELF_SOURCES or not statement.source_utterance_ids:
        return False
    utterance = ledger.get_utterance(conn, statement.source_utterance_ids[0])
    return utterance is not None and utterance.place is Place.DIARY_AND_TALK


def _word_lines(conn: sqlite3.Connection, statements: list[Statement]) -> list[str]:
    """記録の行を作る。確かめた言葉かどうかと、ツインにだけ話した言葉かどうかも読めるようにする。"""
    lines = []
    for number, item in enumerate(statements, start=1):
        place = f"／{_ONLY_TO_TWIN}" if _only_to_twin(conn, item) else ""
        lines.append(
            f"[{number}] {item.effective_date.isoformat()}／{item.source.value}／{advocacy._speaker_name(conn, item)}"
            f"／{item.confirm_status.value}{place}：{item.body}"
        )
    return lines


def _reading_lines(conn: sqlite3.Connection, statements: list[Statement]) -> list[str]:
    """記録の行につながる AI の読みを作る。番号は記録の行と同じ。

    答えの土台を、生の言葉ではなく読みにするため。渡すのは見える記録の分だけで、本人の言葉に限る。
    上限を超えるときは、本人が確かめた言葉、新しい言葉の順に残す。
    関連するテスト：tests/test_advocacy_voice.py
    """
    numbered = [(number, item) for number, item in enumerate(statements, start=1) if item.source in _SELF_SOURCES]
    numbered.sort(key=lambda pair: (pair[1].confirm_status is not ConfirmStatus.CONFIRMED, -pair[1].effective_date.toordinal()))
    lines: dict[int, str] = {}
    for number, item in numbered[: LIMITS["advocacy_readings"]]:
        row = conn.execute(
            "SELECT body FROM interpretations WHERE statement_id = ? LIMIT 1",
            (ledger.root_of(conn, item.id),),
        ).fetchone()
        if row is not None:
            lines[number] = f"[{number}] {row['body']}"
    return [lines[number] for number in sorted(lines)]


def _others(conn: sqlite3.Connection, viewer: User, seen: set[int], room: int) -> list[Statement]:
    """要素の根拠にない証言を、話した人ごとに新しい順で足す。よく書く人の言葉だけで埋まらないようにするため。"""
    visible = [
        item
        for item in consent.visible_statements(conn, viewer)
        if item.source in _TESTIMONY_SOURCES and item.confirm_status is not ConfirmStatus.RESTING and item.id not in seen
    ]
    newest = sorted(visible, key=lambda item: item.effective_date, reverse=True)
    return advocacy._by_speaker(newest)[: max(room, 0)]


def _reading(conn: sqlite3.Connection, viewer: User) -> tuple[dict[int, Statement], list[ModelElement], list[str]]:
    """読む要素と、AI に渡す行を返す。見える要素だけを読む。"""
    heads = ledger.head_map(conn)
    reading = model_store.for_advocacy(model_store.visible_elements(conn, viewer), heads)
    return heads, reading, [model_store.describe(element, number, heads) for number, element in enumerate(reading, start=1)]


def _evidence_for(
    conn: sqlite3.Connection,
    task: str,
    reading: list[ModelElement],
    element_lines: list[str],
    heads: dict[int, Statement],
    scopes,
    per_element: int,
    client=None,
) -> list[Statement]:
    """問いや話題に関わる要素を選ばせ、その根拠を要素ごとに順に取り出す。"""
    result = ai.pick_elements(
        system=advocacy_prompts.gather_system(_self_name(conn)),
        model_text=advocacy_prompts.voice_model_text(element_lines),
        question=task,
        client=client,
    )
    advocacy._log_call(conn, "ツインの答え", result, "ツインのアドボカシーの材料")
    picked: dict[int, ModelElement] = {}
    for number in [*result.value.direct_numbers, *result.value.related_numbers]:
        if 1 <= number <= len(reading):
            picked.setdefault(reading[number - 1].id, reading[number - 1])
    chosen = list(picked.values())[: LIMITS["advocacy_elements"]]
    return advocacy._take_evenly(advocacy._evidence_groups(chosen, heads, scopes, task, per_element))


def _merge(
    conn: sqlite3.Connection,
    viewer: User,
    required: list[Statement],
    taken: list[Statement],
    self_limit: int,
    others_limit: int,
) -> list[Statement]:
    """必ず入れる記録に、上限まで足す。古い順に読ませる。10年の変わり方を順に追えるようにするため。"""
    kept: dict[int, Statement] = {item.id: item for item in required}
    selfs = [item for item in taken if item.source in _SELF_SOURCES and item.id not in kept]
    others = [item for item in taken if item.source in _TESTIMONY_SOURCES and item.id not in kept]
    for item in [*selfs[:self_limit], *others[:others_limit]]:
        kept[item.id] = item
    room = others_limit - sum(item.source in _TESTIMONY_SOURCES for item in kept.values())
    for item in _others(conn, viewer, set(kept), room):
        kept[item.id] = item
    return sorted(kept.values(), key=lambda item: item.effective_date)


def _quotable(conn, run: str, chosen: list[dict], viewer: User, as_of: str | None) -> tuple[list[Statement], dict[str, list[int]]]:
    """選んだピースを支える記録から、引いてよい言葉と、ピースごとの言葉の番号を返す。やり取りの読みは引かない（原則15）。

    見える範囲の関数を通す。本人だけとお休みはここで落ちる。ピースごとに順に取り、1つのピースで上限が埋まらないようにする。
    関連するテスト：tests/test_selfhood_voice.py
    """
    scopes = consent.visible_scopes(conn, viewer)
    heads = ledger.head_map(conn)
    groups = []
    for piece in chosen:
        rows = conn.execute(
            """
            SELECT source_id FROM selfhood_evidence
            WHERE run = ? AND piece_key = ? AND source_table = 'statements' AND stance = '支える' AND effective_date <= ?
            ORDER BY effective_date DESC, rowid DESC LIMIT ?
            """,
            (run, piece["key"], as_of or "9999-12-31", LIMITS["selfhood_words_per_piece"]),
        ).fetchall()
        group = []
        for (statement_id,) in rows:
            head = heads.get(statement_id)
            if head is not None and head.share_scope in scopes and head.confirm_status is not ConfirmStatus.RESTING:
                group.append(head)
        groups.append(group)
    kept: dict[int, Statement] = {}
    for item in advocacy._take_evenly(groups):
        kept.setdefault(item.id, item)
    quotable = list(kept.values())[: LIMITS["selfhood_words"]]
    ids = {item.id for item in quotable}
    by_key = {piece["key"]: [item.id for item in group if item.id in ids] for piece, group in zip(chosen, groups)}
    return quotable, by_key


def _visible_to(conn: sqlite3.Connection, viewer: User) -> Callable[[int], bool]:
    """言葉の番号が、見る人に見える先頭の行につながるかを返す関数。心の目次の行を絞るために使う。"""
    scopes = consent.visible_scopes(conn, viewer)
    heads = ledger.head_map(conn)

    def visible(statement_id: int) -> bool:
        head = heads.get(statement_id)
        return head is not None and head.share_scope in scopes and head.confirm_status is not ConfirmStatus.RESTING

    return visible


def gather_from_selfhood(
    conn: sqlite3.Connection,
    viewer: User,
    task: str,
    run: str,
    required: list[Statement] | None = None,
    client=None,
    as_of: str | None = None,
    health: bool = False,
) -> Material:
    """セルフを土台に材料を組み立てる。読む段が盤面の見出しをたどり、関わるピースを選ぶ。

    as_of は答え合わせで使う。その日までの盤面と言葉だけで答えるため。
    health は、答えの段が健康や病気に関わる問いと読んだか。読む段が見落としても、診断と服薬の指示をしない決まりを添える。
    読む量は上限で切る。記録が増えても読む量を増やさない。
    関連するテスト：tests/test_selfhood_voice.py
    """
    name = _self_name(conn)
    pieces = [piece for piece in selfhood.board(conn, run, as_of) if piece["hypotheses"]]
    # 聞いた人を添えて読ませる。ねらいは聞いた人に当てて読み、本人の思いと分けるため
    asker = f"{viewer.name}（{viewer.role.value}）"
    read = ai.call_structured(
        "問いを読む",
        system=selfhood_prompts.answer_read_system(name),
        cached_system="\n".join(selfhood.piece_lines(pieces, with_hypotheses=False)),
        content=f"聞いた人：{asker}\n問い：{task}",
        schema=AnswerRead,
        client=client,
    )
    advocacy._log_call(conn, "問いを読む", read, "セルフの見出しをたどる")
    chosen: dict[str, dict] = {}
    for number in read.value.piece_numbers[: LIMITS["selfhood_pieces"]]:
        if 1 <= number <= len(pieces):
            chosen.setdefault(pieces[number - 1]["key"], pieces[number - 1])
    # 話し方は問いに関わらず要る。AI が選び忘れても、本人の言い方で答えられるようにする
    for piece in [item for item in pieces if item["key"].startswith("E/")]:
        chosen.setdefault(piece["key"], piece)
    picked = list(chosen.values())
    statements = {item.id: item for item in required or []}
    quotable, piece_words = _quotable(conn, run, picked, viewer, as_of)
    for item in quotable:
        statements.setdefault(item.id, item)
    ordered = sorted(statements.values(), key=lambda item: item.effective_date)
    if read.value.health or health:
        # 健康や病気に関わる問い。止めずに答え、診断と服薬の指示をしない決まりだけを添える（原則17）
        task = f"{task}\n{HEALTH_CAVEAT}"
    body = selfhood.selfhood_text(conn, run, as_of) or ""
    return Material(
        task,
        [] if as_of else _now_lines(conn, viewer),
        [],
        [f"本人の人物像：{body}", *selfhood.piece_lines(picked)],
        ordered,
        _word_lines(conn, ordered),
        [],
        picked,
        body,
        heart.readings_for_supporters(conn, _visible_to(conn, viewer), as_of),
        piece_words,
        question_reading(asker, read.value),
    )


def question_reading(asker: str, read: AnswerRead) -> list[str]:
    """「問いを読む」段の読みを、本人として書く段に渡す行にする。何も読めなかったときは渡さない。

    問いの奥のねらいを聞いた人に当てて読む。話した人を分けるため、行の頭に聞いた人を出し、本人の思いとしては渡さない。
    関連するテスト：tests/test_selfhood_voice.py の test_問いの読みは聞いた人のねらいとして書く段に渡る
    """
    parts = [("聞いた人のねらい", read.asker_need), ("決める場面", read.scene), ("だれの話か", read.about)]
    lines = [f"{label}：{value.strip()}" for label, value in parts if value.strip()]
    return [f"聞いた人：{asker}", *lines] if lines else []


def gather(
    conn: sqlite3.Connection,
    viewer: User,
    task: str,
    required: list[Statement] | None = None,
    client=None,
    health: bool = False,
) -> Material:
    """主張の材料を集める。定数で切るので、記録が増えても読む量は増えない。required は必ず入れる記録。

    health のときは、診断と服薬の指示をしない決まりを問いに添える（原則17）。止めはしない。
    """
    run = selfhood.active_run(conn)
    if run:
        return gather_from_selfhood(conn, viewer, task, run, required, client, health=health)
    if health:
        task = f"{task}\n{HEALTH_CAVEAT}"
    heads, reading, element_lines = _reading(conn, viewer)
    scopes = consent.visible_scopes(conn, viewer)
    taken = _evidence_for(conn, task, reading, element_lines, heads, scopes, LIMITS["advocacy_words_per_element"], client)
    statements = _merge(conn, viewer, required or [], taken, LIMITS["advocacy_words_self"], LIMITS["advocacy_words_others"])
    return Material(
        task, _now_lines(conn, viewer), reading, element_lines, statements, _word_lines(conn, statements), _reading_lines(conn, statements)
    )


def gather_topics(conn: sqlite3.Connection, viewer: User, task: str, topics: tuple[str, ...], client=None) -> Material:
    """話題ごとに材料を集めて合わせる。1回の問いで集めると、問いに近い話題の言葉だけで上限が埋まるため。"""
    run = selfhood.active_run(conn)
    if run:
        return gather_from_selfhood(conn, viewer, f"{task}\n話題：{'、'.join(topics)}", run, None, client)
    heads, reading, element_lines = _reading(conn, viewer)
    scopes = consent.visible_scopes(conn, viewer)
    per_topic = [
        _evidence_for(conn, topic, reading, element_lines, heads, scopes, LIMITS["sheet_words_per_element"], client)
        for topic in topics
    ]
    statements = _merge(conn, viewer, [], advocacy._take_evenly(per_topic), LIMITS["sheet_words_self"], LIMITS["sheet_words_others"])
    return Material(
        task, _now_lines(conn, viewer), reading, element_lines, statements, _word_lines(conn, statements), _reading_lines(conn, statements)
    )


def pick_quotes(numbers: list[int], statements: list[Statement]) -> list[Statement]:
    """番号を記録の行に直す。渡していない番号は捨てる。"""
    kept: dict[int, Statement] = {}
    for number in numbers:
        if 1 <= number <= len(statements):
            kept.setdefault(statements[number - 1].id, statements[number - 1])
    return list(kept.values())


_QUOTED = re.compile(r"「[^」]*」")


def piece_text_for_people(text: str) -> str:
    """画面に出すピースの文から、かぎかっこの言葉を外す。

    ピースの仮説は、本人のいる部屋の会話からも読んでいて、相手の言葉が入ることがある。人だけの部屋で読んだ言葉は
    引かない決まり（原則15）なので、画面では見立ての文だけを見せる。AI に渡す材料はそのまま。
    関連するテスト：tests/test_selfhood_voice.py の test_画面に出すピースの文にかぎかっこの言葉を出さない
    """
    return _QUOTED.sub("（言葉は省きます）", text)


def _piece_views(numbers: list[int], pieces: list[dict]) -> list[dict]:
    """番号をピースに直す。どの見立てから考えたかを人が見られるようにする。渡していない番号は捨てる。"""
    views = []
    for number in numbers:
        if 1 <= number <= len(pieces) and len(views) < LIMITS["advocacy_elements_per_reason"]:
            piece = pieces[number - 1]
            views.append(
                {
                    "element_id": None,
                    "kind": piece["section"],
                    "body": piece_text_for_people(piece["hypotheses"][0]) if piece["hypotheses"] else "",
                    "confirmed": piece["status"] == "本人が確かめた",
                }
            )
    return views


def _element_views(conn: sqlite3.Connection, numbers: list[int], elements: list[ModelElement]) -> list[dict]:
    """番号を意思モデルの要素に直す。渡していない番号は捨てる。どの読みから考えたかを人が見られるようにするため。"""
    heads = ledger.head_map(conn)
    kept: dict[int, ModelElement] = {}
    for number in numbers:
        if 1 <= number <= len(elements):
            kept.setdefault(elements[number - 1].id, elements[number - 1])
    return [
        {
            "element_id": element.id,
            "kind": element.kind.value,
            "body": element.body,
            "confirmed": model_store.confirmed_by_self(element, heads),
        }
        for element in list(kept.values())[: LIMITS["advocacy_elements_per_reason"]]
    ]


def reasons_without_elements(draft: AdvocacyDraft, element_count: int) -> str | None:
    """意思モデルの要素を指していない理由があれば、直してほしいことを文で返す。答えが意思モデルにもとづいていることを確かめるため。"""
    if not element_count:
        return None
    for reason in draft.reasons:
        if not any(1 <= number <= element_count for number in reason.element_numbers):
            return "reasons の element_numbers に、その理由のもとにした意思モデルの要素の番号を必ず書いてください。"
    return None


def build_view(
    conn: sqlite3.Connection,
    draft: AdvocacyDraft,
    statements: list[Statement],
    elements: list[ModelElement] | None = None,
    pieces: list[dict] | None = None,
    own_words_of: dict[str, list[int]] | None = None,
) -> dict:
    """AI の出力を画面に返す形にする。本文は記録から入れる。読めなかった番号の反対の言葉は、答えごと外す。

    own_words_of を渡すと、理由ごとに、もとにしたピースを支える本人の言葉をプログラムが根拠に足す（託された家族の答え）。
    """
    self_name = _self_name(conn)

    def quotes(numbers: list[int]) -> list[dict]:
        kept = pick_quotes(numbers, statements)[: LIMITS["advocacy_quotes_per_reason"]]
        return [advocacy.statement_view(conn, item, self_name) for item in kept]

    def evidence(reason) -> list[dict]:
        found = quotes(reason.evidence_numbers)
        if own_words_of is None or not pieces:
            return found
        return found + _own_words_for(conn, reason.element_numbers, pieces, own_words_of, statements, found, self_name)

    objections = []
    for objection in draft.objections:
        found = quotes([objection.number])
        if found:
            objections.append({"quote": found[0], "answer": objection.answer.strip()})
    return {
        "label": LABEL,
        "selfhood": draft.selfhood.strip(),
        "feeling": draft.feeling.strip(),
        "claim": draft.claim.strip(),
        "reasons": [
            {
                "text": reason.text.strip(),
                "elements": _piece_views(reason.element_numbers, pieces) if pieces else _element_views(conn, reason.element_numbers, elements or []),
                "evidence": evidence(reason),
            }
            for reason in draft.reasons[: LIMITS["advocacy_reasons"]]
        ],
        "objections": objections[: LIMITS["advocacy_objections"]],
        "requests": [item.strip() for item in draft.requests],
        "ask_back": draft.ask_back,
    }


def _own_words_for(
    conn: sqlite3.Connection,
    numbers: list[int],
    pieces: list[dict],
    own_words_of: dict[str, list[int]],
    statements: list[Statement],
    found: list[dict],
    self_name: str,
) -> list[dict]:
    """理由のもとにしたピースを支える本人の言葉を、1件だけ返す。本文は記録のまま入れる。

    託された家族は、ツインの代弁がどの言葉から来たかを自分で確かめたい（tyumajinさんの決定）。AI に選ばせると引かない回があるので、
    ピースの根拠からプログラムが選ぶ。引ける本人の言葉は、ほとんどがツインにだけ話した言葉なので、それも使う。
    並べるだけにならないよう、足すのは理由ごとに1件までにする（原則16。tyumajinさんの決定）。
    関連するテスト：tests/test_selfhood_voice.py の test_託された家族の答えには理由ごとに本人の元の発言が付く
    """
    room = min(OWN_WORDS_PER_REASON, LIMITS["advocacy_quotes_per_reason"] - len(found))
    if room <= 0:
        return []
    by_id = {item.id: item for item in statements}
    seen = {quote["statement_id"] for quote in found}
    added: list[dict] = []
    for number in numbers:
        if not 1 <= number <= len(pieces):
            continue
        for statement_id in own_words_of.get(pieces[number - 1]["key"], []):
            item = by_id.get(statement_id)
            if item is None or item.source not in _SELF_SOURCES or item.id in seen:
                continue
            seen.add(item.id)
            added.append(advocacy.statement_view(conn, item, self_name))
            if len(added) >= room:
                return added
    return added


def used_statement_ids(view: dict) -> list[int]:
    """主張が根拠に使った記録の番号を返す。推論の記録に残し、あとからたどれるようにする。"""
    ids = {quote["statement_id"] for reason in view["reasons"] for quote in reason["evidence"]}
    ids.update(item["quote"]["statement_id"] for item in view["objections"])
    return sorted(ids)


def speak(
    conn: sqlite3.Connection, material: Material, today: date | None = None, client=None, *, with_own_words: bool = False
) -> tuple[dict, ai.AIResult]:
    """材料からツインの主張を作る。with_own_words は、理由ごとに本人の元の発言を添えるとき（託された家族）。"""
    system = advocacy_prompts.voice_system(_self_name(conn))
    model_text = advocacy_prompts.voice_model_text(material.element_lines)
    if material.selfhood_body:
        system += selfhood_prompts.selfhood_voice_rules(_self_name(conn))
        model_text = selfhood_prompts.answer_model_text(material.selfhood_body, material.element_lines[1:], material.heart_lines)
    text = advocacy_prompts.voice_user_text(
        material.task, material.now_lines, material.word_lines, today, material.reading_lines, material.question_reading
    )
    result = ask_without_medical_orders(
        conn,
        lambda extra: ai.advocate(system=system, model_text=model_text, text=text + extra, client=client),
        "ツインのアドボカシー",
        "ツインのアドボカシー",
        check=lambda draft: reasons_without_elements(draft, len(material.pieces or material.elements)),
    )
    own = material.piece_words if with_own_words else None
    view = build_view(conn, result.value, material.statements, material.elements, material.pieces, own)
    return {**view, "reading": reading_note(material)}, result


def _delivered(conn: sqlite3.Connection, viewer: User, turn: dict) -> list[Statement]:
    """その答えで届けた本人の言葉と証言を、今の先頭の行で返す。必ず材料に入れる。今は見えない行は入れない。"""
    scopes = consent.visible_scopes(conn, viewer)
    kept: dict[int, Statement] = {}
    for item in [*turn.get("self_words", []), *turn.get("testimony", [])]:
        head = ledger.head_of(conn, item["statement_id"])
        if head.share_scope in scopes and head.confirm_status is not ConfirmStatus.RESTING:
            kept[head.id] = head
    return list(kept.values())


def _latest_voice(conn: sqlite3.Connection, turn_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM advocacy_voices WHERE turn_id = ? ORDER BY rowid DESC LIMIT 1", (turn_id,)
    ).fetchone()
    if row is None:
        return None
    log = conn.execute("SELECT result FROM inference_logs WHERE id = ?", (row["log_id"],)).fetchone()
    return {"id": row["id"], "turn_id": row["turn_id"], "created_at": row["created_at"], **json.loads(log["result"]), "decide": DECIDE}


@router.post("/turns/{turn_id}/advocate")
def advocate_on_turn(
    body: VoiceInput,
    request: Request,
    turn_id: int = Path(ge=1, le=db.MAX_ID),
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """答えに続けて、ツインの主張を作る。作ってあれば AI を呼ばずに返す。ボタンからもチャットからも同じ道で呼ぶ。"""
    # 託された人（家族と専門職）はみな、ツインに聞くと代弁で答える。ツールの有無で分けない（tyumajinさんの決定）
    advocacy._require_supporter(conn, viewer)
    row = conn.execute(
        "SELECT * FROM advocacy_turns WHERE id = ? AND asker_user_id = ?", (turn_id, viewer.id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    with request.app.state.voice_lock:
        existing = None if body.again else _latest_voice(conn, turn_id)
        if existing is not None:
            return {"voice": existing}
        turn = advocacy._turn_view(conn, row)
        # 材料がまったくないときは、考えを作らない（ツインに聞くと会議の画面で同じにする）。健康や病気の問いでも止めない（原則17）
        if not has_material(conn, viewer):
            return {"voice": None}
        now = clock.now(request.app)
        try:
            material = gather(
                conn, viewer, turn["question"], required=_delivered(conn, viewer, turn), health=advocacy.health_noticed(turn)
            )
            view, result = speak(conn, material, today=now.date(), with_own_words=viewer.role is Role.FAMILY)
        except advocacy._AI_ERRORS as error:
            raise HTTPException(status_code=503, detail=AI_FAILED_MESSAGE) from error
        # 推論の記録に一緒に残し、読み直したときも同じ1文が出るようにする
        # 関連するテスト：tests/test_advocacy_chat.py の test_問いに合う本人の言葉がなければ_代弁の前に記録にないことの1文を付け_読み直しても残る
        view["off_record"] = OFF_RECORD if turn.get("not_yet") else None
        log_id = advocacy._log_inference(
            conn, InferenceKind.ADVOCACY, turn["question"], view, material.elements, used_statement_ids(view), result
        )
        with ledger.atomic(conn):
            db.insert_row(
                conn,
                "advocacy_voices",
                {"turn_id": turn_id, "log_id": log_id, "created_at": now.isoformat()},
            )
    return {"voice": _latest_voice(conn, turn_id)}
