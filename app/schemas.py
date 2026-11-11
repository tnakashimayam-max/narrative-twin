"""保存するデータの型と、項目に入る決めた言葉。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
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
    # 専門職が取り込んだ資料のうち、アセスメントなど専門職の内側の記録から残したもの。託された専門職と本人にだけ見え、
    # 託された家族には見えない（tyumajinさんの決定、2026-11-06。app/professional_records.py）
    PROFESSIONAL_ONLY = "専門職だけ"


class Place(StrEnum):
    """どこで話したか"""

    DIARY_AND_TALK = "日記と会話"
    THREAD = "スレッド"
    CHAT = "チャット"
    MEETING = "会議"
    GROUP = "グループ"


class InterpretationStatus(StrEnum):
    """解釈の状態。AI の読みは仮説として入り、本人が確かめると状態が変わる。"""

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


class PieceStatus(StrEnum):
    """セルフのピースの状態。プログラムが計算する（app/selfhood.py）。"""

    EMPTY = "空き"
    INFERRED = "推測"
    HYPOTHESIS = "仮説"
    LIKELY = "確からしい"
    CONFIRMED = "本人が確かめた"
    WAVERING = "揺れている"
    CHANGED = "変わった"


class EvidenceStance(StrEnum):
    """根拠の向き。"""

    SUPPORT = "支える"
    AGAINST = "反する"


class EvidenceWeightKind(StrEnum):
    """根拠の種類。重みはプログラムが決める（app/selfhood.py の Tuning）。"""

    CONFIRMED = "本人が確かめた"
    SELF_WORDS = "本人の言葉"
    DIALOGUE = "やり取り"
    OBSERVATION = "今の様子"
    TESTIMONY = "証言"
    INFERENCE = "推測"
    ADDED_REPLY = "足された受け答え"
    CORRECTION = "本人の訂正"


class HeartLevel(StrEnum):
    """心の目次の段。月の読みを、人とテーマの目次に固める。"""

    MONTH = "月"
    INDEX = "目次"


class InferenceKind(StrEnum):
    """推論の種類"""

    TWIN_READING = "ツインの読み"
    NEXT_QUESTION = "次の問い"
    THREAD_QUESTION = "スレッドの問い"
    TWIN_ANSWER = "ツインの答え（チャット・会議）"
    ESTIMATED_WILL = "推定意思"
    DRAFT = "原案"
    EVIDENCE_CHECK = "根拠チェック"
    UNCONFIRMED_MARK = "確かめを待つ印"
    ADVOCACY = "ツインのアドボカシー"
    # 専門職が伝えたことを項目に分けた読み、資料（PDF）の読み、本人の今を代弁する手紙（app/professional_records.py、app/advocacy_letter.py）
    REPORT_SPLIT = "伝えの分け"
    DOCUMENT_READ = "資料の読み"
    LETTER = "手紙"
    # 生活まとめプランの下書き（app/life_plan.py）
    LIFE_PLAN = "生活まとめプラン"
    # 渡された資料へのツインのレビュー（app/professional_records.py）
    DOCUMENT_REVIEW = "資料のレビュー"
    # 本人が送る手紙の下書きと校正（app/self_letters.py）
    SELF_LETTER = "本人の手紙"
    LETTER_PROOFREAD = "手紙の校正"


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
    HEALTH = "健康の相談"


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

    話した人と、誰についての記録かを分ける：speaker_user_id / speaker_is_twin（話した人）と
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
    """解釈。言明1つに、AI の読み（この言葉から分かる本人の考え）を1つ持つ。

    目的は、AIの読み取りを本人の言葉と分けて持つこと。読みは仮説として入る。
    解釈は app/splitter.py が仮説として入れる。本人が確かめる処理はまだない。
    """

    id: int
    statement_id: int
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

    unconfirmed_reconsolidations：本人に確かめられないまま固め直した回数。
    同じ見立てが確かめられないまま続いたら印を付けるための材料として持つ。印を出す処理はまだない。
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
    # 丸く出すアイコン。架空用の起動では、入口の札にも出る。
    photo_id: int | None = None
    removed_at: datetime | None = None
    # 大きく出すプロフィール写真。アイコンとは別の行で、ログインの前には読めない。
    profile_photo_id: int | None = None

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


class SelfhoodPiece(_Base):
    """セルフのピースの1つの版。直すたびに新しい行を足す。確からしさと状態はプログラムが計算する。"""

    id: int
    run: str
    piece_key: str
    section: str
    name: str
    about_user_id: int | None = None
    version: int
    hypotheses: list[str]
    status: PieceStatus
    confidence: float
    support: float
    against: float
    replay_date: str
    model: str | None = None
    created_at: datetime


class SelfhoodEvidence(_Base):
    """ピースの根拠の1行。"""

    id: int
    run: str
    piece_key: str
    stance: EvidenceStance
    kind: EvidenceWeightKind
    source_table: str
    source_id: int | None = None
    effective_date: str
    note: str | None = None
    created_at: datetime


class SelfhoodDocument(_Base):
    """セルフの全文や、確かめる問いの候補。版を足す。"""

    id: int
    run: str
    kind: str
    as_of: str
    body: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime


class DialogueReading(_Base):
    """本人のいる部屋のメッセージの読み。言明にせず、引かない（原則15）。"""

    id: int
    message_id: int
    room_id: int
    speaker_user_id: int | None = None
    effective_date: str
    reading: str
    petals: list[str]
    confidence: float
    caution: str | None = None
    is_added_reply: int
    model: str
    created_at: datetime


class HeartReading(_Base):
    """心の目次の1行。AI の読みで、本人の言葉ではない。言明から作り直せるので、作り直すときは新しい版を足す。

    この表は返事の材料にだけ使い、API では返さない。関連するテスト：tests/test_heart.py
    """

    id: int
    generation: int
    level: HeartLevel
    period: str | None = None
    topic: str
    about_user_id: int | None = None
    reading: str
    now_reading: str | None = None
    how_to_stay: str | None = None
    tender: str | None = None
    evidence_statement_ids: list[int]
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


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


class AdvocacyPlan(_Base):
    """ケアプラン案の1件。版の本文は推論の記録（inference_logs）にある。"""

    id: int
    asker_user_id: int
    client_key: str
    first_log_id: int
    objection_log_id: int | None = None
    second_log_id: int | None = None
    created_at: datetime


class AdvocacyPlanChoice(_Base):
    """どの版を使うかを人が選んだ記録。選び直しても前の行は残す。"""

    id: int
    plan_id: int
    chooser_user_id: int
    version: str
    created_at: datetime


class AdvocacySheet(_Base):
    """1枚の紙の1回。本文は推論の記録（inference_logs）にある。作り直しても前の行は残す。"""

    id: int
    asker_user_id: int
    log_id: int
    created_at: datetime


class AdvocacyVoice(_Base):
    """ツインのアドボカシーの1回。本文は推論の記録（inference_logs）にある。作り直しても前の行は残す。"""

    id: int
    turn_id: int
    log_id: int
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


_HEDGED_END = re.compile(r"(かもしれません|かも知れません)[。！!]?$")
# 読んだのは記録の一部なので、記録全体の有無を言い切る語は使わせない
_CLAIMS_ABOUT_RECORDS = ("記録にな", "記録はな", "記録にあり", "記録があり", "見当たら", "見当たり", "見つから", "見つかり", "ありません")


def _is_question(sentence: str) -> bool:
    return any(mark in sentence for mark in "？?") or bool(_HIDDEN_QUESTION.search(sentence.strip()))


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。！!？?])|\n", text) if part.strip()]


def _no_question_mark(text: str) -> str:
    """受け止めの文に問いを混ぜない。問いは1回に1つまでを、型で守るため。"""
    if any(_is_question(sentence) for sentence in _sentences(text)):
        raise ValueError("ここには問いを書かないでください")
    return text


def _drop_question_sentences(text: str) -> str:
    """問いの形の文だけを落とし、残りを受け止めにする。問いの文しかなければ断ってやり直す。

    AI は「落ち着くのですか。」のように書くことがあり、全体を断ると、同じ入力で8回中4回が型に合わず、
    やり直しでも続くと送信が失敗した（docs/tools.md）。
    関連するテスト：tests/test_twin.py の test_受け止めの問いの文だけを落とす
    """
    kept = [sentence for sentence in _sentences(text) if not _is_question(sentence)]
    if not kept:
        raise ValueError("ここには問いを書かないでください")
    return "".join(kept).strip()


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

    about_people、reading、negative、used_numbers を reply より先に置くのは、返事を書く前に「だれの話で、本人にとって何か」を
    書かせるため。読みは画面に出さない。about_people に亡くなった方が入れば、プログラムが問いを落とす（app/twin.py）。
    negative は否定的な発言かの読みで、真なら、プログラムが同意の言葉で始まる文を落とす（app/heart.py の check_reply）。
    used_numbers は心の目次の [番号] で、プログラムが渡した番号かを確かめる（app/heart.py）。
    """

    about_people: list[str] = []
    reading: str = ""
    negative: bool = False
    used_numbers: list[int] = []
    reply: str
    question: str | None = None
    question_theme: Theme | None = None
    care: CareKind = CareKind.NONE
    previous_question_result: QuestionResult | None = None

    @field_validator("reply")
    @classmethod
    def _check_reply(cls, value: str) -> str:
        return _twin_voice(value)

    @field_validator("question")
    @classmethod
    def _check_question(cls, value: str | None) -> str | None:
        """決まりに合わない問いは落とす。問いはなくても会話は続くので、送信を失敗させないため。

        問いが2つある、「？」で終わらない、「私は」を含む、のどれでも画面に出さない（原則12、AIの話し方）。
        関連するテスト：tests/test_twin.py の test_決まりに合わない問いは落として受け止めを返す
        """
        if value is None or not value.strip():
            return None
        try:
            return _one_question(value)
        except ValueError:
            return None

    @model_validator(mode="after")
    def _check_question_in_reply(self) -> "TwinTurn":
        """ふだんの話では問いの文だけを落とす。体や命の場面では、症状を聞く文が混ざった返事を使わない。

        体や命の場面で断ると、呼ぶ側が決まった文とつなぎ先を返す（app/twin.py の detect_care）。
        """
        if self.care is CareKind.NONE:
            self.reply = _drop_question_sentences(self.reply)
        else:
            _no_question_mark(self.reply)
        return self


class HeartItem(_Base):
    """1か月の読みの1項目。"""

    topic: str = Field(min_length=1, max_length=30)
    person: str | None = None
    reading: str = Field(min_length=1)
    how_to_stay: str = Field(min_length=1)
    evidence_numbers: list[int]


class DialogueDraft(_Base):
    """段1b の1件の読み。"""

    number: int
    reading: str
    petals: list[str] = []


class DialogueBatch(_Base):
    """段1b の結果。"""

    readings: list[DialogueDraft]


class SortMatch(_Base):
    """段2 の1行。番号はプログラムが確かめる。"""

    item: int
    piece: int
    stance: EvidenceStance


class ProposedPiece(_Base):
    """段2 が足したいピース。"""

    section: str
    name: str
    person: str | None = None


class SortResult(_Base):
    """段2 の結果。"""

    matches: list[SortMatch] = []
    new_pieces: list[ProposedPiece] = []


class RevisedPiece(_Base):
    """段3 と段5 の1ピース。"""

    number: int
    hypotheses: list[str]


class ReviseResult(_Base):
    """段3 の結果。"""

    pieces: list[RevisedPiece] = []
    changed_numbers: list[int] = []


class IlluminateResult(_Base):
    """段5 の結果。"""

    pieces: list[RevisedPiece] = []
    inferred_numbers: list[int] = []


class SelfhoodWriting(_Base):
    """段6 の結果。"""

    body: str = Field(min_length=1)


class SelfhoodQuestion(_Base):
    """段7 の1問。"""

    piece: int
    question: str
    reason: str


class SelfhoodQuestions(_Base):
    """段7 の結果。"""

    questions: list[SelfhoodQuestion] = []


class AnswerRead(_Base):
    """問い合わせの読む段の結果。"""

    question_kind: str = ""
    # 聞いた人のねらいの読み。聞いた人の思いなので、本人の思いとしては使わない（app/advocacy_voice.py の question_reading）
    asker_need: str = ""
    scene: str = ""
    about: str = ""
    piece_numbers: list[int] = []
    health: bool = False
    path: str = ""


class HeartMonth(_Base):
    """1か月分の読み。"""

    items: list[HeartItem]


class HeartEntry(_Base):
    """人かテーマの目次の1項目。"""

    topic: str = Field(min_length=1, max_length=30)
    story: str = Field(min_length=1)
    now: str = ""
    how_to_stay: str = Field(min_length=1)
    tender: str = ""
    key_numbers: list[int]


class HeartMerge(_Base):
    """目次に固めた結果。"""

    entries: list[HeartEntry]


class TwinPhotoTurn(TwinTurn):
    """写真を読んだときの返事。写真の説明は、あとの会話で写真を送り直さずに話を続けるために残す。"""

    photo_description: str = Field(min_length=1, max_length=120)


class ConversationSummary(_Base):
    """会話の要約。AI の読み取りで、本人の言葉として扱わない。"""

    summary: str = Field(min_length=1, max_length=300)


_JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠]")


class TwinClosing(_Base):
    """「今日はここまで」のときの返事と、今日の暮らしに役立つ一言。

    tip は、重い話で終わった日は空にする。天気や片づけの話で受け流さないため。
    理由は docs/decisions.md の「重い話で終わった日は今日の一言を出さない」にある。
    """

    reply: str
    tip: str

    @field_validator("reply")
    @classmethod
    def _check_reply(cls, value: str) -> str:
        return _no_question_mark(_twin_voice(value))

    @field_validator("tip")
    @classmethod
    def _check_tip(cls, value: str) -> str:
        # 日本語を含まない答えは一言として出さない。空にする場面で、AI が記号や英字の切れ端を書いた回があった（実測）
        return _no_question_mark(_twin_voice(value)) if _JAPANESE.search(value) else ""


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
    """解釈の案（この言葉から分かる本人の考え）。保存するときは必ず仮説になる。"""

    viewpoint_role: Role
    body: str = Field(min_length=1)
    confidence: Confidence
    caution: str | None = None


class SplitStatement(_Base):
    """言明の案。

    話した人と誰についてかは、入力に書かれた名前で受け取る。家族の言葉を本人の意思と取り違えないため。
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
    interpretation: SplitInterpretation | None = None
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
    """

    direct_numbers: list[int] = []
    related_numbers: list[int] = []
    request_kind: RequestKind = RequestKind.NORMAL


class WordPick(_Base):
    """言葉を選ぶ係の AI の出力。番号と短い案内だけで、本文を書く項目を持たない。

    AI の出力が本人の言葉の本文になる道がないことが、原則9を守る理由。番号はプログラムが、渡した候補にあるか、
    出どころが枠に合うかを確かめる。lead は本人の言葉として扱わず、通らなければ捨てる。
    """

    self_numbers: list[int] = []
    testimony_numbers: list[int] = []
    guess_numbers: list[int] = []
    # 選んだ言葉の中で、時期によって考えが変わっているか、本人の言葉と証言が食い違っているか。画面の決まった1文を出す印にだけ使う
    words_differ: bool = False
    lead: str | None = None


class ConfirmedPick(_Base):
    """確かめた言葉を足す係の AI の出力。番号だけで、本文を書く項目を持たない（原則9）。"""

    self_numbers: list[int] = []


class EstimateDraft(_Base):
    """推定意思の案。本人の言葉とは別の枠に出す（原則11）。根拠の番号はプログラムが確かめる。"""

    # 手がかりがなければ書かない。無理に書かせると、別の話題から推し量った推定になるため
    estimate: str | None = Field(default=None, max_length=200)
    evidence_numbers: list[int] = []
    counter_numbers: list[int] = []
    confidence: Confidence = 0.0
    how_to_confirm: str | None = Field(default=None, max_length=200)
    # 問いの事柄のテーマ。本人が託す範囲から外したテーマなら、プログラムが推定を捨てる
    question_theme: Theme | None = None

    @field_validator("estimate")
    @classmethod
    def _check_estimate(cls, value: str | None) -> str | None:
        """全部の文を推測の形にする。文を落とすと AI が書いていない文章ができるので、落とさずに断ってやり直す。

        AI は渡した材料しか読んでいないので、記録全体について「ない」とは言えない。
        関連するテスト：tests/test_advocacy.py の test_推定は全部の文がかもしれませんで終わらなければ出さない
        """
        if value is None or not value.strip():
            return None
        cleaned = _twin_voice(value)
        if any(word in cleaned for word in _CLAIMS_ABOUT_RECORDS):
            raise ValueError("記録全体について書かないでください")
        # 鉤括弧があると、推定の文の一部が本人の言葉に見える（原則11）
        if "「" in cleaned or "『" in cleaned:
            raise ValueError("鉤括弧を使わないでください")
        # 材料は何年も前の記録のことがある。AI の言う「最近」は確かめられない
        if "最近" in cleaned:
            raise ValueError("「最近」と書かないでください")
        if not all(_HEDGED_END.search(sentence.strip()) for sentence in _sentences(cleaned)):
            raise ValueError("どの文も「かもしれません」で終えてください")
        return cleaned


# 文の中の記録の番号。本物の AI の試しで「28と29で」「28を、」のように書いた。読む人には番号が見えない
_RECORD_NUMBER_IN_TEXT = re.compile(r"\[\d+\]|\d+番|\d+の(?:言葉|記録|発言)|\d+を、|\d+と\d+で")


def _advocate_voice(text: str) -> str:
    """ツインが本人として代弁する文を確かめる。止めるのは、引用に見える文と、記録の番号を書いた文だけ。

    一人称と言い切りは止めない（原則9）。本人の言葉の本文はプログラムが番号から入れるので、AI の文に鉤括弧があると、
    AI の書いた文が本人の言葉に見えるため。
    関連するテスト：tests/test_advocacy_voice.py
    """
    cleaned = text.strip()
    if not any(char.isalnum() for char in cleaned):
        raise ValueError("文が空です")
    if "「" in cleaned or "『" in cleaned:
        raise ValueError("鉤括弧を使わず、本人の言葉は番号で指してください")
    if _RECORD_NUMBER_IN_TEXT.search(cleaned):
        raise ValueError("文の中に記録の番号を書かないでください")
    return cleaned


class AdvocacyReason(_Base):
    """ツインの主張の理由。もとにした意思モデルの要素と、補強に引く記録を番号で指す。"""

    text: str = Field(max_length=500)
    element_numbers: list[int] = []
    evidence_numbers: list[int] = []

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _advocate_voice(value)


class AdvocacyObjection(_Base):
    """主張に反対する言葉の番号と、それへのツインの答え。"""

    number: int
    answer: str = Field(max_length=500)

    @field_validator("answer")
    @classmethod
    def _check_answer(cls, value: str) -> str:
        return _advocate_voice(value)


class PlanItem(_Base):
    """ケアプラン案の1項目。目標とサービスの内容。"""

    aim: str = Field(max_length=200)
    support: str = Field(max_length=300)

    @field_validator("aim", "support")
    @classmethod
    def _check_voice(cls, value: str) -> str:
        return _advocate_voice(value)


class GeneralPlanDraft(_Base):
    """本人の言葉を読まずに作る、一般的なケアプラン案の AI の出力。"""

    summary: str = Field(max_length=400)
    items: list[PlanItem] = []

    @field_validator("summary")
    @classmethod
    def _check_summary(cls, value: str) -> str:
        return _advocate_voice(value)


class PlanObjectionPoint(_Base):
    """案のどこに、なぜ異を唱えるか。根拠は渡した記録の番号で指す。"""

    target: str = Field(max_length=200)
    objection: str = Field(max_length=500)
    evidence_numbers: list[int] = []

    @field_validator("target", "objection")
    @classmethod
    def _check_voice(cls, value: str) -> str:
        return _advocate_voice(value)


class PlanObjectionDraft(_Base):
    """ケアプラン案へのツインの異議の AI の出力。本人の言葉の本文を書く項目を持たない。"""

    claim: str = Field(max_length=800)
    points: list[PlanObjectionPoint] = []
    requests: list[str] = []
    ask_back: str | None = Field(default=None, max_length=300)

    @field_validator("claim")
    @classmethod
    def _check_claim(cls, value: str) -> str:
        return _advocate_voice(value)

    @field_validator("requests")
    @classmethod
    def _check_requests(cls, value: list[str]) -> list[str]:
        return [_advocate_voice(item) for item in value]

    @field_validator("ask_back")
    @classmethod
    def _check_ask_back(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _advocate_voice(value)


class RevisedPlanItem(_Base):
    """書き直した案の1項目。なぜこの形にしたかと、根拠の番号を持つ。"""

    aim: str = Field(max_length=200)
    support: str = Field(max_length=300)
    why: str = Field(max_length=400)
    evidence_numbers: list[int] = []

    @field_validator("aim", "support", "why")
    @classmethod
    def _check_voice(cls, value: str) -> str:
        return _advocate_voice(value)


class RevisedPlanDraft(_Base):
    """異議を受けて書き直したケアプラン案の AI の出力。本人の言葉の本文を書く項目を持たない。"""

    summary: str = Field(max_length=400)
    self_intent: str = Field(max_length=400)
    long_term_goal: str = Field(max_length=400)
    items: list[RevisedPlanItem] = []

    @field_validator("summary", "self_intent", "long_term_goal")
    @classmethod
    def _check_voice(cls, value: str) -> str:
        return _advocate_voice(value)


class SheetTopic(_Base):
    """1枚の紙の話題ごとの代弁。根拠は渡した記録の番号で指す。"""

    title: str = Field(max_length=40)
    text: str = Field(max_length=500)
    evidence_numbers: list[int] = []

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _advocate_voice(value)


class SheetDraft(_Base):
    """1枚の紙の AI の出力。ツイン自身の文と、記録を指す番号だけを持つ。本人の言葉の本文を書く項目を持たない。"""

    claim: str = Field(max_length=800)
    topics: list[SheetTopic] = []
    change: AdvocacyReason | None = None
    self_intent: str = Field(max_length=400)
    long_term_goal: str = Field(max_length=400)
    plan_evidence_numbers: list[int] = []
    requests: list[str] = []
    ask_back: str | None = Field(default=None, max_length=300)

    @field_validator("claim", "self_intent", "long_term_goal")
    @classmethod
    def _check_voice(cls, value: str) -> str:
        return _advocate_voice(value)

    @field_validator("requests")
    @classmethod
    def _check_requests(cls, value: list[str]) -> list[str]:
        return [_advocate_voice(item) for item in value]

    @field_validator("ask_back")
    @classmethod
    def _check_ask_back(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _advocate_voice(value)


class AdvocacyDraft(_Base):
    """ツインのアドボカシーの AI の出力。ツイン自身の文と、記録を指す番号だけを持つ。

    本人の言葉の本文を書く項目を持たない。番号はプログラムが、渡した記録にあるかを確かめて本文に戻す。
    本人像と気持ちの読みを答えより先に置く。記録の読みから本人を組み立ててから答えさせるため。
    """

    selfhood: str = Field(default="", max_length=500)
    feeling: str = Field(default="", max_length=300)
    claim: str = Field(max_length=800)
    reasons: list[AdvocacyReason] = []
    objections: list[AdvocacyObjection] = []
    requests: list[str] = []
    # 判断に要るのに読めていないことを、相手に1つ聞く。なければ空
    ask_back: str | None = Field(default=None, max_length=300)

    @field_validator("claim")
    @classmethod
    def _check_claim(cls, value: str) -> str:
        return _advocate_voice(value)

    @field_validator("selfhood", "feeling")
    @classmethod
    def _check_reading(cls, value: str) -> str:
        return _advocate_voice(value) if value.strip() else ""

    @field_validator("requests")
    @classmethod
    def _check_requests(cls, value: list[str]) -> list[str]:
        return [_advocate_voice(item) for item in value]

    @field_validator("ask_back")
    @classmethod
    def _check_ask_back(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _advocate_voice(value)


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


# ---- 専門職が伝えること（チャットと資料）と、本人の今を代弁する手紙 ----
# 置き場の表は app/db.py、処理は app/professional_records.py と app/advocacy_letter.py にある。
# 設計：専門職が伝えたことは、確かめた項目だけを今の様子として台帳に入れる。資料を書いた人が話した人。


class ProfessionalItemKind(StrEnum):
    """専門職が伝えた1項目の種類。資料の中の「本人：〜」は、専門職の聞き取りで、本人の言葉ではない。"""

    OBSERVATION = "観察"
    SELF_HEARD = "本人の思いの聞き取り"
    FAMILY_HEARD = "家族の思いの聞き取り"


class ProfessionalItemStatus(StrEnum):
    """伝えた項目の状態。台帳に入るのは「残した」だけ。"""

    WAITING = "確かめ待ち"
    KEPT = "残した"
    DROPPED = "残さない"


class DocumentKind(StrEnum):
    """取り込んだ資料の種類"""

    CARE_PLAN = "ケアプラン"
    ASSESSMENT = "アセスメント"
    MEETING = "担当者会議の要点"
    PROGRESS = "支援経過"
    PROVIDER_REPORT = "事業所の報告"
    OTHER = "そのほか"


class DocumentStatus(StrEnum):
    """取り込んだ資料の状態。名前が本人と合わないときは、項目を出す前に止める。"""

    NAME_CHECK = "名前の確かめ待ち"
    WAITING = "確かめ待ち"
    DONE = "確かめ済み"
    WITHDRAWN = "取り下げ"


class LetterStatus(StrEnum):
    """手紙の状態。確かめていない手紙は専門職に届かない。"""

    CHECKING = "確かめ待ち"
    RETURNED = "返した"
    DECLINED = "出さない"


class MadeDocumentKind(StrEnum):
    """ツイン（AI）が作った書類の種類。削除の印を付けられる（app/made_documents.py）。"""

    LETTER = "手紙"
    SHEET = "1枚の紙"
    PLAN = "ケアプラン案"
    LIFE_PLAN = "生活まとめプラン"


class ReportItemDraft(_Base):
    """AI が分けた1項目。だれについての項目かは名前で受け取り、プログラムが人に直す。"""

    kind: ProfessionalItemKind
    text: str = Field(max_length=300)
    about: str = Field(default="", max_length=40)
    page: int | None = Field(default=None, ge=1, le=600)
    # 資料のどの場面（見出し、表の行）から読んだか。専門職が原文と照らせるようにする（資料のときだけ）
    section: str = Field(default="", max_length=40)
    # 原文の短い抜き出し（資料のときだけ）
    quote: str = Field(default="", max_length=120)


class ReportSplit(_Base):
    """チャットで伝えたことを項目に分けた AI の出力。"""

    items: list[ReportItemDraft] = []


class CarePlanNeed(_Base):
    """ケアプランの第2表の1行。"""

    need: str = Field(max_length=300)
    long_term_goal: str = Field(default="", max_length=300)
    short_term_goal: str = Field(default="", max_length=300)
    services: list[str] = []


class CarePlanContent(_Base):
    """ケアプランの第1表と第2表の中身。ケアプランのチェックに渡す。"""

    self_intent: str = Field(default="", max_length=600)
    family_intent: str = Field(default="", max_length=600)
    policy: str = Field(default="", max_length=600)
    needs: list[CarePlanNeed] = []


class DocumentRead(_Base):
    """資料（PDF）を読んだ AI の出力。医療の情報も書かれたとおりに項目にする（原則17）。あるかどうかの印は画面の案内に使う。"""

    kind: DocumentKind
    document_date: str = Field(default="", max_length=20)
    author: str = Field(default="", max_length=100)
    person_name: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=100)
    summary: str = Field(max_length=400)
    items: list[ReportItemDraft] = []
    care_plan: CarePlanContent | None = None
    has_medical: bool = False
    # ツイン（AI）や AI が作った・下書きした資料か。AI の文を記録に戻さないため、項目にしない（原則3）
    made_by_ai: bool = False


class AdvocacyLetterDraft(_Base):
    """手紙の AI の出力。本人の今の暮らしと今の気持ちを、本人として書く。

    本人の言葉の本文を書く項目を持たない。引くときは番号で指し、本文はプログラムが記録から入れる。
    挿絵とピースも番号で指す。
    """

    body: str = Field(max_length=1200)
    requests: list[str] = Field(default=[], max_length=3)
    quote_number: int | None = None
    piece_numbers: list[int] = []
    illustration_number: int | None = None

    @field_validator("body")
    @classmethod
    def _check_body(cls, value: str) -> str:
        return _advocate_voice(value)

    @field_validator("requests")
    @classmethod
    def _check_requests(cls, value: list[str]) -> list[str]:
        return [_advocate_voice(item) for item in value if item.strip()]


class LifePlanRow(_Base):
    """生活まとめプランの1行。場面（食事は朝食などの分けも）ごとに5つの列を書く。書くことがない列は空にする。

    今まで、出来ること、出来ないこと、誰がは事実を短く。支援して欲しいことは本人の願いとして書く。
    鉤括弧は使わない（本人の言葉に見えるため。_advocate_voice と同じ決まり）。
    """

    area: str = Field(max_length=40)
    part: str | None = Field(default=None, max_length=10)
    past: str = Field(default="", max_length=120)
    can: str = Field(default="", max_length=120)
    cannot: str = Field(default="", max_length=120)
    support: str = Field(default="", max_length=120)
    who: str = Field(default="", max_length=60)

    @field_validator("past", "can", "cannot", "support", "who")
    @classmethod
    def _check_cell(cls, value: str) -> str:
        cleaned = value.strip()
        return _advocate_voice(cleaned) if cleaned else ""


class LifePlanDraft(_Base):
    """生活まとめプランの AI の出力。行は場面の名前で指し、プログラムが紙の行に当てはめる（forms/life_plan.json）。"""

    rows: list[LifePlanRow] = Field(max_length=30)
    piece_numbers: list[int] = []


class CareFormEntry(_Base):
    """書式の1つの欄に読み取った値。key は「まとまり／欄」の形。選ぶ欄は value に選択肢の文、いくつでも選ぶ欄は choices に入れる。"""

    key: str = Field(max_length=120)
    value: str = Field(default="", max_length=2000)
    choices: list[str] = Field(default=[], max_length=30)
    detail: str = Field(default="", max_length=2000)


class CareFormRow(_Base):
    """同じまとまりを何件も書く欄（医療機関、緊急連絡先など）の1つの値。index は0から。"""

    group: str = Field(max_length=60)
    index: int = Field(ge=0, le=9)
    name: str = Field(max_length=60)
    value: str = Field(default="", max_length=2000)


class CareFormRead(_Base):
    """資料（PDF）を書式の欄に読み取った AI の出力。書式に合うかはプログラムが確かめ、合わない値は捨てる（app/care_form_records.py）。"""

    record_date: str | None = Field(default=None, max_length=10)
    person_name: str = Field(default="", max_length=60)
    entries: list[CareFormEntry] = Field(default=[], max_length=200)
    rows: list[CareFormRow] = Field(default=[], max_length=100)


class ReviewConcern(_Base):
    """レビューの気になるところ。where は資料のどこか（ページと場面）。"""

    text: str = Field(max_length=200)
    where: str = Field(default="", max_length=40)


class DocumentReview(_Base):
    """渡された資料へのツインのレビュー。本人のセルフと照らして、文章で返す（app/professional_records.py）。"""

    overview: str = Field(max_length=300)
    fits: list[str] = Field(default=[], max_length=4)
    concerns: list[ReviewConcern] = Field(default=[], max_length=5)
    asks: list[str] = Field(default=[], max_length=3)


class SelfLetterDraft(_Base):
    """本人が送る手紙の下書き（app/self_letters.py）。ツインが、その期間の会話をもとに本人の人柄が伝わる標準語で書く。

    宛名、本文、結び、署名を分けて返す。画面ではそれぞれを直せる。挿絵は番号で指す。
    """

    salutation: str = Field(max_length=40)
    body: str = Field(max_length=1600)
    closing: str = Field(default="", max_length=200)
    signature: str = Field(default="", max_length=40)
    illustration_number: int | None = None


class SelfLetterProofread(_Base):
    """手紙の校正の結果。直したあとの4つの部分と、直したところの短い説明（notes）。"""

    salutation: str = Field(max_length=40)
    body: str = Field(max_length=1600)
    closing: str = Field(default="", max_length=200)
    signature: str = Field(default="", max_length=40)
    notes: list[str] = Field(default=[], max_length=6)


class LetterArt(_Base):
    """手紙の挿絵（app/self_letters.py）。ツインが手紙の文に合う絵を SVG で描く。

    SVG は、そのまま画面に出さない。使ってよい要素と属性だけを残してから出す（app/svg_art.py）。
    """

    title: str = Field(max_length=40)
    svg: str = Field(max_length=40000)
