"""保存するデータの型と、項目に入る決めた言葉。

役割：Enum と Pydantic で型を決める。データベースの表（app/db.py）はこの型に合わせて作る。
4つの極：3. 読み解き、積み上げる（中心の型）。ほかの極の記録の型もここに置く。
守る原則：3（出どころを分ける）、4（推測を推測として持つ）、10（共有範囲）、
          13（確かめた状態を分ける）。

Enum の値は画面にもそのまま出る日本語にしている。別名への変換を挟むと、
保存された言葉と画面の言葉がずれる余地が生まれるため。
型と表が食い違わないことは tests/test_schema.py で確かめる。
全体の図は docs/architecture.md にある。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


class Role(StrEnum):
    """役割"""

    SELF = "本人"
    FAMILY = "家族"
    CONNECTED = "縁のある人"
    PROFESSIONAL = "専門職"


class Source(StrEnum):
    """出どころ（原則3）"""

    SELF_WORDS = "本人の言葉"
    OBSERVATION = "今の様子（観察）"
    FAMILY_TESTIMONY = "家族の証言"
    CONNECTED_TESTIMONY = "縁のある人の証言"
    AI_GUESS = "AIの推測"
    SELF_CORRECTION = "本人の訂正"


class ConfirmStatus(StrEnum):
    """確認の状態"""

    UNCONFIRMED = "未確認"
    CONFIRMED = "確認済み"
    CORRECTED = "訂正済み"
    RESTING = "お休み"


class Period(StrEnum):
    """時期"""

    CHILDHOOD = "子ども時代"
    YOUTH = "若いころ"
    PRIME = "働き盛り"
    RETIRED = "退職後"
    NOW = "今"


class Theme(StrEnum):
    """テーマ"""

    FAMILY = "家族"
    WORK = "仕事・役割"
    HOME = "住まい・土地"
    LIKES = "好きなこと"
    VALUES = "大切にしてきたこと"
    HELP = "人の手を借りること"
    HARDSHIP = "つらかったこと"


class Petal(StrEnum):
    """花びら（キットウッドの5つの心理的ニーズ。AIの見立てとして付ける）"""

    COMFORT = "くつろぎ"
    ATTACHMENT = "結びつき"
    INCLUSION = "共にあること"
    OCCUPATION = "たずさわること"
    IDENTITY = "自分らしさ"


class LifeStage(StrEnum):
    """人生の段階"""

    ACTIVE = "元気な時期"
    SUPPORT = "要支援"
    CARE = "要介護"


class ShareScope(StrEnum):
    """共有範囲（原則10）。だれに見えるかの表は app/consent.py にある。"""

    SELF_ONLY = "本人だけ"
    FAMILY = "家族"
    CONNECTED = "縁のある人まで"
    DECISION_SUPPORT = "意思決定支援用"


class Place(StrEnum):
    """どこで話したか"""

    DIARY_AND_TALK = "日記と会話"
    THREAD = "スレッド"
    CHAT = "チャット"
    MEETING = "会議"
    GROUP = "グループ"


class Perspective(StrEnum):
    """解釈の視点（NAK-4V の4つの視点）"""

    SURFACE = "表の言葉"
    NEED = "本当のニーズ"
    INTENT = "本人の真意"
    FEELING = "気持ち"


class InterpretationStatus(StrEnum):
    """解釈の状態（仮説リーフ）"""

    HYPOTHESIS = "仮説"
    CONFIRMED = "本人が確かめた"


class ElementKind(StrEnum):
    """意思モデルの種類。判断の決まりの条件は、要素の condition の列に持つ。"""

    VALUE = "価値"
    RULE = "判断の決まり"
    PRIORITY = "優先順位"
    CHANGE = "変化"


class ElementStatus(StrEnum):
    """意思モデルの状態"""

    AI_VIEW = "AIの見立て"
    SELF_CONFIRMED = "本人確認済み"


class ConsolidationUnit(StrEnum):
    """固める単位"""

    MONTH = "月"
    YEAR = "年"
    LIFE_STAGE = "人生の段階"


class InferenceKind(StrEnum):
    """推論の種類"""

    NEXT_QUESTION = "次の問い"
    THREAD_QUESTION = "スレッドの問い"
    TWIN_ANSWER = "ツインの答え（チャット・会議）"
    ESTIMATED_WILL = "推定意思"
    DRAFT = "原案"
    EVIDENCE_CHECK = "根拠チェック"
    FIXATION_WARNING = "固定化警告"


class QuestionResult(StrEnum):
    """質問の結果"""

    ANSWERED = "答えた"
    PARTIAL = "途中"
    RESTING = "お休み"
    LATER = "また今度"


class EvidenceVerdict(StrEnum):
    """根拠チェックの判定"""

    SELF_WORDS = "本人の言葉で裏付けあり"
    TESTIMONY_ONLY = "家族・縁のある人の証言のみ"
    AI_ONLY = "AIの推測だけ（要確認）"
    NO_EVIDENCE = "根拠なし"


class EvalQuestionType(StrEnum):
    """評価の質問の種類"""

    CURRENT_VIEW = "今の考え"
    CHANGE = "変化"
    UNKNOWN = "知らないこと"
    VERBATIM = "一字一句"


class EvalMethod(StrEnum):
    """評価で比べる方式"""

    WILL_MODEL = "意思モデル"
    RAG = "RAG"


class Gender(StrEnum):
    """性別（任意。家系図の形に使う）"""

    MALE = "男性"
    FEMALE = "女性"
    UNKNOWN = "不明"


class Residence(StrEnum):
    """住まい"""

    TOGETHER = "同居"
    NEAR = "近く"
    FAR = "遠く"


class FamilyLinkKind(StrEnum):
    """家系図の関係。親子は from が親、to が子。"""

    PARENT_CHILD = "親子"
    SPOUSE = "夫婦"


class RelativeKind(StrEnum):
    """人の候補の関係"""

    PARENT = "親"
    CHILD = "子"
    SPOUSE = "配偶者"
    SIBLING = "きょうだい"
    OTHER = "その他"


class CandidateStatus(StrEnum):
    """人の候補の状態。決めるのは本人。"""

    PENDING = "候補"
    REGISTERED = "登録した"
    REJECTED = "ちがう"
    LATER = "今はいい"


class RoomKind(StrEnum):
    """部屋の種類"""

    TWIN = "ツイン"
    PAIR = "1対1"
    GROUP = "グループ"


class MessageKind(StrEnum):
    """部屋の行の種類。お知らせは、ツインが入った、はずれたなどの表示。"""

    SAY = "発言"
    NOTICE = "お知らせ"


class PhotoKind(StrEnum):
    """写真の種類。AI に送ってよいのは会話の写真だけ。"""

    TALK = "会話"
    ROOM = "部屋"
    LETTER = "便り"
    PROFILE = "プロフィール"


class LinkStrength(StrEnum):
    """つながりの線の種類。本人が選ぶ。AI は提案しない。"""

    STRONG = "強い"
    NORMAL = "ふつう"
    WEAK = "弱い"
    STRESS = "ストレス"


class SupportDirection(StrEnum):
    """支援の向き。本人が選ぶ。"""

    FROM_SELF = "本人から"
    TO_SELF = "相手から"
    BOTH = "お互い"
    NONE = "なし"


class SplitTargetKind(StrEnum):
    """どこまで言明に分けたかの印の対象。"""

    CONVERSATION = "会話"
    LETTER = "便り"


class CareKind(StrEnum):
    """問いを続けず、人につなぐ場面の種類。"""

    NONE = "なし"
    BODY = "体の不調"
    LIFE = "命に関わる"
    DISTRESS = "強いつらさ"


class GroupTurnKind(StrEnum):
    """グループのツインが書く場面の種類。"""

    QUESTION = "問い"
    ANSWER = "答え"
    CANNOT = "聞けていない"
    NOTHING = "書かない"




def _no_duplicates(petals: list[Petal]) -> list[Petal]:
    if len(set(petals)) != len(petals):
        raise ValueError("花びらが重なっています")
    return petals


# 花びらは1つの記録に0〜2個で、重ならない
Petals = Annotated[list[Petal], Field(max_length=2), AfterValidator(_no_duplicates)]
# 確からしさ：0が「まったく確かでない」、1が「とても確か」
Confidence = Annotated[float, Field(ge=0, le=1)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _exactly_one_speaker(speaker_user_id: int | None, speaker_is_twin: bool) -> None:
    """話した人は、利用者かツインのどちらか一方。

    ツインは役割の決めた言葉にないので利用者の表に入れない。AIの言葉を
    本人の発言と取り違えないよう、両方の空きと両方の記入を型の段階で止める。
    """
    if (speaker_user_id is None) != speaker_is_twin:
        raise ValueError("話した人は、利用者かツインのどちらか一方にしてください")


# ---- 中心の3つ：発言・言明・意思モデル ----


class UtteranceDraft(_Base):
    """発言の入力。会話かスレッド（便り）のどちらかにぶら下がる。"""

    conversation_id: int | None = None
    letter_id: int | None = None
    speaker_user_id: int | None = None
    speaker_is_twin: bool = False
    text: str = Field(min_length=1)
    spoken_at: datetime
    place: Place

    @model_validator(mode="after")
    def _check(self) -> UtteranceDraft:
        _exactly_one_speaker(self.speaker_user_id, self.speaker_is_twin)
        if self.place is Place.THREAD:
            if self.letter_id is None or self.conversation_id is not None:
                raise ValueError("スレッドの発言は便りにぶら下げてください")
        elif self.conversation_id is None or self.letter_id is not None:
            raise ValueError("スレッド以外の発言は会話にぶら下げてください")
        return self


class Utterance(UtteranceDraft):
    """発言。原文は作り直せないので、追記だけにしている。"""

    id: int


class StatementDraft(_Base):
    """言明の入力。

    TSS（話した人と、誰についての記録かを分ける仕組み）：NAK の考え方をもとに期間中に
    新しく書いた実装として、speaker_user_id / speaker_is_twin（話した人）と
    subject_user_id（誰についてか）を別の項目として持つ。
    目的は、家族の言葉を本人の意思と取り違えないこと。
    """

    source_utterance_ids: list[int]
    subject_user_id: int
    speaker_user_id: int | None = None
    speaker_is_twin: bool = False
    source: Source
    confirm_status: ConfirmStatus = ConfirmStatus.UNCONFIRMED
    body: str = Field(min_length=1)
    period: Period
    theme: Theme
    petals: Petals = []
    share_scope: ShareScope
    effective_date: date
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _check_speaker(self) -> StatementDraft:
        _exactly_one_speaker(self.speaker_user_id, self.speaker_is_twin)
        return self


class Statement(StatementDraft):
    """言明の台帳の1行。追記だけで、書き換えない。"""

    id: int
    replaces_id: int | None = None
    created_at: datetime


class Interpretation(_Base):
    """解釈。

    NAK-4V（1つの言明を4つの視点で読む仕組み）と、仮説リーフ（解釈を仮説として
    言明の下に並べ、本人が確かめると状態が変わる仕組み）を、NAK の考え方をもとに
    期間中に新しく書いた実装として、この表で持つ。
    目的は、AIの読み取りを本人の言葉と分けて持つこと。
    解釈は app/splitter.py が仮説として入れる。本人が確かめる処理はまだない。
    """

    id: int
    statement_id: int
    perspective: Perspective
    viewpoint_role: Role
    body: str
    confidence: Confidence
    caution: str | None = None
    evidence_statement_ids: list[int]
    status: InterpretationStatus


class ModelElement(_Base):
    """意思モデルの要素。現時点では表の定義だけで、要素を作る処理はない。

    previous_version_id：同じ要素の前の版を指す。版の番号だけでは、本文や花びらが
    変わった版どうしをつなげないために足した列。

    unconfirmed_reconsolidations：本人に確かめられないまま固め直した回数。固定化警告
    （同じ見立てが確かめられないまま続くと印を付ける仕組み）の材料で、NAK の考え方を
    もとに期間中に新しく書いた実装として持つ。警告を出す処理はまだない。
    """

    id: int
    kind: ElementKind
    body: str
    condition: str | None = None
    petals: Petals = []
    confidence: Confidence
    status: ElementStatus
    valid_from: date
    valid_to: date | None = None
    evidence_statement_ids: list[int]
    unit: ConsolidationUnit
    version: int
    previous_version_id: int | None = None
    last_confirmed_on: date | None = None
    unconfirmed_reconsolidations: int = 0


# ---- 画面と記録のためのもの ----


class ConversationDraft(_Base):
    """会話の入力。チャットと会議の発言も、1回を1行としてここにぶら下げる。"""

    conversation_date: date
    trigger: str | None = None
    theme: Theme | None = None
    life_stage: LifeStage = LifeStage.ACTIVE
    summary: str | None = None
    self_confirmation: str | None = None
    room_id: int | None = None
    closed_at: datetime | None = None


class Conversation(ConversationDraft):
    """会話"""

    id: int


class Photo(_Base):
    """写真"""

    id: int
    file: str
    taken_on: date | None = None
    description: str | None = None
    owner_user_id: int | None = None
    kind: PhotoKind
    created_at: datetime


class LetterDraft(_Base):
    """便り（タイムラインの投稿）の入力。共有の相手は共有範囲のうち2つだけを使う。"""

    conversation_id: int | None = None
    body: str
    photo_id: int | None = None
    share_with: ShareScope
    shared_at: datetime

    @model_validator(mode="after")
    def _check_share(self) -> LetterDraft:
        if self.share_with not in (ShareScope.FAMILY, ShareScope.CONNECTED):
            raise ValueError("便りの共有の相手は、家族か縁のある人までです")
        return self


class Letter(LetterDraft):
    """便り（タイムラインの投稿）"""

    id: int


class CommentDraft(_Base):
    """コメントの入力。"""

    letter_id: int
    author_user_id: int | None = None
    body: str
    commented_at: datetime
    is_twin_question: bool = False
    client_key: str | None = None

    @model_validator(mode="after")
    def _check_author(self) -> CommentDraft:
        _exactly_one_speaker(self.author_user_id, self.is_twin_question)
        return self


class Comment(CommentDraft):
    """コメント。本文は発言としても保存し、その発言の番号を utterance_id に持つ。"""

    id: int
    utterance_id: int


class ConsolidationRun(_Base):
    """固める処理の記録。どのモデルで作ったかを残すので、あとから検証できる。"""

    id: int
    unit: ConsolidationUnit
    period_start: date
    period_end: date
    input_statement_ids: list[int]
    output_element_ids: list[int]
    model: str
    input_tokens: int
    output_tokens: int
    run_at: datetime


class InferenceLog(_Base):
    """推論の記録。AIを使わない種類もあるので、モデルとトークン数は空でもよい。"""

    id: int
    kind: InferenceKind
    question: str | None = None
    result: str
    element_ids: list[int]
    statement_ids: list[int]
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime


class QuestionLog(_Base):
    """質問の記録。まだ聞けていない質問は、聞いた日と結果を空にして持つ。"""

    id: int
    question: str
    theme: Theme | None = None
    period: Period | None = None
    petals: Petals = []
    asked_on: date | None = None
    result: QuestionResult | None = None
    conversation_id: int | None = None
    letter_id: int | None = None
    room_id: int | None = None
    utterance_id: int | None = None


class CarePlanDraft(_Base):
    """ケアプラン原案"""

    id: int
    self_intent: str
    long_term_goal: str
    evidence_statement_ids: list[int]
    evidence_element_ids: list[int]
    verdict: EvidenceVerdict
    created_on: date
    previous_version_id: int | None = None


class User(_Base):
    """人。ログインできる人だけでなく、亡くなった方も家系図のために入る。"""

    id: int
    name: str
    role: Role
    called_name: str | None = None
    birth_year: int | None = None
    gender: Gender | None = None
    relation: str | None = None
    residence: Residence | None = None
    is_deceased: bool = False
    memo: str | None = None
    photo_id: int | None = None
    removed_at: datetime | None = None

    @property
    def display_name(self) -> str:
        """呼ぶときの名前。呼ばれ方がなければ名前を使う。"""
        return self.called_name or self.name


class Delegation(_Base):
    """託す設定。託す人（招く専門職を含む）と範囲と、本人が決めた日。"""

    id: int
    delegate_user_id: int
    scope: ShareScope
    decided_on: date


class AccessLog(_Base):
    """閲覧の記録。誰がいつ何を開いたか。"""

    id: int
    viewer_user_id: int
    target: str
    opened_at: datetime


class AICall(_Base):
    """AI の呼び出しの記録。どのモデルで作ったかを残すので、あとから検証できる。

    言明への分割は推論の記録の種類に入らないので、呼び出し全体をここに残す。
    """

    id: int
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    is_batch: bool = False
    target: str | None = None
    called_at: datetime
    seconds: float | None = None


class Room(_Base):
    """部屋。"""

    id: int
    kind: RoomKind
    name: str | None = None
    created_at: datetime


class RoomMember(_Base):
    """部屋の人。はずれた人は left_at が入る。"""

    room_id: int
    user_id: int
    joined_at: datetime
    left_at: datetime | None = None


class RoomRead(_Base):
    """人ごとの読んだ位置。last_read_seq は部屋の中の順番。未読と既読に使い、AI には渡さない。"""

    room_id: int
    user_id: int
    last_read_seq: int
    updated_at: datetime


class HandoverTheme(_Base):
    """本人が「意思決定支援用」に入れると決めたテーマ。決めた日を画面に出す（原則10）。"""

    id: int
    theme: Theme
    decided_on: date


class StoryMark(_Base):
    """架空ログをどこまで読み込んだかの印。2回読み込まないため。"""

    stage: str
    loaded_at: datetime


class StoryKey(_Base):
    """架空ログの言明の鍵と番号の対応。番号は乱数なので、保存した結果は鍵で言明を指す。"""

    key: str
    statement_id: int


class AdvocacyTurn(_Base):
    """専門職とツインのやり取りの1回。問いと答えの本文は推論の記録（inference_logs）にある。"""

    id: int
    asker_user_id: int
    client_key: str
    answer_log_id: int
    estimate_log_id: int | None = None
    created_at: datetime


class AICallCache(_Base):
    """AI の呼び出しのプロンプトキャッシュの量。既存の ai_calls の表を変えないよう、別の表に持つ。"""

    ai_call_id: int
    cache_read_tokens: int
    cache_write_tokens: int


class TwinPresence(_Base):
    """グループにツインが入っている間。conversation_id の会話の発言だけをツインが読む。"""

    id: int
    room_id: int
    conversation_id: int
    joined_at: datetime
    left_at: datetime | None = None


class RoomMessage(_Base):
    """部屋に出す1行。AI に渡す発言は本文を utterances にだけ持ち、ここからは番号で指す。"""

    id: int
    room_id: int
    sender_user_id: int | None = None
    is_twin: bool = False
    kind: MessageKind
    text: str | None = None
    photo_id: int | None = None
    utterance_id: int | None = None
    conversation_id: int | None = None
    question_log_id: int | None = None
    client_key: str | None = None
    sent_at: datetime


class FamilyLink(_Base):
    """家系図の関係。親子は from が親、to が子。"""

    id: int
    from_user_id: int
    to_user_id: int
    kind: FamilyLinkKind
    created_at: datetime


class MapPlace(_Base):
    """つながりの地図の、人でないつながり（病院、デイサービス、町内会など）。ログインはできない。"""

    id: int
    name: str
    kind: str | None = None
    created_at: datetime
    removed_at: datetime | None = None


class MapLink(_Base):
    """つながりの線。本人が選ぶ。"""

    id: int
    user_id: int | None = None
    place_id: int | None = None
    strength: LinkStrength
    direction: SupportDirection
    updated_at: datetime


class PersonCandidate(_Base):
    """話に出てきた人の候補。本人が決めるまで、地図にも一覧にも足さない。"""

    id: int
    name: str
    relation: str | None = None
    relative_kind: RelativeKind
    related_to_name: str | None = None
    is_deceased: bool = False
    gender: Gender
    evidence_utterance_id: int
    status: CandidateStatus
    created_at: datetime
    decided_at: datetime | None = None


class SplitMark(_Base):
    """どこまで言明に分けたかの印。"""

    target_kind: SplitTargetKind
    target_id: int
    last_utterance_rowid: int
    updated_at: datetime


class JobFailure(_Base):
    """裏の処理の失敗の記録。"""

    id: int
    job: str
    target: str | None = None
    message: str
    failed_at: datetime


# ---- AI の結果の型。プログラムが確かめてから保存するので、そのまま表には入れない ----


# 本人になりきる言い方。ツインの文に入っていたら受け取らず、1回やり直す（原則12）。
_FIRST_PERSON = ("私は", "私が", "私の", "私も", "私に", "わたしは", "わたしが", "わたしの", "僕は", "僕が", "ぼくは", "俺は")


def _twin_voice(text: str) -> str:
    """ツインの文を確かめる。空でなく、本人になりきっていないこと。"""
    cleaned = text.strip()
    if not any(char.isalnum() for char in cleaned):
        raise ValueError("文が空です")
    if any(word in cleaned for word in _FIRST_PERSON):
        raise ValueError("「私は」を使わないでください")
    return cleaned


# 「？」を付けない問いの形。文末で見る。「そうですか。」は受け止めなので入れない。
_HIDDEN_QUESTION = re.compile(r"(?<!そう)(?:でしょうか|ますか|ましたか|ませんか|ですか|でしたか)[。！!…]*$")


def _no_question_mark(text: str) -> str:
    """受け止めの文に問いを混ぜない。問いは1回に1つまでを、型で守るため。"""
    if any(mark in text for mark in "？?"):
        raise ValueError("ここには問いを書かないでください")
    for sentence in re.split(r"(?<=[。！!])|\n", text):
        if _HIDDEN_QUESTION.search(sentence.strip()):
            raise ValueError("ここには問いを書かないでください")
    return text


def _one_question(text: str) -> str:
    """問いがちょうど1つで、「？」で終わること。"""
    cleaned = _twin_voice(text)
    if sum(cleaned.count(mark) for mark in "？?") != 1 or cleaned[-1] not in "？?":
        raise ValueError("問いを1つだけにしてください")
    return cleaned


class TwinTurn(_Base):
    """ツインの1回の返事。受け止めと問いを別の項目で受け取り、問いを1つまでに保つ。

    previous_question_result は、直前の問いに本人がどう応じたかの AI の読み取り。
    断ったと読んだら、プログラムがその話題をお休みにする。
    """

    reply: str
    question: str | None = None
    question_theme: Theme | None = None
    care: CareKind = CareKind.NONE
    previous_question_result: QuestionResult | None = None

    @field_validator("reply")
    @classmethod
    def _check_reply(cls, value: str) -> str:
        return _no_question_mark(_twin_voice(value))

    @field_validator("question")
    @classmethod
    def _check_question(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _one_question(value)


class TwinPhotoTurn(TwinTurn):
    """写真を読んだときの返事。写真の説明は、あとの会話で写真を送り直さずに話を続けるために残す。"""

    photo_description: str = Field(min_length=1, max_length=120)


class ConversationSummary(_Base):
    """会話の要約。AI の読み取りで、本人の言葉として扱わない。"""

    summary: str = Field(min_length=1, max_length=300)


class TwinClosing(_Base):
    """「今日はここまで」のときの返事と、今日の暮らしに役立つ一言。"""

    reply: str
    tip: str

    @field_validator("reply", "tip")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _no_question_mark(_twin_voice(value))


class ThreadQuestion(_Base):
    """スレッドのツインの問い。書かないときは null。"""

    question: str | None = None

    @field_validator("question")
    @classmethod
    def _check_question(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _one_question(value)


class GroupTurn(_Base):
    """グループのツインの答え。"""

    kind: GroupTurnKind
    text: str = ""

    @model_validator(mode="after")
    def _check_text(self) -> GroupTurn:
        if self.kind is GroupTurnKind.QUESTION:
            self.text = _one_question(self.text)
        elif self.kind is GroupTurnKind.ANSWER:
            self.text = _no_question_mark(_twin_voice(self.text))
        return self


class LetterBodyDraft(_Base):
    """便りの下書き。本人が直してから共有する。"""

    body: str = Field(min_length=1, max_length=120)


class PersonCandidateDraft(_Base):
    """話に出てきた人の候補。人の仲の良さや悪さは項目に持たない。AI に決めさせないため。"""

    name: str = Field(min_length=1, max_length=40)
    relation: str = Field(default="", max_length=40)
    relative_kind: RelativeKind = RelativeKind.OTHER
    related_to: str | None = Field(default=None, max_length=40)
    is_deceased: bool = False
    gender: Gender = Gender.UNKNOWN
    utterance_number: int


class PersonCandidateResult(_Base):
    """人の候補の一覧。"""

    people: list[PersonCandidateDraft]


class SplitInterpretation(_Base):
    """解釈の案（NAK-4V の1つの視点）。保存するときは必ず仮説になる。"""

    perspective: Perspective
    viewpoint_role: Role
    body: str = Field(min_length=1)
    confidence: Confidence
    caution: str | None = None


class SplitStatement(_Base):
    """言明の案。

    話した人と誰についてかは、入力に書かれた名前で受け取る（TSS）。
    根拠の発言は、入力で振った1始まりの番号で受け取る。発言の本当の番号は53ビットの乱数なので、
    AI に書き写させると誤りやすいため。共有範囲は受け取らない。見せる範囲は本人が決めるため。
    """

    source: Source
    subject: str
    speaker: str
    body: str = Field(min_length=1)
    period: Period
    theme: Theme
    petals: Petals = []
    utterance_numbers: list[int]
    interpretations: list[SplitInterpretation] = []
    confirm_question: str | None = None


class SplitResult(_Base):
    """言明に分けた結果。"""

    statements: list[SplitStatement]


class ConsolidatedElement(_Base):
    """固める処理が作る、意思モデルの要素の案。

    根拠は入力で振った番号で受け取り、プログラムが確かめて本当の番号に直す。
    conflicts_with は食い違う今の要素の番号、continues は前回の結果から続く要素の番号。
    """

    kind: ElementKind
    body: str = Field(min_length=1, max_length=200)
    condition: str | None = None
    petals: Petals = []
    confidence: Confidence
    evidence_numbers: list[int]
    conflicts_with: int | None = None
    continues: int | None = None
    confirm_question: str | None = None


class ConsolidationResult(_Base):
    """固める処理の結果。"""

    elements: list[ConsolidatedElement]


class RequestKind(StrEnum):
    """専門職の頼みの種類。原則に触れる頼みには、決まった1文を添える。"""

    NORMAL = "通常"
    IMPERSONATE = "なりきり"
    REPHRASE = "言い換え"
    HEALTH = "健康の判断"
    ASSERT = "言い切り"


class TwinAnswerPick(_Base):
    """ツインの答えの AI の出力。要素の番号を選ぶだけで、本人の言葉の本文は持たない。

    direct_numbers は問いに直接答える要素、related_numbers は関係はあるが直接は答えない要素。
    lead は短い案内で、本人の言葉として扱わない。プログラムが確かめて、通らなければ捨てる。
    """

    direct_numbers: list[int] = []
    related_numbers: list[int] = []
    request_kind: RequestKind = RequestKind.NORMAL
    lead: str | None = None


class EstimateDraft(_Base):
    """推定意思の案。本人の言葉とは別の枠に出す（原則11）。根拠の番号はプログラムが確かめる。"""

    estimate: str = Field(min_length=1, max_length=200)
    evidence_numbers: list[int]
    counter_numbers: list[int] = []
    # 問いの事柄そのものを本人が話している材料の番号。要素の文には細かい事柄が残らないことがあるので、ここで拾い直す
    quote_numbers: list[int] = []
    confidence: Confidence
    how_to_confirm: str = Field(min_length=1, max_length=200)


class Evaluation(_Base):
    """評価"""

    id: int
    question: str
    correct_answer: str
    question_type: EvalQuestionType
    method: EvalMethod
    result: str | None = None
    is_correct: bool | None = None
    read_amount: int | None = None
