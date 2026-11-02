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
    life_stage: LifeStage
    summary: str | None = None
    self_confirmation: str | None = None


class Conversation(ConversationDraft):
    """会話"""

    id: int


class Photo(_Base):
    """写真"""

    id: int
    file: str
    taken_on: date | None = None
    description: str | None = None


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
    """利用者（架空の4人）"""

    id: int
    name: str
    role: Role


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


# ---- AI の結果の型。プログラムが確かめてから保存するので、そのまま表には入れない ----


class PhotoQuestion(_Base):
    """写真について本人に問いかける文。問いは1回に1つだけなので、1文だけを受け取る。"""

    question: str

    @field_validator("question")
    @classmethod
    def _one_question(cls, value: str) -> str:
        text = value.strip()
        ends = [char for char in text if char in "。！？!?\n.…"]
        has_words = any(char.isalnum() for char in text)
        if not has_words or len(ends) != 1 or text[-1] not in "？?":
            raise ValueError("問いかける文を1つだけにしてください")
        return text


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
