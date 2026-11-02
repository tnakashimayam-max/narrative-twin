"""ツインの言葉を作り、確かめて保存する。

役割：本人とツインの部屋、グループ、スレッドで、AI の答えをプログラムの決まりに通して発言にする。
4つの極：1. 書く・話す、2. 分かち合う。
守る原則：1（判断しない）、6（お休みの話題とつらい出来事は聞かない）、7（引き留めない）、
          12（本人になりきらない）、14（スレッドのツインは問いを1つ）、15（招かれた部屋だけを読む）。

プロンプトの決まりは AI へのお願いで、守られるとは限らない。次のことはプログラムで決める。
- 問いは1回に1つまで（型で受け止めと問いを分ける。app/schemas.py）。
- お休みのテーマ、つらかったことのテーマ、同じ問い、本人が終えたそうなときの問いは落とす。
- 問いが2回続いたら、次は受け止めるだけにする。尋問のようにしないため。
- 受け止めの文から、気持ちの決めつけと、本人が今話していない体やつらさの話の文を外す。
- 体の急な不調、命に関わる言葉、強いつらさには問いを続けず、つなぎ先の文をプログラムが添える。
  語の一覧を AI の判定に重ねるのは、AI が断ったり見落としたりしても、つなぎ先を必ず出すため。
- グループでは、ツインが入ったあとのその部屋の発言だけを渡す。スレッドでは、便りとコメントだけを渡す。
  本人の考えや気持ちを聞かれたら、AI を呼ばずに決まった文を返す。

問いの選び方は、固める処理ができるまでの仮の決まり（docs/decisions.md）。
関連するテスト：tests/test_twin.py、tests/test_rooms.py、tests/test_timeline.py
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app import ai, config, db, ledger, memory, photos, prompts, room_store
from app.schemas import (
    CareKind,
    Conversation,
    GroupTurnKind,
    Place,
    QuestionResult,
    Role,
    Room,
    RoomMessage,
    Theme,
    User,
    UtteranceDraft,
)

# 語の一覧は AI の判定に重ねるもの。一覧だけで見分けようとはしない。
_LIFE_WORDS = (
    "死にたい",
    "死のう",
    "消えたい",
    "自殺",
    "生きていてもしょうがない",
    "生きててもしょうがない",
    "生きていても仕方",
    "生きてても仕方",
    "生きている意味",
    "いなくなりたい",
)
_BODY_WORDS = (
    "胸が痛",
    "胸の痛",
    "胸が苦し",
    "息が苦し",
    "息苦し",
    "息ができ",
    "ろれつ",
    "意識が",
    "血を吐",
    "激しい痛",
    "急に動けな",
    "倒れた",
)
_DISTRESS_WORDS = ("もう耐えられない", "もう限界", "つらくてたまらない", "苦しくてたまらない")

# つなぎ先の文。AI の文ではなく、プログラムが必ず添える。番号は厚生労働省の案内で確かめた。
_CARE_LINES = {
    CareKind.BODY: "急な体の不調は、すぐに身近な人に伝えてください。かかりつけの病院か、急ぐときは119番に電話してください。",
    CareKind.LIFE: "おひとりで抱えないでください。身近な人に伝えてください。よりそいホットライン（0120-279-338、24時間）でも話を聞いてもらえます。",
    CareKind.DISTRESS: "身近な人に話してみてください。かかりつけの病院や、よりそいホットライン（0120-279-338、24時間）でも話を聞いてもらえます。",
}
# AI が答えられないときの受け止め。つなぎ先の文の前に置く。
_CARE_RECEIVE = {
    CareKind.BODY: "それは心配です。",
    CareKind.LIFE: "お話ししてくださって、ありがとうございます。",
    CareKind.DISTRESS: "お話ししてくださって、ありがとうございます。",
}
_CARE_ORDER = [CareKind.NONE, CareKind.DISTRESS, CareKind.BODY, CareKind.LIFE]
# 句点で終わる問いの形。この場面では原因を聞かないので、受け止めの文に混ざっていたら決まった文に替える。
_HIDDEN_QUESTION = re.compile(r"(ますか|ませんか|ですか|でしたか|でしょうか|ありますか)[。！!]?")

# 本人が終えたそうな言葉。問いを落とし、引き留めない。誤って落としても、問わないだけで害は小さい。
_GOODBYE_WORDS = (
    "もう帰",
    "帰ります",
    "帰るね",
    "今日はいい",
    "今日はもういい",
    "もういいよ",
    "そろそろ",
    "おしまい",
    "疲れた",
    "もう寝",
    "寝ます",
    "またね",
    "また今度",
    "じゃあね",
    "おやすみ",
    "さようなら",
)

# AI から聞かない話題の語。問いにこの語があれば落とす。
_NOT_TO_ASK = ("病気", "病院", "薬", "痛み", "痛い", "体調", "具合", "亡くな", "つらかった", "辛かった", "お金", "借金")

# 活用で形が変わる語は、本人が話したかを語の頭で見る。「痛くて」と話した人に「痛いのですね」と返せるようにするため。
_TOPIC_STEMS = {
    "痛み": "痛",
    "痛い": "痛",
    "病気": "病",
    "病院": "病",
    "亡くな": "亡",
    "つらかった": "つら",
    "辛かった": "辛",
}

# 本人の気持ちを決めつける言い方。プロンプトで頼んでも実測で出たので、この文だけプログラムで外す。
# やり直しにしないのは、返事を待たせず、失敗も増やさないため。
_ASSUMING_WORDS = ("伝わります", "伝わってきます", "思い浮かびます", "大切にされて", "大切に使われて")

# 本人の考えや気持ちを聞く言葉。AI を呼ばずに決まった文を返す。
_ASKS_FOR_SELF = ("本音", "本心", "気持ち", "どう思", "考えて", "思って", "望んで", "したいの", "意思")

_MENTION = re.compile(r"[@＠]\s*ツイン")

# 本人の言葉を伝える言い方。今のところ、本人の言葉を番号で取り出して届ける処理はないので、
# AI がこの形で書いた文は、原文と照らせるものだけを通す。原則9（本人の言葉は記録の本文そのままだけ）。
_REPORTED_PAST = re.compile(r"おっしゃいました|おっしゃっていました|おっしゃっています|言っていました|話していました|言っています|話しています")
_QUOTE_THEN_REPORT = re.compile(r"「([^「」]+)」と(?:おっしゃ|言|話|うかが)")
_HEARD_RECEIPT = "お話をうかがいました。"
_FAMILY_WORDS = ("お父さん", "お母さん", "父", "母", "本人", "ご本人")
_MAX_QUESTIONS_IN_A_ROW = 2


def detect_care(text: str) -> CareKind:
    """語の一覧から、問いを続けない場面かを見る。"""
    if any(word in text for word in _LIFE_WORDS):
        return CareKind.LIFE
    if any(word in text for word in _BODY_WORDS):
        return CareKind.BODY
    if any(word in text for word in _DISTRESS_WORDS):
        return CareKind.DISTRESS
    return CareKind.NONE


def _stronger(first: CareKind, second: CareKind) -> CareKind:
    return max(first, second, key=_CARE_ORDER.index)


def says_goodbye(text: str) -> bool:
    return any(word in text for word in _GOODBYE_WORDS)


def question_is_allowed(question: str, theme: Theme | None, *, resting: set[Theme], asked: list[str]) -> bool:
    """AI の問いを、聞いてよいものか確かめる。"""
    if theme is Theme.HARDSHIP or (theme is not None and theme in resting):
        return False
    if any(word in question for word in _NOT_TO_ASK):
        return False
    return not memory.is_repeated(question, asked)


def words_are_real(text: str, sources: list[str]) -> bool:
    """本人の言葉を伝える文が、原文と照らせるかを返す。

    「〜」と言いました、の中身が今の話か前に聞いた本人の言葉にあれば通す。中身を示さずに
    「おっしゃっていました」と伝える文は、照らせないので通さない。
    """
    joined = "\n".join(sources)
    quotes = _QUOTE_THEN_REPORT.findall(text)
    if quotes:
        return all(quote in joined for quote in quotes)
    return _REPORTED_PAST.search(text) is None


def reports_self(text: str, self_user: User) -> bool:
    """グループの答えが、本人の考えや発言を伝える文かを返す。"""
    names = (self_user.name, self_user.display_name, *_FAMILY_WORDS)
    return _REPORTED_PAST.search(text) is not None and any(name in text for name in names)


def trim_reply(reply: str, heard: str) -> str:
    """受け止めの文から、気持ちの決めつけと、本人が今話していない体やつらさの話の文を外す。

    前の話に出た不調を AI が持ち出すと、関係のない話の途中で思い出させることになる（原則6）。
    heard は今の発言と写真の説明。全部の文が外れたら空を返し、呼ぶ側が決まった文に替える。
    """
    sentences = re.findall(r"[^。！!\n]+[。！!]*", reply)
    kept = [
        sentence
        for sentence in sentences
        if not any(word in sentence for word in _ASSUMING_WORDS)
        and not any(word in sentence and _TOPIC_STEMS.get(word, word) not in heard for word in _NOT_TO_ASK)
    ]
    return "".join(kept).strip()


def mentions_twin(text: str) -> bool:
    return _MENTION.search(text) is not None


def asks_for_self(text: str) -> bool:
    return any(word in text for word in _ASKS_FOR_SELF)


# ---- 保存の部品 ----


def _log_result(conn: sqlite3.Connection, role: str, result, target: str | None = None) -> None:
    db.log_ai_call(
        conn,
        role=role,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        target=target,
        seconds=result.seconds,
    )


def _log_spent(conn: sqlite3.Connection, role: str, error: Exception, target: str | None = None) -> None:
    """答えが得られなかった呼び出しも、費用がかかったので記録する。"""
    db.log_ai_call(
        conn,
        role=role,
        model=error.model,
        input_tokens=error.input_tokens,
        output_tokens=error.output_tokens,
        target=target,
    )


_SPENT_ERRORS = (ai.AIResultError, ai.AIRefusedError)
_ALL_AI_ERRORS = (*_SPENT_ERRORS, ai.AIUnavailableError, config.MissingApiKeyError)


def _say(conn, room: Room, conversation_id: int, place: Place, text: str, now: datetime, question_log_id=None):
    return room_store.say_in_ledger(
        conn,
        room_id=room.id,
        conversation_id=conversation_id,
        place=place,
        is_twin=True,
        text=text,
        sent_at=now,
        question_log_id=question_log_id,
    )


def _ask(
    conn,
    room: Room,
    conversation_id: int,
    place: Place,
    question: str,
    theme: Theme | None,
    now: datetime,
) -> None:
    """問いを発言として保存し、質問の記録に入れる。"""
    with ledger.atomic(conn):
        utterance = ledger.add_utterance(
            conn,
            UtteranceDraft(
                conversation_id=conversation_id,
                speaker_is_twin=True,
                text=question,
                spoken_at=now,
                place=place,
            ),
        )
        question_id = ledger.add_asked_question(
            conn,
            question=question,
            theme=theme,
            asked_on=now.date(),
            conversation_id=conversation_id,
            room_id=room.id,
            utterance_id=utterance.id,
        )
        room_store.insert_message(
            conn,
            room_id=room.id,
            is_twin=True,
            utterance_id=utterance.id,
            conversation_id=conversation_id,
            question_log_id=question_id,
            sent_at=now.isoformat(),
        )


# ---- 本人とツインの部屋 ----


def respond_in_twin_room(
    conn: sqlite3.Connection,
    *,
    room: Room,
    self_user: User,
    message: RoomMessage,
    now: datetime,
    photos_dir: Path,
    client=None,
) -> None:
    """本人の発言に返事をする。AI が失敗したら例外のまま上げる。本人の発言は保存済み。"""
    conversation_id = message.conversation_id
    said = memory.message_text(conn, message)
    keyword_care = detect_care(said)
    context = memory.build_twin_context(conn, room, conversation_id, self_user, now.date())
    system = prompts.twin_system(self_user.display_name)
    role = "ツインの会話"
    photo = photos.get_photo(conn, message.photo_id) if message.photo_id else None
    try:
        if photo is not None:
            role = "写真の問い"
            result = ai.twin_photo_turn(
                photo,
                photos.read_photo_bytes(photos_dir, photo),
                system=system,
                text=prompts.twin_user_text(**context) + "\n\n" + prompts.TWIN_PHOTO_INSTRUCTION,
                client=client,
            )
        else:
            result = ai.twin_turn(system=system, text=prompts.twin_user_text(**context), client=client)
    except _ALL_AI_ERRORS as error:
        if isinstance(error, _SPENT_ERRORS):
            _log_spent(conn, role, error, f"会話 {conversation_id}")
        if keyword_care is CareKind.NONE:
            raise
        # AI が断ったり答えなかったりしても、命や体に関わる言葉には、つなぎ先を必ず返す
        with ledger.atomic(conn):
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, _CARE_RECEIVE[keyword_care], now)
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, _CARE_LINES[keyword_care], now)
            _answer_pending(conn, conversation_id, None)
        return
    _log_result(conn, role, result, f"会話 {conversation_id}")
    turn = result.value
    care = _stronger(turn.care, keyword_care)
    question = turn.question
    if care is not CareKind.NONE or says_goodbye(said):
        question = None
    elif question:
        resting = memory.resting_themes(conn)
        asked = memory.asked_questions(conn, room.id)
        if not question_is_allowed(question, turn.question_theme, resting=resting, asked=asked):
            question = None
        elif memory.questions_in_a_row(conn, conversation_id) >= _MAX_QUESTIONS_IN_A_ROW:
            # 問いが続くと尋問のようになるので、受け止めるだけの回を入れる
            question = None
    # 本人の言葉を伝える形の文は、原文と照らせるものだけを通す
    sources = [context["transcript"], *context["self_words"]]
    if photo is not None:
        sources.append(result.value.photo_description)
    if question and not words_are_real(question, sources):
        question = None
    with ledger.atomic(conn):
        _answer_pending(conn, conversation_id, turn.previous_question_result)
        if photo is not None:
            conn.execute(
                "UPDATE photos SET description = ? WHERE id = ?",
                (result.value.photo_description[:120], photo.id),
            )
        heard = said + (result.value.photo_description if photo is not None else "")
        fallback = _CARE_RECEIVE[care] if care is not CareKind.NONE else _HEARD_RECEIPT
        reply = trim_reply(turn.reply, heard) or fallback
        if care is not CareKind.NONE and _HIDDEN_QUESTION.search(reply):
            reply = _CARE_RECEIVE[care]
        elif not words_are_real(reply, sources):
            reply = _HEARD_RECEIPT
        _say(conn, room, conversation_id, Place.DIARY_AND_TALK, reply, now)
        if care is not CareKind.NONE:
            _say(conn, room, conversation_id, Place.DIARY_AND_TALK, _CARE_LINES[care], now)
        if question:
            _ask(conn, room, conversation_id, Place.DIARY_AND_TALK, question, turn.question_theme, now)
            memory.mark_candidate_used(conn, question, now.date())


def _answer_pending(conn: sqlite3.Connection, conversation_id: int, result: QuestionResult | None) -> None:
    """直前の問いに結果を書く。本人が断ったと読めたら、その話題はお休みになる。"""
    conn.execute(
        "UPDATE question_logs SET result = ? WHERE conversation_id = ? AND result IS NULL AND asked_on IS NOT NULL",
        ((result or QuestionResult.ANSWERED).value, conversation_id),
    )


def close_in_twin_room(
    conn: sqlite3.Connection,
    *,
    room: Room,
    self_user: User,
    conversation: Conversation,
    now: datetime,
    client=None,
) -> None:
    """「今日はここまで」の返事を作る。短いお礼と、今日の暮らしに役立つ一言。"""
    lines = memory.conversation_lines(
        conn,
        conversation.id,
        self_user,
        max_lines=memory.LIMITS["current_utterances"],
        max_chars=memory.LIMITS["current_chars"],
        with_photos=True,
    )
    if not lines:
        return
    target = f"会話 {conversation.id}"
    try:
        result = ai.twin_closing(
            system=prompts.twin_closing_system(self_user.display_name),
            text=f"## 今日の日付\n{memory.date_label(now.date())}\n\n## 今日の話\n" + "\n".join(lines),
            client=client,
        )
    except _SPENT_ERRORS as error:
        _log_spent(conn, "ツインの会話", error, target)
        raise
    _log_result(conn, "ツインの会話", result, target)
    with ledger.atomic(conn):
        _say(conn, room, conversation.id, Place.DIARY_AND_TALK, result.value.reply, now)
        _say(conn, room, conversation.id, Place.DIARY_AND_TALK, result.value.tip, now)


# ---- グループ ----


def group_turn(
    conn: sqlite3.Connection,
    *,
    room: Room,
    conversation_id: int,
    trigger: RoomMessage,
    now: datetime,
    client=None,
) -> None:
    """グループのツインが書く。書くのは、@ツイン で呼ばれたときと、家族や知人の発言のあとの問いだけ。"""
    self_user = db.self_user(conn)
    sender = db.get_user(conn, trigger.sender_user_id)
    said = memory.message_text(conn, trigger)
    called = mentions_twin(said)
    pending = _pending_in_room(conn, room.id)
    if sender.role is Role.SELF:
        # 本人が書いたら、待っている問いには答えたことにする
        with conn:
            conn.execute(
                "UPDATE question_logs SET result = ? WHERE room_id = ? AND result IS NULL AND asked_on IS NOT NULL",
                (QuestionResult.ANSWERED.value, room.id),
            )
        pending = False
    if called and asks_for_self(said):
        _say(conn, room, conversation_id, Place.GROUP, prompts.CANNOT_ANSWER_FOR_SELF, now)
        return
    if not called and (sender.role is Role.SELF or pending):
        return
    # 渡すのは、ツインが入ったあとのこの部屋の発言だけ。写真の説明も渡さない。
    people = room_store.members_of(conn, room.id)
    lines = memory.conversation_lines(
        conn,
        conversation_id,
        self_user,
        max_lines=memory.LIMITS["current_utterances"],
        max_chars=memory.LIMITS["current_chars"],
    )
    roster = "、".join(f"{person.display_name}（{person.role.value}）" for person in people)
    scene = (
        f"{sender.display_name}が @ツイン で呼びました。"
        if called
        else f"{sender.display_name}の発言のあとです。呼ばれてはいません。"
    )
    target = f"部屋 {room.id}"
    try:
        result = ai.group_turn(
            system=prompts.group_system(self_user.display_name),
            text=f"## 部屋の人\n{roster}\n\n## あなたが入ったあとの発言\n" + "\n".join(lines) + f"\n\n## 今の場面\n{scene}",
            client=client,
        )
    except _SPENT_ERRORS as error:
        _log_spent(conn, "グループのツイン", error, target)
        raise
    _log_result(conn, "グループのツイン", result, target)
    turn = result.value
    # AI を待つ間に本人がツインをはずしたら、何も書かない
    presence = room_store.active_presence(conn, room.id)
    if presence is None or presence.conversation_id != conversation_id:
        return
    if turn.kind is GroupTurnKind.CANNOT or (
        turn.kind is GroupTurnKind.ANSWER and called and reports_self(turn.text, self_user)
    ):
        # 本人の考えや発言を伝える答えは、代わりに答えたことになるので書かない
        _say(conn, room, conversation_id, Place.GROUP, prompts.CANNOT_ANSWER_FOR_SELF, now)
    elif turn.kind is GroupTurnKind.ANSWER and called:
        _say(conn, room, conversation_id, Place.GROUP, turn.text, now)
    elif turn.kind is GroupTurnKind.QUESTION and not pending and not called:
        if not any(word in turn.text for word in _NOT_TO_ASK) and not memory.is_repeated(
            turn.text, memory.asked_questions(conn, room.id)
        ):
            _ask(conn, room, conversation_id, Place.GROUP, turn.text, None, now)


def _pending_in_room(conn: sqlite3.Connection, room_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM question_logs WHERE room_id = ? AND result IS NULL AND asked_on IS NOT NULL LIMIT 1",
        (room_id,),
    ).fetchone()
    return row is not None


# ---- スレッド ----


def thread_turn(
    conn: sqlite3.Connection, *, letter_id: int, author_user_id: int, now: datetime, client=None
) -> None:
    """コメントが付いたら、本人への問いを1つだけ書く。本人が答えるまで、次の問いは書かない。"""
    self_user = db.self_user(conn)
    if author_user_id == self_user.id:
        with conn:
            conn.execute(
                "UPDATE question_logs SET result = ? WHERE letter_id = ? AND result IS NULL AND asked_on IS NOT NULL",
                (QuestionResult.ANSWERED.value, letter_id),
            )
        return
    pending = conn.execute(
        "SELECT 1 FROM question_logs WHERE letter_id = ? AND result IS NULL AND asked_on IS NOT NULL LIMIT 1",
        (letter_id,),
    ).fetchone()
    if pending:
        return
    # 渡すのは、このスレッドに見えている便りとコメントだけ
    letter_body, lines = memory.thread_lines(conn, letter_id)
    asked = [
        row["question"]
        for row in conn.execute("SELECT question FROM question_logs WHERE letter_id = ?", (letter_id,))
    ]
    target = f"便り {letter_id}"
    try:
        result = ai.thread_question(
            system=prompts.thread_system(self_user.display_name),
            text=f"## 便り\n{letter_body}\n\n## コメント\n" + "\n".join(lines),
            client=client,
        )
    except _SPENT_ERRORS as error:
        _log_spent(conn, "スレッドの問い", error, target)
        raise
    _log_result(conn, "スレッドの問い", result, target)
    question = result.value.question
    if not question or any(word in question for word in _NOT_TO_ASK) or memory.is_repeated(question, asked):
        return
    with ledger.atomic(conn):
        comment = ledger.add_comment(
            conn,
            ledger.CommentDraft(letter_id=letter_id, body=question, commented_at=now, is_twin_question=True),
        )
        ledger.add_asked_question(
            conn,
            question=question,
            theme=None,
            asked_on=now.date(),
            letter_id=letter_id,
            utterance_id=comment.utterance_id,
        )
