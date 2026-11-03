"""10年分の架空ログの予定（物語）を決める。

役割：2026年から2036年までの出来事と、約400回の会話の日付、テーマ、話題を決める。AI は呼ばない。
4つの極：3. 読み解き、積み上げる（10年分の材料を作る）。
守る原則：8（人物と記録はすべて架空。実在の人に似せない）。

乱数を使わないので、同じ予定が毎回できる。物語を差し替えるときは、ここを直して作り直す。
「決めた言葉」は、本人がそのまま言うものとして会話に入れ、デモと確かめの手がかりにする。

物語に入れないもの：
- 泊まりを使うこと（ショートステイ）。記録がない問いに「まだ聞けていません」と答えることを確かめるため。
- 病名と診断。健康の判断をしないため。

作り方の説明：data/README.md
関連するテスト：tests/test_fiction.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.schemas import LifeStage, Theme

# デモでその場で話す日。ログには入れない。
DEMO_DAY = date(2026, 11, 3)
# 「10年後へ進む」の先の日付
FUTURE_DAY = date(2036, 5, 1)
FIRST_DAY = date(2026, 4, 12)
LAST_LOG_DAY = date(2036, 4, 26)

# どの話にも入れない語。記録がない話題と、健康の判断の話を混ぜないため。
FORBIDDEN_WORDS = ("泊まり", "ショートステイ", "ショート", "認知症", "アルツハイマー", "診断", "病名")

# 語の一覧。作った会話を確かめる。
_THEMES = [theme for theme in Theme if theme is not Theme.HARDSHIP]


@dataclass(frozen=True)
class Era:
    """1年の暮らしの様子。"""

    year: int
    stage: LifeStage
    count: int
    age: int
    setting: str
    # この年によく出る話題。テーマを問わず順に使う。
    topics: tuple[str, ...]


ERAS = [
    Era(
        2026,
        LifeStage.ACTIVE,
        40,
        74,
        "元気に暮らしている。製材所を退職して十年あまり。朝は縁側で和子さんとお茶を飲み、庭の手入れをする。将棋の会に通う。",
        ("庭の手入れ", "製材所の思い出", "朝のお茶", "将棋の会", "孫の健太", "町内の掃除", "夕方の散歩", "古い道具の手入れ"),
    ),
    Era(
        2027,
        LifeStage.ACTIVE,
        45,
        75,
        "元気に暮らしている。町内会の役を引き受けている。和子さんと近くへ旅行に出る。",
        ("町内会の役", "和子さんとの旅行", "木工の棚づくり", "将棋の会", "孫の健太", "畑の野菜", "昔の同僚", "季節の花"),
    ),
    Era(
        2028,
        LifeStage.ACTIVE,
        46,
        76,
        "元気だが、高い枝の剪定は人に頼むようになった。正雄さんとの将棋が楽しみ。",
        ("剪定を頼むこと", "正雄さんとの将棋", "町内会の役", "和子さんの料理", "美穂の仕事", "孫の健太の進学", "古い家の修理"),
    ),
    Era(
        2029,
        LifeStage.ACTIVE,
        47,
        77,
        "元気に暮らしている。春につつじ公園へ花見に行く。車の運転はまだ続けている。",
        ("つつじ公園の花見", "車の運転", "正雄さんとの将棋", "節子さんとの花の交換", "和子さんとの朝のお茶", "家の片づけ"),
    ),
    Era(
        2030,
        LifeStage.ACTIVE,
        48,
        78,
        "町内会の役を降りた。少し疲れやすくなったが、庭は続けている。",
        ("町内会の役を降りたこと", "庭を小さくすること", "運転の距離", "和子さんの腰", "美穂の帰省", "製材所の思い出"),
    ),
    Era(
        2031,
        LifeStage.SUPPORT,
        40,
        79,
        "物忘れが少し増え、家族の勧めで介護の相談を始めた。運転免許を自分で返した。",
        ("免許を返したこと", "物の置き場所", "手帳に書くこと", "和子さんとの買い物", "美穂の電話", "庭の水やり"),
    ),
    Era(
        2032,
        LifeStage.SUPPORT,
        35,
        80,
        "週に二回デイサービスへ通い始めた。庭に出る時間を大事にしている。",
        ("デイサービスの様子", "庭に出る時間", "和子さんに頼むこと", "正雄さんの訪問", "節子さんの差し入れ", "手伝ってもらうこと"),
    ),
    Era(
        2033,
        LifeStage.SUPPORT,
        30,
        81,
        "歩くのがゆっくりになった。和子さんの負担を気にかけるようになった。",
        ("和子さんの負担", "杖を使うこと", "美穂の手伝い", "縁側で過ごす時間", "デイサービスの友だち", "昔の写真"),
    ),
    Era(
        2034,
        LifeStage.CARE,
        25,
        82,
        "介護を受ける暮らしになった。言葉は短くなったが、庭の話はよくする。",
        ("窓から見る庭", "和子さんのお茶", "美穂の訪問", "ヘルパーさんのこと", "昔の木の話"),
    ),
    Era(
        2035,
        LifeStage.CARE,
        20,
        83,
        "ゆっくり暮らしている。話す量は減ったが、好きなことははっきり言う。",
        ("窓から見る庭", "紫陽花の色", "和子さんの声", "将棋盤を見ること", "美穂の顔"),
    ),
    Era(
        2036,
        LifeStage.CARE,
        11,
        84,
        "短い言葉で話す。朝の光と庭を好む。",
        ("朝の光", "庭の花", "和子さんのお茶", "美穂の話"),
    ),
]


@dataclass(frozen=True)
class FixedWord:
    """決めた日に、本人がそのまま言う言葉。"""

    day: date
    theme: Theme
    words: str
    # 話の場面。AI に渡す
    scene: str


# 確かめの手がかりになる本人の言葉。願い、暮らし方、考え方、家族、大切にしてきたこと、変化。
FIXED_WORDS = [
    FixedWord(date(2026, 4, 12), Theme.LIKES, "毎朝この庭を見るのが楽しみなんです", "縁側の朝"),
    FixedWord(date(2026, 5, 10), Theme.WORK, "最初の職場は製材所だった", "若いころの仕事"),
    FixedWord(date(2026, 6, 14), Theme.HOME, "朝は縁側でお茶を飲んで、それから庭を一回りする。", "毎日の暮らし方"),
    FixedWord(date(2026, 7, 19), Theme.VALUES, "仕事は丁寧にやるものだ。木は嘘をつかないからな。", "仕事で大切にしてきたこと"),
    FixedWord(date(2026, 8, 23), Theme.HELP, "人に頼むのは下手なんだ。頼まれるほうが性に合っている。", "人の手を借りること"),
    FixedWord(date(2026, 9, 27), Theme.FAMILY, "美穂には自分の暮らしを大事にしてほしい。", "離れて暮らす娘のこと"),
    FixedWord(date(2026, 10, 18), Theme.HOME, "できれば最後まで、この家で庭を見ていたい。", "これからの暮らしの願い"),
    FixedWord(date(2027, 3, 21), Theme.HOME, "施設に入るなんて考えたくもない。", "知人が施設に入った話を聞いて"),
    FixedWord(date(2028, 9, 10), Theme.FAMILY, "和子には苦労をかけた。あれがいたから続けられた。", "妻への思い"),
    FixedWord(date(2029, 4, 7), Theme.LIKES, "つつじ公園の桜は、毎年和子と見に行くんだ。", "春の花見"),
    FixedWord(date(2029, 10, 14), Theme.VALUES, "車の運転はまだ続けたい。自分で動けるのがいちばんだ。", "運転について"),
    FixedWord(date(2031, 5, 18), Theme.VALUES, "免許は返した。言われる前に自分で決めたかった。", "運転をやめたこと"),
    FixedWord(date(2032, 4, 11), Theme.HELP, "デイサービスは週に二回までならいい。庭の手入れができなくなるのは困る。", "デイサービスに通い始めて"),
    FixedWord(date(2033, 6, 12), Theme.HOME, "和子がきつくなったら、施設のことも考えていい。", "妻の負担を気にして"),
    FixedWord(date(2034, 5, 20), Theme.LIKES, "窓から庭が見えれば、それでいい。", "介護を受ける暮らしの中で"),
    FixedWord(date(2035, 6, 16), Theme.LIKES, "紫陽花は、青いのがいい。", "今年の紫陽花"),
    FixedWord(date(2036, 4, 5), Theme.LIKES, "つつじ公園の桜を、見に行った。", "春の花見に連れて行ってもらって"),
]

# 「本人だけ」のままにする言葉。田中さんに本文も件数も出ないことを確かめる。
PRIVATE_WORDS = [
    FixedWord(
        date(2030, 3, 3),
        Theme.HARDSHIP,
        "兄とは最後まで仲直りできなかった。このことは誰にも言わないでほしい。",
        "本人が自分から話した、兄のこと",
    ),
]


@dataclass(frozen=True)
class FixedComment:
    """便りへのコメント。家族と縁のある人の証言になる。"""

    day: date
    speaker: str
    words: str


FIXED_COMMENTS = [
    FixedComment(date(2026, 9, 27), "美穂さん", "夏休みに、父と縁側でスイカを食べました。"),
    FixedComment(date(2028, 9, 10), "和子さん", "朝のお茶のときだけは、よく話してくれます。"),
    FixedComment(date(2029, 4, 7), "節子さん", "誠一さんの庭の紫陽花を、毎年分けてもらっています。"),
    FixedComment(date(2029, 10, 14), "正雄さん", "将棋では絶対に待ったをしない人です。"),
    FixedComment(date(2032, 4, 11), "美穂さん", "父は製材所の話になると目が輝きます。"),
    FixedComment(date(2033, 6, 12), "和子さん", "私のことを気にして、頼みごとを我慢しているようです。"),
    # 本人の言葉（2033年）と食い違う証言。チャットと推定の枠で、出どころを分けて出す
    FixedComment(date(2034, 5, 20), "美穂さん", "父は施設だけは嫌だと言っていました。"),
    FixedComment(date(2035, 6, 16), "美穂さん", "紫陽花の季節は、窓の近くに椅子を動かすと喜びます。"),
]


@dataclass(frozen=True)
class FixedObservation:
    """今の様子（観察）。家族か専門職が見て書く。"""

    day: date
    speaker: str
    words: str


FIXED_OBSERVATIONS = [
    FixedObservation(date(2036, 3, 22), "美穂さん", "朝は窓の近くで庭を眺めて、静かに過ごしています。"),
    FixedObservation(date(2036, 4, 19), "和子さん", "お茶を出すと、ゆっくり飲み干してうなずきます。"),
    FixedObservation(date(2036, 4, 26), "美穂さん", "歩くときは手を添えると安心した顔になります。"),
    # 田中さんが、これからの暮らしについて聞く理由になる
    FixedObservation(date(2036, 4, 26), "美穂さん", "母は夜に何度も起きるので、朝は疲れた顔をしています。"),
]

# 2026年10月の話から作るAIの推測。デモの訂正の場面で使う。
DEMO_GUESS = "庭が見える場所での暮らしを重視されているのかもしれません。"
DEMO_GUESS_DAY = date(2026, 10, 18)


@dataclass(frozen=True)
class PlanItem:
    """1回の会話の予定。"""

    key: str
    day: date
    stage: LifeStage
    age: int
    theme: Theme
    topic: str
    setting: str
    must_say: str | None = None
    scene: str | None = None
    private: bool = False
    commenters: tuple[str, ...] = ()
    must_comments: tuple[tuple[str, str], ...] = field(default=())
    # 写真は実物を使わず、説明文だけの記録にする
    photo_note: bool = False


_FIXED_BY_DAY = {item.day: item for item in [*FIXED_WORDS, *PRIVATE_WORDS]}
_COMMENTS_BY_DAY: dict[date, list[FixedComment]] = {}
for _comment in FIXED_COMMENTS:
    _COMMENTS_BY_DAY.setdefault(_comment.day, []).append(_comment)

_COMMENTER_ROTATION = (("節子さん",), ("美穂さん",), ("和子さん",), ("正雄さん",), ("美穂さん", "節子さん"))


def _era_days(era: Era) -> list[date]:
    """1年の会話の日を、等しい間隔で決める。決めた日とデモの日は重ならないよう動かす。"""
    start = FIRST_DAY if era.year == 2026 else date(era.year, 1, 5)
    end = LAST_LOG_DAY if era.year == 2036 else date(era.year, 12, 26)
    fixed = sorted(day for day in _FIXED_BY_DAY if day.year == era.year)
    others = era.count - len(fixed)
    span = (end - start).days
    taken = set(fixed)
    for step in range(others):
        day = start + timedelta(days=round(step * span / max(others - 1, 1)))
        while day in taken or day == DEMO_DAY:
            day += timedelta(days=1)
        taken.add(day)
    return sorted(taken)


def build_plan() -> list[PlanItem]:
    """すべての会話の予定を、日付の順に作る。"""
    items: list[PlanItem] = []
    for era in ERAS:
        rotation = 0
        for day in _era_days(era):
            fixed = _FIXED_BY_DAY.get(day)
            topic = era.topics[rotation % len(era.topics)]
            theme = fixed.theme if fixed else _THEMES[rotation % len(_THEMES)]
            must_comments = tuple((c.speaker, c.words) for c in _COMMENTS_BY_DAY.get(day, []))
            commenters = tuple(speaker for speaker, _ in must_comments)
            # 本人だけの話には便りを付けない
            is_private = fixed in PRIVATE_WORDS if fixed else False
            if not commenters and not is_private and rotation % 7 == 6:
                commenters = _COMMENTER_ROTATION[(rotation // 7) % len(_COMMENTER_ROTATION)]
            items.append(
                PlanItem(
                    key=f"{day.isoformat()}",
                    day=day,
                    stage=era.stage,
                    age=era.age,
                    theme=theme,
                    topic=fixed.scene if fixed else topic,
                    setting=era.setting,
                    must_say=fixed.words if fixed else None,
                    scene=fixed.scene if fixed else None,
                    private=is_private,
                    commenters=commenters,
                    must_comments=must_comments,
                    photo_note=rotation % 5 == 2 and not fixed,
                )
            )
            rotation += 1
    return items


def plan_for_years(first: int, last: int) -> list[PlanItem]:
    return [item for item in build_plan() if first <= item.day.year <= last]
