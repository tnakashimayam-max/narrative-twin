"""専門職とツインのチャット。

役割：専門職の問いに、意思モデルの要素を選び、根拠の本人の言葉を記録の本文のまま届ける。
4つの極：4. 10年後に代弁する。
守る原則：2（今の様子 → 本人の言葉 → AIの推測の順）、3（出どころを分ける）、9（本人の言葉は一字も変えない。
          記録がないことは「まだ聞けていません」）、10（根拠が見えない要素は使わない）、
          11（推定は別の枠に、根拠、反対の根拠、確からしさ、確かめ方を付ける）、12（本人になりきらない）。

処理は3段に分ける。守る決まりは「AI の出力が本人の言葉の本文になる道がないこと」で、受け取るのは番号だけにする。
1. 要素を選ぶ（役割：ツインの答え）：意思モデルを読み、問いに合う要素の番号を選ぶ。本文は渡さない。
2. 言葉を選ぶ（役割：言葉を選ぶ）：選ばれた要素の根拠の本文を読み、問いに合う言葉の番号を選ぶ。番号が渡した候補にあり、
   出どころが枠に合うかをプログラムが確かめる。失敗したら、プログラムが要素の根拠から順に取り出す道へ落とす。
   API の一時の失敗が「記録がない」に見えないようにするため。
3. 本人の言葉がなければ「まだ聞けていません」と決まった文で出し、推定（役割：推定意思）を別の枠に作る。
要素の本文は AI の読み取りなので、本人の言葉としては出さない。届ける本文はプログラムが記録から取り出す。

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
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import ai, clock, config, consent, db, devlog, handover, ledger, model_store, observations, prompts, twin
from app.auth import NOT_FOUND, current_user, get_conn
from app.records import order_for_display
from app.schemas import (
    ConfirmStatus,
    ElementKind,
    InferenceKind,
    ModelElement,
    RequestKind,
    Role,
    Source,
    Statement,
    User,
    WordPick,
    _CLAIMS_ABOUT_RECORDS,
    _sentences,
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

_LEAD_MAX_CHARS = 60
_LEAD_FORM = re.compile(r"(について|に関する|にまつわる)(の)?(記録|話)です。?$")
_COPIED_RUN = 8
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


def _shares_run(text: str, body: str) -> bool:
    """案内の文に、届ける本文と同じ文字の並びがあるかを返す。"""
    return any(body[index : index + _COPIED_RUN] in text for index in range(max(len(body) - _COPIED_RUN + 1, 0)))


def usable_lead(lead: str | None, delivered: tuple[str, ...] | list[str] = ()) -> str | None:
    """AI の短い案内を確かめる。通らなければ捨てる。答えに関わらないので、やり直さない。"""
    if not lead:
        return None
    text = lead.strip()
    if not text or len(text) > _LEAD_MAX_CHARS or "「" in text or "『" in text or "おっしゃ" in text:
        return None
    # 仕組みの言葉は、読む人には意味がない
    if "番号" in text or "要素" in text:
        return None
    # 話題の名前だけにする。経緯や理由を書くと、本人の言葉の言い換えや、推測と示さない読み取りになるため
    if not _LEAD_FORM.search(text) or len(_sentences(text)) != 1:
        return None
    # 読んだのは記録の一部なので、記録全体の有無は言い切らない
    if any(word in text for word in _CLAIMS_ABOUT_RECORDS):
        return None
    # AI は本文を読んでいるので、案内に写していないかを確かめる（原則9の言い換えを防ぐ）
    if any(_shares_run(text, body) for body in delivered):
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


def _evidence_groups(
    elements: list[ModelElement], heads: dict[int, Statement], scopes, question: str, per_element: int | None = None
) -> list[list[Statement]]:
    """選ばれた要素ごとに、根拠のうち見える先頭の行を、関係の近い順に集める。"""
    per_element = per_element or LIMITS["quotes_per_element"]
    groups: list[list[Statement]] = []
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
        group: list[Statement] = []
        for candidate in candidates:
            if candidate.source in _SELF_SOURCES:
                if taken[candidate.source] >= per_element:
                    continue
                taken[candidate.source] += 1
            group.append(candidate)
        groups.append(group)
    return groups


def _take_evenly(groups: list[list[Statement]]) -> list[Statement]:
    """要素ごとに1件ずつ順に取り出す。

    新しい順に並べてから上限で切ると、古い年の言葉がいつも落ちる。
    2026年に話した言葉が2036年に届かなくなるので、選んでから並べる。
    関連するテスト：tests/test_advocacy.py の test_上限で切っても_選ばれた要素の古い本人の言葉が残る
    """
    chosen: dict[int, Statement] = {}
    for rank in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if rank < len(group):
                chosen.setdefault(group[rank].id, group[rank])
    return list(chosen.values())


def _sections(conn: sqlite3.Connection, picked: list[Statement], self_name: str) -> dict:
    """出どころごとに上限まで選び、原則2の順に並べる（原則2、3）。picked は問いに近い順か _take_evenly の順。"""

    def choose(sources, limit: int) -> list[dict]:
        # 片方を含む同じ言葉をまとめてから、上限で切る
        same = [item for item in picked if item.source in sources]
        bodies = [item.body for item in same]
        kept = [item for item in same if not any(item.body in other and item.body != other for other in bodies)][:limit]
        # 問いに近い言葉に印を付ける。画面は印のある言葉を先に見せ、残りは本人が開いたときに出す。答えが長くなりすぎるため。
        # いちばん新しい言葉は必ず印を付ける。今の本人を最優先するため（原則2）
        first = kept[: LIMITS["quotes_first_shown"]]
        newest = max(kept, key=lambda item: item.effective_date, default=None)
        if newest is not None and newest not in first:
            first = [*first[:-1], newest]
        top = {item.id for item in first}
        return [{**statement_view(conn, item, self_name), "top": item.id in top} for item in order_for_display(kept)]

    return {
        "self_words": choose(_SELF_SOURCES, LIMITS["quotes_total"]),
        "testimony": choose(_TESTIMONY_SOURCES, LIMITS["testimonies"]),
        "guesses": choose({Source.AI_GUESS}, LIMITS["guesses"]),
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
    picked = _take_evenly(_evidence_groups(elements, heads, scopes, question))
    # 本人の言葉を先に残す。読む量の上限で切ってから、原則2の順に並べる
    picked.sort(key=lambda item: item.source not in _SELF_SOURCES)
    ordered = order_for_display(picked[: LIMITS["estimate_statements"]])
    lines = [
        f"[{number}] {item.effective_date.isoformat()}／{item.source.value}／{_speaker_name(conn, item)}：{item.body}"
        for number, item in enumerate(ordered, start=1)
    ]
    return ordered, lines


def _readable(items: list[Statement]) -> list[Statement]:
    """新しい順に並べ、ほかの引用に含まれる文は1つにまとめる。同じ言葉が二重に出ないようにする。"""
    ordered = order_for_display(items)
    return [
        item
        for item in ordered
        if not any(other.id != item.id and item.body in other.body and item.body != other.body for other in ordered)
    ]


def _usable_estimate(draft, materials: list[Statement]) -> dict | None:
    """推定の案を確かめる。渡していない番号の根拠を通さない。根拠がなければ枠を出さない。言い切りは型で止めている。"""
    if not draft.estimate or not draft.how_to_confirm:
        return None
    if any(word in draft.estimate for word in ("病気", "病名", "認知症", "診断")):
        return None

    def pick(numbers: list[int]) -> list[Statement]:
        seen: dict[int, Statement] = {}
        for number in numbers:
            if 1 <= number <= len(materials):
                seen.setdefault(materials[number - 1].id, materials[number - 1])
        return list(seen.values())

    evidence = _readable(pick(draft.evidence_numbers))
    evidence_ids = {item.id for item in evidence}
    evidence_bodies = [item.body for item in evidence]
    counter = _readable(
        [
            item
            for item in pick(draft.counter_numbers)
            if item.id not in evidence_ids and not any(item.body in body for body in evidence_bodies)
        ]
    )
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


@dataclass
class WordPool:
    """言葉を選ぶ係に渡す候補。番号（1始まり）から元の行を引く。"""

    statements: list[Statement]
    direct: list[str]
    related: list[str]
    others: list[str]


def _word_line(conn: sqlite3.Connection, number: int, item: Statement) -> str:
    return f"[{number}] {item.effective_date.isoformat()}／{item.source.value}／{_speaker_name(conn, item)}：{item.body}"


def _by_speaker(items: list[Statement]) -> list[Statement]:
    """話した人ごとに順に並べる。新しい順のままだと、よく書く人の言葉だけで上限が埋まるため。"""
    groups: dict[int | None, list[Statement]] = {}
    for item in items:
        groups.setdefault(item.speaker_user_id, []).append(item)
    ordered: list[Statement] = []
    while groups:
        for speaker in list(groups):
            ordered.append(groups[speaker].pop(0))
            if not groups[speaker]:
                del groups[speaker]
    return ordered


def _build_word_pool(
    conn: sqlite3.Connection,
    viewer: User,
    direct: list[ModelElement],
    related: list[ModelElement],
    heads: dict[int, Statement],
    scopes,
    question: str,
) -> WordPool:
    """候補を作る。定数で切るので、記録が増えても読む量は増えない。

    本人の言葉は、直接答える要素を先に、要素ごとに順に取る。近い順で切ると、ほかの要素の言葉が落ちるため。
    証言と推測は要素の根拠をほとんど通らないので、根拠にある分を先に入れ、足りない分を新しい順で埋める。
    語の一致では探さない。質問のたびに似た文を探す方式にしないため（CLAUDE.md の「方式」）。
    関連するテスト：tests/test_advocacy.py の test_記録が増えても_言葉を選ぶ係に渡す件数は上限で変わらない
    """
    per_element = LIMITS["word_pool_per_element"]
    direct_picked = _take_evenly(_evidence_groups(direct, heads, scopes, question, per_element))
    related_picked = _take_evenly(_evidence_groups(related, heads, scopes, question, per_element))
    limit = LIMITS["word_pool_self"]
    seen: set[int] = set()

    def is_other(item: Statement) -> bool:
        return item.source in _TESTIMONY_SOURCES or item.source is Source.AI_GUESS

    def take(items: list[Statement], room: int) -> list[Statement]:
        kept: list[Statement] = []
        for item in items:
            if len(kept) >= room:
                break
            if item.id not in seen:
                seen.add(item.id)
                kept.append(item)
        return kept

    direct_self = take([item for item in direct_picked if item.source in _SELF_SOURCES], limit)
    related_self = take([item for item in related_picked if item.source in _SELF_SOURCES], limit - len(direct_self))
    room = LIMITS["word_pool_others"]
    direct_others = take([item for item in direct_picked if is_other(item)], room)
    related_others = take([item for item in related_picked if is_other(item)], room - len(direct_others))
    # 新しい順の証言を先に、次に推測。本人が話していないので、問いに関係があるかは言葉を選ぶ係が決める
    visible = [item for item in consent.visible_statements(conn, viewer) if item.confirm_status is not ConfirmStatus.RESTING]
    newest = sorted(visible, key=lambda item: item.effective_date, reverse=True)
    testimonies = _by_speaker([item for item in newest if item.source in _TESTIMONY_SOURCES])
    guesses = [item for item in newest if item.source is Source.AI_GUESS]
    left = room - len(direct_others) - len(related_others)
    other_testimonies = take(testimonies, left)
    other_guesses = take(guesses, left - len(other_testimonies))

    statements: list[Statement] = []

    def lines_for(items: list[Statement]) -> list[str]:
        lines = []
        for item in items:
            statements.append(item)
            lines.append(_word_line(conn, len(statements), item))
        return lines

    direct_lines = lines_for([*direct_self, *direct_others])
    related_lines = lines_for([*related_self, *related_others])
    other_lines = lines_for([*other_testimonies, *other_guesses])
    return WordPool(statements, direct_lines, related_lines, other_lines)


def _chosen_statements(pick: WordPick, pool: WordPool) -> list[Statement]:
    """番号を候補の行に直す。渡していない番号と、出どころが枠に合わない番号は捨てる（原則3）。"""

    def frame(numbers: list[int], sources) -> list[Statement]:
        kept: dict[int, Statement] = {}
        for number in numbers:
            if 1 <= number <= len(pool.statements) and pool.statements[number - 1].source in sources:
                kept.setdefault(pool.statements[number - 1].id, pool.statements[number - 1])
        return list(kept.values())

    return [
        *frame(pick.self_numbers, _SELF_SOURCES),
        *frame(pick.testimony_numbers, _TESTIMONY_SOURCES),
        *frame(pick.guess_numbers, {Source.AI_GUESS}),
    ]


def _elements_view(
    elements: list[ModelElement], heads: dict[int, Statement], delivered_ids: set[int]
) -> list[dict]:
    """届けた言葉を根拠に持つ要素を返す。「根拠をたどる」で発言の原文まで確かめられるようにする。"""
    shown = [
        element
        for element in elements
        if any(number in heads and heads[number].id in delivered_ids for number in element.evidence_statement_ids)
    ]
    return [
        {
            "element_id": element.id,
            "body": element.body,
            "kind": element.kind.value,
            "unit": element.unit.value,
            "valid_from": element.valid_from.isoformat(),
            "confirmed": model_store.confirmed_by_self(element, heads),
        }
        for element in shown
    ]


def answer(conn: sqlite3.Connection, viewer: User, question: str, client=None, today: date | None = None) -> dict:
    """問いに答える。読む量はどの段も上限で切る。"""
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

    picked = _take_evenly(_evidence_groups(direct, heads, scopes, question))
    lead = None
    words_differ = False
    pool = _build_word_pool(conn, viewer, direct, related, heads, scopes, question)
    if pool.statements:
        try:
            words = ai.pick_words(
                system=prompts.word_pick_system(self_name),
                text=prompts.word_pick_user_text(question, pool.direct, pool.related, pool.others),
                client=client,
            )
        except _AI_ERRORS:
            # 要素の根拠から順に取り出す道へ落とす。一時の失敗を「記録がない」に見せないため
            pass
        else:
            _log_call(conn, "言葉を選ぶ", words, "専門職の問い")
            picked = _chosen_statements(words.value, pool)
            lead = words.value.lead
            words_differ = words.value.words_differ
    sections = _sections(conn, picked, self_name)
    delivered = [item["body"] for key in ("self_words", "testimony", "guesses") for item in sections[key]]
    has_self_words = bool(sections["self_words"])
    delivered_ids = {item["statement_id"] for key in ("self_words", "testimony", "guesses") for item in sections[key]}
    self_ids = {item["statement_id"] for item in sections["self_words"]}
    view: dict = {
        "question": question,
        "notice": _NOTICES.get(kind),
        "lead": usable_lead(lead, delivered),
        # いちばん上に今の様子を出す（原則2）。問いに関係なく、いつも入れる
        "now": observations.now_view(conn, viewer, LIMITS["now"]),
        **sections,
        "not_yet": None if has_self_words else NOT_YET,
        # 言葉が時期によって変わっているか、証言と食い違っているときに、決まった1文を出す。
        # 本文を読んだ言葉を選ぶ係の判断に、届けた本人の言葉を根拠に持つ「変化」の要素を重ねる
        "changed": has_self_words
        and (
            words_differ
            or any(
                element.kind is ElementKind.CHANGE
                and any(number in heads and heads[number].id in self_ids for number in element.evidence_statement_ids)
                for element in [*direct, *related]
            )
        ),
        "estimate": None,
        "elements": _elements_view([*direct, *related], heads, delivered_ids),
    }
    estimate_log = None
    if not has_self_words:
        estimate_log = _estimate(conn, view, question, [*direct, *related], heads, scopes, self_name, client, today)
        if kind is not RequestKind.HEALTH:
            _remember_question(conn, question)
    answer_log = _log_inference(conn, InferenceKind.TWIN_ANSWER, question, view, direct, sorted(delivered_ids), result)
    devlog.record(
        "専門職の問い",
        outcome=devlog.SUCCESS if has_self_words else devlog.NOT_YET,
        ms=devlog.since(started),
        question_chars=len(question),
        model_chars=len(model_text),
        pool_statements=len(pool.statements),
    )
    return {"view": view, "answer_log": answer_log, "estimate_log": estimate_log}


def _estimate(conn, view: dict, question: str, elements, heads, scopes, self_name: str, client, today=None) -> int | None:
    """本人の言葉がないときに、推定の枠を作る。材料がなければ呼ばない。作れなくても答えは返す。"""
    if not elements:
        return None
    materials, lines = _materials(elements, heads, scopes, question, conn)
    if not materials:
        return None
    try:
        result = ai.estimate_will(
            system=prompts.estimate_system(self_name), text=prompts.estimate_user_text(question, lines, today), client=client
        )
    except _AI_ERRORS:
        return None
    _log_call(conn, "推定意思", result, "専門職の問い")
    usable = _usable_estimate(result.value, materials)
    if usable is None:
        return None
    draft = usable["draft"]
    # 本人の言葉の根拠がなければ、確からしさを上げない（原則11）。家族の言葉を本人の意思として扱わないため
    has_self_ground = any(item.source in _SELF_SOURCES for item in usable["evidence"])
    view["estimate"] = {
        "text": usable["text"],
        "evidence": [statement_view(conn, item, self_name) for item in usable["evidence"]],
        "counter": [statement_view(conn, item, self_name) for item in usable["counter"]],
        "confidence": _confidence_words(draft.confidence if has_self_ground else 0.0),
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
            outcome = answer(conn, viewer, question, today=clock.now(request.app).date())
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
    seen_utterances: set[int] = set()
    # 日付の順にする。同じ発言の原文は最初の1回だけ返す
    for head in sorted((heads[number] for number in element.evidence_statement_ids), key=lambda item: item.effective_date):
        utterances = [
            ledger.get_utterance(conn, value) for value in head.source_utterance_ids if value not in seen_utterances
        ]
        seen_utterances.update(head.source_utterance_ids)
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
