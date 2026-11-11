"""心の目次。ツインとの会話エンジンの材料を、決まった手順で集めて渡す。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：本人の言葉を月ごとに読み直す材料を作り、読みを保存し、返事に渡す「人の名簿」と「心の目次」を組み立てる。
4つの極：3. 読み解き、積み上げる（読みを積む）、1. 書く・話す（返事に使う）。
守る原則：3（AI の読みと本人の言葉を分ける）、4（読みは仮説）、5（追記だけ）、9（本人の言葉は原文のまま）、
10（ほかの人に見せない）、12（本人になりきらない）、15（招かれた部屋だけを読む）。

最近のログをそのまま渡すだけでは、「妻」がだれで、その人が本人にとって何なのかが分からない。
そこで本人の言葉を月ごとに読み直して「その人や事柄は本人にとって何か、どう受け止めるとよいか」を
仮説として積み、人とテーマごとの目次に固める。問いのたびに似た文を探す形にしない（CLAUDE.md の「方式」）。
返事は目次を丸ごと読み、記録が増えても読む量は上限までにとどまる。

返事の材料は、次の3つに積む。
- 本人の言葉（言明の台帳。変えない）
- 月ごとの読み（役割「心の読み」、Sonnet 5.5、Message Batches API。level=月）
- 人とテーマの心の目次（役割「心の目次」、Opus 5.5。level=目次）
返事を書く処理は app/twin.py の respond_in_twin_room にある。

読みはこの表（heart_readings）にだけ持ち、API では返さない。専門職と託された家族の代弁には、見る人に見える言葉だけを
根拠にした読みの文を渡す（readings_for_supporters）。本人の言葉の本文は渡さない。
読みの材料は、ツインとの部屋から来た本人の言葉だけで、人だけの部屋は発言の表に入らないので材料に入らない。
本人の言葉は作り直せないので、言明は変えない。読みは言明から作り直せるので、作り直すときは新しい版を足す。
どのモデルで作ったかを行ごとに残す。

関連するテスト：tests/test_heart.py
決めた経緯：docs/decisions.md の「ツインとの会話エンジン」
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from app import config, db, prompts
from app.memory import date_label
from app.schemas import ConfirmStatus, HeartEntry, HeartLevel, HeartMerge, HeartMonth, Place, Role, ShareScope, Source

ROLE_MONTH = "心の読み"
ROLE_MERGE = "心の目次"
_THEMES = "テーマ"
_LIMITS = config.MEMORY_LIMITS
# 本人の発言の中で、続柄の代わりに出てくる呼び方。本人の発言にだけ使う（美穂さんの「母」は和子さんだが、本人の「母」は別の人）
_RELATION_WORDS = {
    "妻": ("妻", "家内", "女房", "嫁さん", "かみさん", "奥さん"),
    "夫": ("夫", "旦那", "主人"),
    "長女": ("娘",),
    "長男": ("息子",),
    "孫": ("孫",),
}
_INVITING_WORDS = ("一緒に", "誘って", "誘う", "という手も", "会いに行")
# 勧める言い方。「一緒に」がこの形と同じ文にあるときだけ、勧めと見る
_SUGGESTING = re.compile(r"のも|ませんか|ましょう|てみて|ては|といい|とよい|と良い|てください|どうですか|いかが")
# かぎかっこの中の句点では切らない。本人の言葉には句点が入る
_SENTENCE = re.compile(r"(?:「[^」]*」?|[^。！？!?「])+[。！？!?]?")
_DEATH_WORDS = ("亡くな", "なくな", "死", "逝", "命日")
_QUOTE = re.compile(r"「([^」]*)」?")
# 返事に出してはいけない、作業の言葉。実際に「〜は言い直しになるので」と書いた返事が出た（実測）
_WORKING_WORDS = ("言い直し", "reading", "reply", "目次", "読み取り", "分析", "とは言いません", "とは書きません")
# 本人との会話では冷たく聞こえる言い方。tyumajinさんの決定。専門職の問いの決まった文には使わない（app/advocacy.py）
_COLD_WORDS = (
    "まだ聞けて",
    "分かりません",
    "わかりません",
    "知りません",
    "存じません",
    "知っているふり",
    "存じ上げ",
    "出てきていません",
    "聞いていません",
    "うかがっていません",
    "伺っていません",
    "お聞きしていない",
    "聞いた覚えがありません",
    "伺った覚えがありません",
    "代わりに決める",
    "決めることはできません",
    "決められません",
)
_AGREEING_STARTS = ("そうですね", "そうですよね", "確かに", "たしかに", "そうかもしれません", "その通り", "そのとおり", "おっしゃるとおり", "おっしゃる通り")
_LINK_WORDS = re.compile(r"^(ただ|でも|けれど|それでも|かわりに)、?")
# 本人の言葉を伝える言い方。かぎかっこがないと、言い換えた文が本人の言葉として届いてしまう（原則9）
_HEARD_FORM = re.compile(r"うかが(い|っ)|おっしゃ|話されて|言うて")


@dataclass
class MonthRequest:
    """1か月分の読み直しの頼み。番号から言明の番号を引く。"""

    period: str
    system: str
    text: str
    numbers: dict[int, int]


@dataclass
class MergeRequest:
    """目次に固める頼み。person が None ならテーマの目次。"""

    key: str
    person: str | None
    system: str
    text: str
    numbers: dict[int, int]


@dataclass
class HeartBlock:
    """返事に渡す固定のブロック。並びが毎回同じなので、プロンプトキャッシュに当たる。"""

    text: str
    numbers: dict[int, int] = field(default_factory=dict)
    words: list[str] = field(default_factory=list)


def _bare(name: str) -> str:
    return re.sub(r"(さん|くん|ちゃん|先生)$", "", name)


def self_name(conn: sqlite3.Connection) -> str:
    person = db.self_user(conn)
    return person.display_name if person else "本人"


def roster_lines(conn: sqlite3.Connection) -> list[str]:
    """人の名簿。人の表の事実をプログラムが書く。亡くなったことを AI に推させないため。"""
    lines = []
    for user in db.list_users(conn):
        if user.role is Role.SELF:
            continue
        parts = [part for part in (user.relation, "亡くなった方" if user.is_deceased else None) if part]
        lines.append(f"- {user.display_name}：{'。'.join(parts) or user.role.value}")
    return lines


def gone_names(conn: sqlite3.Connection) -> list[str]:
    """亡くなった方の呼び名を、さんを除いて返す。"""
    return [_bare(user.display_name) for user in db.list_users(conn) if user.is_deceased]


def people_words(conn: sqlite3.Connection) -> tuple[str, ...]:
    """本人の発言に人が出たかを見る語。深いモデルを選ぶ判断に使う（app/twin_model.py）。"""
    words: list[str] = []
    for user in db.list_users(conn):
        if user.role is Role.SELF:
            continue
        words.append(_bare(user.display_name))
        words.extend(_RELATION_WORDS.get(user.relation or "", ()))
    return tuple(word for word in words if word)


def _invites(sentence: str, gone: list[str]) -> bool:
    """1つの文が、亡くなった方と何かをするよう勧めているかを返す。

    「一緒に」は、勧める形（〜のもいい、〜ませんか など）のときだけ数える。「長い年月を一緒に過ごした和子さん」のような
    過ぎた年月の話まで勧めと取り、寄り添う返事をまるごと捨てていた（2026-11-06 の実測）。
    """
    if not any(name in sentence for name in gone):
        return False
    if any(word in sentence for word in _INVITING_WORDS if word != "一緒に"):
        return True
    return "一緒に" in sentence and _SUGGESTING.search(sentence) is not None


def invites_gone(reply: str, gone: list[str]) -> bool:
    """亡くなった方と何かをするよう勧める文かを返す。実際に夕飯を勧める一言が出たことがある。"""
    return any(_invites(sentence, gone) for sentence in _SENTENCE.findall(reply))


def says_gone(reply: str, heard: str) -> bool:
    """本人が言っていないのに、亡くなったことを口にした文かを返す。訂正しない決まりをプログラムでも守る。"""
    return "亡くな" in reply and not any(word in heard for word in _DEATH_WORDS)


def drop_gone_sentences(reply: str, gone: list[str], heard: str) -> tuple[str, list[dict[str, str]]]:
    """亡くなった方と何かをする案の文と、本人が言っていない死の言葉の文だけを落とす。落とした文と理由も返す。

    返事を丸ごと決まった文に替えると、寄り添う言葉まで消えて冷たく聞こえる（tyumajinさんの指摘）ので、文ごとに落とす。
    関連するテスト：tests/test_heart.py の test_亡くなった方の文だけを落とし_ほかの受け止めは残す
    """
    kept: list[str] = []
    notes: list[dict[str, str]] = []
    for sentence in _SENTENCE.findall(reply):
        if _invites(sentence, gone):
            notes.append({"文": sentence, "理由": "亡くなった方と何かをする案"})
        elif says_gone(sentence, heard):
            notes.append({"文": sentence, "理由": "本人が言っていない死の言葉"})
        else:
            kept.append(sentence)
    return "".join(kept).strip(), notes


def clean_reply(reply: str, sources: list[str]) -> str:
    """check_reply の文だけを返す。"""
    return check_reply(reply, sources)[0]


def check_reply(reply: str, sources: list[str], *, negative: bool = False) -> tuple[str, list[dict[str, str]]]:
    """作業の言葉の文と、記録で確かめられないかぎかっこの文を落とす。全部落ちたら空を返す。落とした文と理由も返す。

    かぎかっこは本人や家族の言葉に見える。記録にない言葉を人の言葉として出さない（原則9）。
    関連するテスト：tests/test_heart.py の test_記録にないかぎかっこの文は出さない
    """
    joined = "".join(sources)
    kept = []
    dropped = False
    notes: list[dict[str, str]] = []
    for sentence in _SENTENCE.findall(reply):
        if any(word in sentence for word in _WORKING_WORDS):
            dropped = True
            notes.append({"文": sentence, "理由": "作業の言葉"})
            continue
        if any(word in sentence for word in _COLD_WORDS):
            dropped = True
            notes.append({"文": sentence, "理由": "冷たく言い切る文"})
            continue
        # 否定的な話には、寄り添っても同意しない。tyumajinさんの決定
        if negative and sentence.strip().startswith(_AGREEING_STARTS):
            dropped = True
            notes.append({"文": sentence, "理由": "否定的な話への同意"})
            continue
        quotes = _QUOTE.findall(sentence)
        if any(quote.rstrip("。、").strip() not in joined for quote in quotes):
            dropped = True
            notes.append({"文": sentence, "理由": "記録にないかぎかっこ"})
            continue
        if not quotes and _HEARD_FORM.search(sentence):
            dropped = True
            notes.append({"文": sentence, "理由": "言い換えた本人の言葉"})
            continue
        # 前の文を落としたとき、つなぎの言葉が残ると、前の文を受けたまま読めてしまう
        kept.append(_LINK_WORDS.sub("", sentence) if dropped and not kept else sentence)
    return "".join(kept).strip(), notes


def _month_rows(conn: sqlite3.Connection, period: str = "") -> list[sqlite3.Row]:
    """読み直す本人の言葉を返す。ツインに話した言葉だけで、お休みは入れない。app/memory.py の _self_words と同じ絞り。

    「専門職だけ」は入れない。心の目次は託された家族への代弁の材料にもなるため。ツインに話した言葉が「専門職だけ」になる
    道は今はない（専門職から入る言明の場所は「チャット」）が、場所の決まりが変わっても漏れないよう、範囲でも外す。
    関連するテスト：tests/test_heart.py の test_専門職だけの言明は心の目次に読まない
    """
    return conn.execute(
        """
        SELECT s.id, s.body, s.effective_date
        FROM statements s
        WHERE s.source IN (?, ?)
          AND s.confirm_status <> ?
          AND s.share_scope <> ?
          AND s.effective_date LIKE ?
          AND s.id NOT IN (SELECT replaces_id FROM statements WHERE replaces_id IS NOT NULL)
          AND EXISTS (
              SELECT 1 FROM json_each(s.source_utterance_ids) j
              JOIN utterances u ON u.id = j.value
              WHERE u.place = ?
          )
        ORDER BY s.effective_date, s.rowid
        """,
        (
            Source.SELF_WORDS.value,
            Source.SELF_CORRECTION.value,
            ConfirmStatus.RESTING.value,
            ShareScope.PROFESSIONAL_ONLY.value,
            f"{period}%",
            Place.DIARY_AND_TALK.value,
        ),
    ).fetchall()


def months_to_read(conn: sqlite3.Connection) -> list[str]:
    """本人の言葉がある月を古い順に返す。"""
    return sorted({row["effective_date"][:7] for row in _month_rows(conn)})


def month_request(conn: sqlite3.Connection, period: str) -> MonthRequest | None:
    """1か月分の頼みを作る。本人の言葉を日付つきで並べる。"""
    rows = _month_rows(conn, period)
    if not rows:
        return None
    numbers = {number: row["id"] for number, row in enumerate(rows, start=1)}
    lines = []
    for number, row in enumerate(rows, start=1):
        lines.append(f"[{number}] {row['effective_date']}：{row['body']}")
    return MonthRequest(
        period,
        prompts.heart_month_system(self_name(conn)),
        prompts.heart_month_text(period, roster_lines(conn), lines),
        numbers,
    )


def next_generation(conn: sqlite3.Connection, level: HeartLevel) -> int:
    row = conn.execute("SELECT MAX(generation) FROM heart_readings WHERE level = ?", (level.value,)).fetchone()
    return (row[0] or 0) + 1


def _person_id(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    wanted = _bare(name)
    for user in db.list_users(conn):
        if user.role is not Role.SELF and _bare(user.display_name) == wanted:
            return user.id
    return None


def _real_ids(numbers: list[int], table: dict[int, int]) -> list[int]:
    """AI が返した番号を言明の番号に直す。渡していない番号は捨てる。AI の出力から記録を作らないため。"""
    seen: list[int] = []
    for number in numbers:
        statement_id = table.get(number)
        if statement_id is not None and statement_id not in seen:
            seen.append(statement_id)
    return seen


def save_month(
    conn: sqlite3.Connection,
    request: MonthRequest,
    result: HeartMonth,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    generation: int,
    moment: datetime,
) -> int:
    """月の読みを追記する。根拠が1つも残らない読みは入れない。"""
    saved = 0
    for item in result.items:
        evidence = _real_ids(item.evidence_numbers, request.numbers)
        if not evidence:
            continue
        db.insert_row(
            conn,
            "heart_readings",
            {
                "generation": generation,
                "level": HeartLevel.MONTH.value,
                "period": request.period,
                "topic": item.topic,
                "about_user_id": _person_id(conn, item.person),
                "reading": item.reading,
                "how_to_stay": item.how_to_stay,
                "evidence_statement_ids": evidence,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "created_at": moment.isoformat(),
            },
        )
        saved += 1
    return saved


def _latest_months(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """月ごとに最新の版の読みを返す。古い版は行のまま残る。"""
    return conn.execute(
        """
        SELECT * FROM heart_readings h
        WHERE h.level = ?
          AND h.generation = (SELECT MAX(x.generation) FROM heart_readings x WHERE x.level = h.level AND x.period = h.period)
        ORDER BY h.period, h.rowid
        """,
        (HeartLevel.MONTH.value,),
    ).fetchall()


def _statement_bodies(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    found: dict[int, sqlite3.Row] = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" for _ in part)
        for row in conn.execute(f"SELECT id, body, effective_date FROM statements WHERE id IN ({marks})", part):
            found[row["id"]] = row
    return found


def merge_requests(conn: sqlite3.Connection) -> list[MergeRequest]:
    """月の読みから、人ごと1つとテーマ全体で1つの頼みを作る。"""
    rows = _latest_months(conn)
    bodies = _statement_bodies(conn, sorted({i for row in rows for i in json.loads(row["evidence_statement_ids"])}))
    groups: dict[int | None, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["about_user_id"], []).append(row)
    name = self_name(conn)
    requests = []
    for about, items in groups.items():
        numbers: dict[int, int] = {}
        back: dict[int, int] = {}
        lines = []
        for row in items:
            evidence = []
            for statement_id in json.loads(row["evidence_statement_ids"]):
                statement = bodies.get(statement_id)
                if statement is None:
                    continue
                number = back.setdefault(statement_id, len(back) + 1)
                numbers[number] = statement_id
                evidence.append(f"[{number}]{statement['body'][:80]}")
            lines.append(f"- {row['period']}【{row['topic']}】{row['reading']} 受け止め方：{row['how_to_stay']} 言葉：{' '.join(evidence)}")
        person = db.get_user(conn, about) if about is not None else None
        requests.append(
            MergeRequest(
                f"u-{about}" if about is not None else "themes",
                person.display_name if person else None,
                prompts.heart_merge_system(name),
                prompts.heart_merge_text(person.display_name if person else None, roster_lines(conn), lines),
                numbers,
            )
        )
    return requests


def save_merge(
    conn: sqlite3.Connection,
    request: MergeRequest,
    result: HeartMerge,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    generation: int,
    moment: datetime,
) -> int:
    """目次の行を追記する。人の頼みなら1行だけ、その人の行として入れる。"""
    entries: list[HeartEntry] = result.entries[:1] if request.person else result.entries[: _LIMITS["heart_themes"]]
    saved = 0
    for entry in entries:
        evidence = _real_ids(entry.key_numbers, request.numbers)
        db.insert_row(
            conn,
            "heart_readings",
            {
                "generation": generation,
                "level": HeartLevel.INDEX.value,
                "topic": request.person or entry.topic,
                "about_user_id": _person_id(conn, request.person),
                "reading": entry.story,
                "now_reading": entry.now,
                "how_to_stay": entry.how_to_stay,
                "tender": entry.tender,
                "evidence_statement_ids": evidence,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "created_at": moment.isoformat(),
            },
        )
        saved += 1
    return saved


def _index_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """最新の版の目次を、人、テーマの順に返す。並びを固定してプロンプトキャッシュに当てる。"""
    row = conn.execute("SELECT MAX(generation) FROM heart_readings WHERE level = ?", (HeartLevel.INDEX.value,)).fetchone()
    if row[0] is None:
        return []
    return conn.execute(
        """
        SELECT * FROM heart_readings WHERE level = ? AND generation = ?
        ORDER BY about_user_id IS NULL, rowid
        """,
        (HeartLevel.INDEX.value, row[0]),
    ).fetchall()


def _word_line(number: int, statement: sqlite3.Row) -> str:
    return f"[{number}] {date_label(date.fromisoformat(statement['effective_date']))}：{statement['body']}"


def index_block(conn: sqlite3.Connection) -> HeartBlock:
    """返事に渡す名簿と目次を作る。読む量は上限で切る。記録が増えても読む量を増やさないため。"""
    roster = roster_lines(conn)
    parts = [
        "## 人の名簿（人の表からプログラムが入れた事実です）",
        *(roster or ["- まだだれも登録されていません。"]),
    ]
    index = _index_rows(conn)
    months = _latest_months(conn)
    recent_periods = sorted({row["period"] for row in months})[-_LIMITS["heart_recent_months"] :]
    recent = [row for row in months if row["period"] in recent_periods]
    if not index and not recent:
        return HeartBlock("\n".join(parts))
    wanted = [i for row in [*index, *recent] for i in json.loads(row["evidence_statement_ids"])]
    bodies = _statement_bodies(conn, sorted(set(wanted)))
    numbers: dict[int, int] = {}
    back: dict[int, int] = {}
    words: list[str] = []
    parts += ["", "## 心の目次（AI の読みです。本人の言葉ではありません。[番号] の行だけが本人の言葉です）"]
    total = sum(len(part) for part in parts)

    def quoted(ids: list[int], limit: int) -> list[str]:
        lines = []
        for statement_id in ids[:limit]:
            statement = bodies.get(statement_id)
            if statement is None:
                continue
            number = back.setdefault(statement_id, len(back) + 1)
            numbers[number] = statement_id
            words.append(statement["body"])
            lines.append(_word_line(number, statement))
        return lines

    for row in index:
        lines = [f"### {row['topic']}", f"これまで：{row['reading']}"]
        if row["now_reading"]:
            lines.append(f"今：{row['now_reading']}")
        if row["how_to_stay"]:
            lines.append(f"受け止め方：{row['how_to_stay']}")
        if row["tender"]:
            lines.append(f"そっとしておくこと：{row['tender']}")
        lines += quoted(json.loads(row["evidence_statement_ids"]), _LIMITS["heart_words_per_topic"])
        size = sum(len(line) for line in lines)
        if total + size > _LIMITS["heart_chars"]:
            break
        parts += lines
        total += size
    if recent:
        parts.append("### ここ最近の読み")
        for row in recent:
            lines = [f"- {row['period']}【{row['topic']}】{row['reading']} 受け止め方：{row['how_to_stay']}"]
            lines += quoted(json.loads(row["evidence_statement_ids"]), 2)
            size = sum(len(line) for line in lines)
            if total + size > _LIMITS["heart_chars"]:
                break
            parts += lines
            total += size
    return HeartBlock("\n".join(parts), numbers, words)


def readings_for_supporters(conn: sqlite3.Connection, visible: Callable[[int], bool], as_of: str | None = None) -> list[str]:
    """専門職と託された家族の代弁に渡す、心の目次の読みの行を返す。本人の言葉は入れない。

    根拠の言葉が1つでも見る人に見えなければ、その行ごと外す。「本人だけ」とお休みの言葉から作った読みを届けないため（原則10）。
    渡すのは AI の読みの文だけで、本人の言葉の本文は入れない。代弁に引く言葉はセルフの根拠から、見える範囲の関数を通して取る。
    受け止め方（how_to_stay）は本人との会話のための文なので渡さない。
    as_of は答え合わせで使う。目次はすべての年をまとめた読みで、その日より後の言葉も入っているため使えない。
    かわりに、その日までの1年の月の読みを新しい順に使い、量は今の目次と同じくらい（config の heart_chars_as_of）に切る。
    関連するテスト：tests/test_heart.py の test_代弁に渡す目次は見えない言葉の読みを外す
    """
    limit = _LIMITS["heart_chars"]
    if as_of is None:
        rows = list(_index_rows(conn))
    else:
        first = f"{int(as_of[:4]) - 1}{as_of[4:7]}"
        rows = sorted((row for row in _latest_months(conn) if first < row["period"] <= as_of[:7]), key=lambda row: row["period"], reverse=True)
        limit = _LIMITS["heart_chars_as_of"]
    lines: list[str] = []
    total = 0
    for row in rows:
        ids = json.loads(row["evidence_statement_ids"])
        if not ids or not all(visible(statement_id) for statement_id in ids):
            continue
        parts = [f"### {row['topic']}", f"これまで：{row['reading']}"]
        if row["now_reading"]:
            parts.append(f"今：{row['now_reading']}")
        if row["tender"]:
            parts.append(f"そっとしておくこと：{row['tender']}")
        size = sum(len(part) for part in parts)
        if total + size > limit:
            break
        lines += parts
        total += size
    return lines


def used_statement_ids(block: HeartBlock, numbers: list[int]) -> list[int]:
    """返事に使った記録の番号を確かめ、言明の番号に直す。渡していない番号は捨てる。"""
    return _real_ids(numbers, block.numbers)


__all__ = [
    "HeartBlock",
    "MergeRequest",
    "MonthRequest",
    "ROLE_MERGE",
    "ROLE_MONTH",
    "gone_names",
    "index_block",
    "clean_reply",
    "drop_gone_sentences",
    "invites_gone",
    "merge_requests",
    "month_request",
    "months_to_read",
    "next_generation",
    "people_words",
    "roster_lines",
    "save_merge",
    "save_month",
    "says_gone",
    "used_statement_ids",
]
