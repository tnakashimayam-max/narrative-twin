"""設定を1か所にまとめる。

作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
役割：ファイルの置き場所、API キーの読み込み、役割ごとのモデル名を持つ。
4つの極：特定の極に属さない土台。
守る原則：特定の原則には関わらない。

モデルを選んだ理由と選ばなかった案の全体の表は docs/tools.md にある。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "narrative_twin.db"
PHOTOS_DIR = DATA_DIR / "photos"
# デモの顔の写真の置き場（fiction/prepare_photos.py の出力）。Git に入らず、架空用の起動だけが読む。
# 起動の保存先には含めない。流し込みの別のデータベースでも、同じ顔を使うため（fiction/faces.py）。
DEMO_FACES_DIR = DATA_DIR / "demo_photos" / "profile"
# 開発のログの置き場。data/ の外に置くのは、自分用の記録を data/mine/ の中だけに保つため（app/devlog.py）。
LOG_DIR = ROOT / "logs"
STATIC_DIR = ROOT / "static"
EVAL_CASES_PATH = ROOT / "eval" / "cases.json"

LABEL_FICTIONAL = "架空データ"
LABEL_MINE = "自分の記録で試用中"
LABEL_FRESH = "新規のデータ"
# 起動の種類。画面が「ログイン画面」のボタンを出すかを決める
KIND_DEMO = "demo"
KIND_FRESH = "fresh"
KIND_MINE = "mine"
# 入口の「新規で始める」で作るデータの置き場。Git に入らない
FRESH_FOLDER = "fresh"
FRESH_LOG = "fresh.jsonl"

# 架空用の起動で、専門職が現れてよい日付。10年後の場面を日付で切り替えるため（app/auth.py）。
PROFESSIONAL_FROM = date(2036, 1, 1)


@dataclass(frozen=True)
class Deployment:
    """起動の種類ごとの保存先と表示。

    架空用と自分用で保存先を丸ごと分ける。開発者が自分の本当の言葉と写真で試すとき、
    公開するものに混ざらないようにするため。data/mine/ は Git に入らない（.gitignore）。
    ポートを分けるのは、ブラウザを操作する MCP が開ける先を架空用だけに限るため（.mcp.json）。
    """

    mine: bool
    port: int
    db_path: Path
    photos_dir: Path
    label: str
    log_path: Path
    fresh_id: str | None = None

    @property
    def kind(self) -> str:
        """起動の種類を返す。"""
        if self.fresh_id:
            return KIND_FRESH
        return KIND_MINE if self.mine else KIND_DEMO


def deployment(*, mine: bool = False, data_dir: Path | None = None) -> Deployment:
    """起動の種類を決める。data_dir を渡せるのは、テストで本物の data/ に触れないため。"""
    base = data_dir if data_dir is not None else DATA_DIR
    if mine:
        folder = base / "mine"
        return Deployment(
            True, 8001, folder / "narrative_twin.db", folder / "photos", LABEL_MINE, LOG_DIR / "mine.jsonl"
        )
    return Deployment(
        False, 8000, base / "narrative_twin.db", base / "photos", LABEL_FICTIONAL, LOG_DIR / "fictional.jsonl"
    )


def fresh_deployment(top: Deployment, fresh_id: str) -> Deployment:
    """入口の「新規で始める」で作るデータの保存先と表示を決める。

    動きの決まりは自分用の起動と同じにする（mine=True）。架空の人を入れず、日付を動かさず、ログに本文を書かず、
    入口に顔を出さない。新規には実在の人の話が入りうるため。自分用の保存先（data/mine/）とログは使わない。
    関連するテスト：tests/test_fresh.py
    """
    folder = top.db_path.parent / FRESH_FOLDER / fresh_id
    return Deployment(
        True, top.port, folder / "narrative_twin.db", folder / "photos", LABEL_FRESH, top.log_path.with_name(FRESH_LOG), fresh_id
    )


API_KEY_NAME = "ANTHROPIC_API_KEY"
# セルフの再生の費用の上限。tyumajinさんの決定で全工程 $100。余裕を残して止める
SELFHOOD_BUDGET_USD = 90.0
SELFHOOD_MAX_PIECES = 180

# アプリの中の Claude の役割ごとに使うモデル。モデル名を変えるときは、ここだけを直す。
# 全体の表（選ばなかった案と実測を含む）は docs/tools.md にある。
_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"
_HAIKU = "claude-haiku-5-5"

# プログラムの判断で選ぶ、ツインの会話のもう1つの役割の名前（app/twin_model.py）。
TWIN_DEEP_ROLE = "ツインの深い会話"
# プログラムの判断で選ぶ、言明への分割のもう1つの役割の名前（app/twin_model.py）。
SPLIT_DEEP_ROLE = "重い言葉の分割"
# 重い言葉の分割で本人の発言が落ちたときの、やり直しの役割の名前（app/intake.py）。
SPLIT_DEEP_RETRY_ROLE = "重い言葉の分割のやり直し"

MODEL_BY_ROLE: dict[str, str] = {
    # 接続確認：いちばん多く使うモデルまで届くかを確かめるのが目的なので、Haiku で確かめる。
    "接続確認": _HAIKU,
    # 写真の問い：写真を読むのはここだけ。写真に本当に写っているものに触れてほしいので Sonnet。
    "写真の問い": _SONNET,
    # 言明への分割：会話を閉じるたびに裏で動き、回数が多い。本人の言葉の一字一句はプログラムが
    # 確かめる。Sonnet との比べは docs/tools.md にある。スレッド、グループ、短いふつうの話はこの役割で分ける。
    "言明への分割": _HAIKU,
    # 重い言葉の分割：発言の中身をプログラムが判断し、必要なときだけモデルを Sonnet 5.5 へ選択する。
    # 本人の言葉は作り直せない。比べで Haiku は発言を1つ落とし、言い切りの文を作った。費用は約12倍なので、
    # 本人が長く話した回と、人生、体、気持ちの語がある回だけにする。判断は app/twin_model.py。
    SPLIT_DEEP_ROLE: _SONNET,
    # やり直しも同じモデルにして、深さだけを上げる。下の EFFORT_BY_ROLE に理由がある。
    SPLIT_DEEP_RETRY_ROLE: _SONNET,
    # 架空ログの作成：決まった筋書きの文を大量に作る。判断の重さがないので Haiku。
    "架空ログの作成": _HAIKU,
    # 架空の受け答えとその確かめ：画面に映る本人の短い文で、方言の加減と、事実を足さない判断の差が出る。
    # 回数は全体の中でわずかなので Sonnet にする（fiction/added_words.py）。
    "架空の受け答え": _SONNET,
    "架空の受け答えの確かめ": _SONNET,
    # ツインの会話：本人が返事を待つので、ふだんの話はいちばん速いモデルにする。毎日使っても費用が小さい。
    "ツインの会話": _HAIKU,
    # ツインの深い会話：発言の中身と Haiku の答えをプログラムが判断し、必要なときだけモデルを Sonnet へ選択する。
    # 心の目次があるときは、すべての返事をこのモデルで書く。Haiku は同じ目次を渡しても言い直しに戻った（docs/tools.md）。
    # 相談、体の話、人生に関わる話、気持ちの話は、言葉の加減の差が使い続けるかに響く。判断は app/twin_model.py。
    TWIN_DEEP_ROLE: _SONNET,
    # 心の読み：本人の言葉を月ごとに読み直し、その人や事柄が本人にとって何かを読む。言葉の下を読む力が要り、
    # 件数は月の数だけなのでバッチの Sonnet にする。比べと理由は docs/tools.md にある。
    "心の読み": _SONNET,
    # やり取りを読む：本人のいる部屋の会話を1件ずつ読む。件数が多いので Haiku にする。重い話は下の役割へ。
    "やり取りを読む": _HAIKU,
    # 重いやり取りを読む：人生、気持ち、体の語がある回。言葉の奥を読む差が大きいので Sonnet にする。
    "重いやり取りを読む": _SONNET,
    # ピースに仕分ける：どのピースに当たるかを番号で返すだけで、文を書かない。回数が多いので Haiku にする。
    "ピースに仕分ける": _HAIKU,
    # 仮説を直す：ピースの文を書く。物語の文の質が要るので Sonnet にする。
    "仮説を直す": _SONNET,
    # 全体を照らす、セルフを書く：盤面全体を読む。誤るとすべての代弁に響くので Opus にする。回数は年に1回。
    "全体を照らす": _OPUS,
    "セルフを書く": _OPUS,
    # 確かめる問いを作る：回数は少なく、聞く価値の判断が要る。
    "確かめる問いを作る": _SONNET,
    # 答え合わせの判定、問い作り、基本の答え：開発の確かめにだけ使う（eval/selfhood_check.py）。基本の答えは、仕組みの差だけを
    # 比べられるよう、代弁と同じ Opus にする。
    "答え合わせの判定": _SONNET,
    "答え合わせの問い作り": _OPUS,
    "基本の答え": _OPUS,
    # 問いを読む：盤面の見出しをたどって番号を返すだけ。本人の代弁を待たせないので Haiku にする。
    "問いを読む": _HAIKU,
    # 心の目次：月の読みを人とテーマごとに固める。回数は少なく、誤ると毎回の返事に響くので、固める処理と同じ Opus にする。
    "心の目次": _OPUS,
    # スレッドの問い：見えている便りとコメントだけを読み、問いを1つ書く。入力が小さいので Haiku。
    "スレッドの問い": _HAIKU,
    # グループのツイン：部屋に見えている発言だけを読み、問いか短い答えを書く。入力が小さいので Haiku。
    "グループのツイン": _HAIKU,
    # 会話の要約：閉じた会話を短くまとめ、次の会話で思い出すために使う。本人の言葉としては扱わないので Haiku。
    "会話の要約": _HAIKU,
    # 便りの下書き：本人の発言を短くまとめるだけで、本人が直してから共有するので Haiku。
    "便りの下書き": _HAIKU,
    # 人の候補：本人の言葉から名前と続柄を抜き出すだけで、決めるのは本人なので Haiku。
    "人の候補": _HAIKU,
    # 固める処理（月）：回数が多く、言明の抜き出しに近い作業なので Sonnet。バッチで半額にする。
    "固める処理（月）": _SONNET,
    # ツインの答え：専門職の問いに合う要素の番号を選ぶだけで、本文は読まない。専門職が待つので Opus は使わない。
    "ツインの答え": _SONNET,
    # 言葉を選ぶ：要素の根拠の本文を読み、問いに合う言葉の番号を選ぶ。番号の誤りが届く言葉の違いになるので Haiku にしない。
    "言葉を選ぶ": _SONNET,
    # 確かめた言葉を足す：言葉を選ぶ係と同じく、番号の誤りが届く言葉の誤りになる。同じ深さで読ませるため Sonnet 5.5
    "確かめた言葉を足す": _SONNET,
    # 固める処理（年と人生の段階）：回数は少ないが、誤ると意思モデル全体に残る。Opus にして、バッチで半額にする。
    "固める処理": _OPUS,
    # 推定意思：本人の言葉がない場面で、ケアの判断の材料になる。出すときだけ呼ぶので Opus の費用で足りる。
    "推定意思": _OPUS,
    # ツインのアドボカシー：10年の意思モデルを順に読み、結論までの筋道をツイン自身の言葉で書く。
    # このアプリの深さがいちばん出る処理で、呼ぶのは人が求めたときだけなので Opus の費用で足りる。
    "ツインのアドボカシー": _OPUS,
    # 原案：ケアプランの第1版と第2版を書く。第2版は根拠のつながりの質が要るので Opus。比べはまだしていない。
    "原案": _OPUS,
    # 根拠チェック：案に、本人の言葉と意思モデルを根拠に異を唱える。原案と同じ強さにする。
    "根拠チェック": _OPUS,
    # 比べの正解付け：eval/previous_story/compare_rag.py でだけ使う。本人の言葉を全部読んで正解を付ける。
    # 誤ると比べ全体がゆがむので Opus にし、全部の言葉をキャッシュに載せて費用を抑える。
    "比べの正解付け": _OPUS,
    # 比べの正解の確かめ：正解付けの揺れを見るために、別のモデルで一部を付け直す。
    "比べの正解の確かめ": _SONNET,
    # 比べの判定：届いた言葉を腕の名前を隠して読む。言葉を選ぶ係（Sonnet）と別のモデルにする。
    "比べの判定": _OPUS,
    # 伝えを分ける：専門職がチャットで伝えた文を、観察と聞き取りの項目に分ける。文を作らず分けるだけなので Haiku。
    "伝えを分ける": _HAIKU,
    # 資料を読む：ケアプランやアセスメントの PDF を読む。スキャンした紙はページを画像として読むので、
    # 写真の問いと同じく Sonnet にする。Haiku で PDF を十分に読めるかは確かめていない（app/professional_records.py）。
    "資料を読む": _SONNET,
    # 手紙：本人の今の暮らしと今の気持ちを、本人の人柄が伝わる標準語で書く。紙になって人の手に渡るので、代弁と同じ Opus。
    "手紙": _OPUS,
    # 生活まとめプラン：15の場面の今までと今を、10年の記録から書き分ける。専門職が直して紙で出すので、手紙と同じ Opus。
    "生活まとめプラン": _OPUS,
    # 書式を読む：アセスメントなどの PDF を、書式の欄に当てはめて読む。資料を読む係と同じ Sonnet。
    "書式を読む": _SONNET,
    # 資料のレビュー：渡された資料を本人のセルフと照らし、ツインの考えを文章で返す。代弁と同じ Opus。
    "資料のレビュー": _OPUS,
    # 本人の手紙：本人が送る手紙を、その期間の会話から本人の人柄が伝わる標準語で書く。人の手に渡る文なので、手紙と同じ Opus。
    "本人の手紙": _OPUS,
    # 手紙の校正：本人が直した手紙の言葉を整える。出来事も文も消さず、整えるだけなので Sonnet（app/self_letters.py）。
    "手紙の校正": _SONNET,
    # 手紙の挿絵：手紙の文に合う絵を、これまでの水彩の挿絵を見本にして SVG で描く。ご本人の指定で Sonnet（app/self_letters.py）。
    "手紙の挿絵": _SONNET,
}

# 安全の確認で断られたときに、API の側で別のモデルへ切り替える機能が使えるモデル。
# Haiku 5.5 にはこの機能がないので、断られた応答はアプリの側で今のメッセージとして扱う。
MODELS_WITH_FALLBACK = {_OPUS, _SONNET}

# 役割ごとの考える深さ。指定のない役割はモデルの既定のまま（Haiku は medium）。
# いずれも短く決まった形の答えなので、深く考えさせて費用と待ち時間を増やす必要がない。
# ツインの会話の深さは、返事の速さと言葉の質を実際に比べて決めた。low と medium で速さはほぼ同じで、
# medium のほうが決まりをよく守ったので medium にした（docs/tools.md）。
EFFORT_BY_ROLE: dict[str, str] = {
    "写真の問い": "low",
    "言明への分割": "medium",
    # 比べと同じ深さにする。モデルだけを変えて、差の出どころを分かるようにするため。
    SPLIT_DEEP_ROLE: "medium",
    # medium は重い言葉で、最初の発言の1件だけを返すことがある（実測で91回のうち10回）。同じ深さでやり直すと
    # 同じ答えが返るので、やり直しだけ深くする。落とした入力で試すと、high は6回とも落とさなかった。
    SPLIT_DEEP_RETRY_ROLE: "high",
    "架空ログの作成": "low",
    "架空の受け答え": "low",
    "架空の受け答えの確かめ": "low",
    "ツインの会話": "medium",
    TWIN_DEEP_ROLE: "medium",
    "スレッドの問い": "low",
    "グループのツイン": "low",
    "便りの下書き": "low",
    "人の候補": "low",
    "会話の要約": "low",
    "固める処理（月）": "low",
    "心の読み": "low",
    "重いやり取りを読む": "low",
    "仮説を直す": "low",
    "全体を照らす": "medium",
    "セルフを書く": "medium",
    "確かめる問いを作る": "low",
    "心の目次": "medium",
    "ツインの答え": "low",
    # 関係の薄い言葉を本人の言葉として届けないよう、low より深く考えさせる（docs/tools.md の比べ）
    "言葉を選ぶ": "medium",
    "確かめた言葉を足す": "medium",
    "固める処理": "medium",
    "推定意思": "medium",
    # 食い違う言葉を読み合わせて筋道を立てる処理なので、ほかの役割より深く考えさせる
    "ツインのアドボカシー": "high",
    "原案": "medium",
    "根拠チェック": "high",
    "比べの正解付け": "medium",
    "比べの正解の確かめ": "medium",
    "比べの判定": "low",
    "伝えを分ける": "low",
    "資料を読む": "medium",
    "手紙": "high",
    "生活まとめプラン": "medium",
    "書式を読む": "medium",
    "資料のレビュー": "medium",
    "本人の手紙": "medium",
    "手紙の校正": "low",
    # 絵の組み立て（何をどこに置き、どう重ねるか）を考えてから描くので、校正より深くする
    "手紙の挿絵": "medium",
}

# 役割ごとの応答の上限。考える処理の分もここに含まれるので、余裕を持たせる。
# 切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS_BY_ROLE: dict[str, int] = {
    "架空ログの作成": 6000,
    "架空の受け答え": 1000,
    "架空の受け答えの確かめ": 1000,
    "言明への分割": 12000,
    SPLIT_DEEP_ROLE: 12000,
    SPLIT_DEEP_RETRY_ROLE: 12000,
    "ツインの会話": 2000,
    TWIN_DEEP_ROLE: 2000,
    "写真の問い": 2000,
    "スレッドの問い": 1500,
    "グループのツイン": 1500,
    "便りの下書き": 1500,
    "人の候補": 3000,
    "会話の要約": 1500,
    "固める処理（月）": 6000,
    "心の読み": 6000,
    "やり取りを読む": 12000,
    "重いやり取りを読む": 12000,
    "ピースに仕分ける": 12000,
    "仮説を直す": 12000,
    "全体を照らす": 16000,
    "セルフを書く": 16000,
    "確かめる問いを作る": 4000,
    "問いを読む": 2000,
    "心の目次": 12000,
    "固める処理": 12000,
    "ツインの答え": 3000,
    "言葉を選ぶ": 3000,
    "確かめた言葉を足す": 2000,
    "推定意思": 6000,
    "ツインのアドボカシー": 16000,
    "原案": 8000,
    "根拠チェック": 16000,
    "比べの正解付け": 8000,
    "比べの正解の確かめ": 8000,
    "比べの判定": 4000,
    "伝えを分ける": 2000,
    "資料を読む": 8000,
    "手紙": 16000,
    "生活まとめプラン": 12000,
    "書式を読む": 12000,
    "資料のレビュー": 8000,
    "本人の手紙": 10000,
    "手紙の校正": 6000,
    # SVG は長い。考える分と合わせて、切れないように多めにする
    "手紙の挿絵": 20000,
}

# 本人が待つ処理は、長く待たせずに「もう一度送る」を出す。単位は秒。
TIMEOUT_SECONDS_BY_ROLE: dict[str, float] = {
    "ツインの会話": 60,
    TWIN_DEEP_ROLE: 60,
    "写真の問い": 90,
    "便りの下書き": 60,
    "ツインの答え": 90,
    "言葉を選ぶ": 90,
    "確かめた言葉を足す": 90,
    "推定意思": 150,
    "ツインのアドボカシー": 240,
    "原案": 180,
    "根拠チェック": 240,
    "伝えを分ける": 60,
    "資料を読む": 180,
    "手紙": 240,
    "生活まとめプラン": 240,
    "書式を読む": 180,
    "資料のレビュー": 180,
    "本人の手紙": 240,
    "手紙の校正": 120,
    "手紙の挿絵": 180,
    # 裏で動くので本人は待たない。止まったままにせず、Haiku の答えへ切り替えるための上限（app/intake.py）。
    # 比べの3単位は合計23.2秒だった。「言明への分割」は本人が待たず、切り替える先もないので、この表に入れていない。
    SPLIT_DEEP_ROLE: 180,
    SPLIT_DEEP_RETRY_ROLE: 180,
}

# ツインが前の話を思い出すために読む量の上限。記録が増えても読む量を増やさないため。
# 合計で約18,000字で、Haiku の高い値段（プロンプトが10万トークン超）にはならない。
# 固める処理ができたら、要約と本人の言葉は意思モデルに置き換える。決めた経緯：docs/decisions.md
MEMORY_LIMITS = {
    "current_utterances": 40,
    "current_chars": 8000,
    "past_conversations": 10,
    "summary_chars": 300,
    "unsummarized_utterances": 20,
    "self_statements": 40,
    "self_chars": 4000,
    "asked_questions": 30,
    "pending_questions": 5,
    # 心の目次。返事が読む量の上限。人とテーマの数が増えても、読む量は増えない
    "heart_chars": 16000,
    # 答え合わせで、目次のかわりに月の読みを渡すときの量。2036年5月の目次（18件）が約3,900字なので、同じくらいにそろえる
    "heart_chars_as_of": 4000,
    "heart_words_per_topic": 8,
    "heart_themes": 12,
    "heart_recent_months": 2,
}

# 推論で読む量の上限。記録が増えても、読むのは意思モデルの上限までなので、読む量はほぼ変わらない。
MODEL_LIMITS = {
    "element_chars": 12000,
    "selected_elements": 6,
    "quotes_per_element": 3,
    "quotes_total": 8,
    "quotes_first_shown": 3,
    # 言葉を選ぶ係に渡す候補の上限。定数で切るので、記録が増えても読む量は増えない
    "word_pool_per_element": 6,
    "word_pool_self": 30,
    "word_pool_others": 12,
    "testimonies": 3,
    "guesses": 2,
    "now": 4,
    "estimate_statements": 24,
    # ツインのアドボカシーで読む量の上限。10年の変わり方を読むので、問いへの答えより広く読む。定数で切るので、記録が増えても増えない
    "advocacy_element_chars": 24000,
    "advocacy_elements": 20,
    "advocacy_words_per_element": 8,
    "advocacy_words_self": 40,
    "advocacy_words_others": 12,
    # AI の読みを渡す言葉の数。答えの土台を生の言葉ではなく、読みにするため
    "advocacy_readings": 40,
    # 画面に出す量の上限。言葉は代弁を補強するときだけ引く。並べると検索と同じになり、本人が他人に聞かせるつもりのなかった言葉も出るため
    "advocacy_reasons": 3,
    "advocacy_quotes_per_reason": 1,
    "selfhood_pieces": 12,
    "selfhood_words_per_piece": 6,
    "selfhood_words": 24,
    "advocacy_elements_per_reason": 3,
    "advocacy_objections": 2,
    "sheet_change_quotes": 3,
    "sheet_plan_quotes": 2,
    # 1枚の紙は話題ごとに材料を集めるので、1回の主張より広く読む
    "sheet_words_per_element": 6,
    "sheet_words_self": 100,
    "sheet_words_others": 30,
    "context_elements": 40,
    "month_statement_chars": 16000,
}

# 公式の料金表（2026-11-02 に確認）。1M トークンあたりの USD。費用の計算と docs/tools.md に使う。
# バッチは入力も出力も半額。Haiku 5.5 だけ、プロンプトが 10万トークンを超えると高い値段になる。
PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    _OPUS: {"input": 4.0, "output": 20.0},
    _SONNET: {"input": 2.0, "output": 10.0},
    _HAIKU: {"input": 0.10, "output": 0.50},
}
LONG_PROMPT_TOKENS = 100_000
PRICE_PER_MTOK_LONG_PROMPT: dict[str, dict[str, float]] = {
    _HAIKU: {"input": 0.50, "output": 2.50},
}
BATCH_DISCOUNT = 0.5

# 1回の応答の上限。ここで切れると JSON が壊れ、型に合わない答えとしてやり直しになる。
MAX_TOKENS = 16000

# 通信の失敗のときに SDK が繰り返す回数。費用と待ち時間を読めるよう、明示する。
MAX_API_RETRIES = 2


# プロンプトキャッシュの料金の倍率。読むと入力の1割、書くと入力の1.25倍。
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


def cache_cost_usd(model: str, read_tokens: int, write_tokens: int) -> float:
    """プロンプトキャッシュの分の費用を計算する。input_tokens には含まれない分。"""
    price = PRICE_PER_MTOK[model]["input"]
    return (read_tokens * CACHE_READ_RATE + write_tokens * CACHE_WRITE_RATE) * price / 1_000_000


def cost_usd(model: str, input_tokens: int, output_tokens: int, *, batch: bool = False) -> float:
    """トークン数から費用を計算する。料金表にないモデルは 0 ではなくエラーにする。"""
    price = PRICE_PER_MTOK[model]
    if input_tokens > LONG_PROMPT_TOKENS and model in PRICE_PER_MTOK_LONG_PROMPT:
        price = PRICE_PER_MTOK_LONG_PROMPT[model]
    cost = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    return cost * BATCH_DISCOUNT if batch else cost


class MissingApiKeyError(RuntimeError):
    """API キーが見つからないとき。"""


def has_api_key() -> bool:
    """API キーが見つかるかだけを返す。通信はせず、値も返さない。画面の案内に使う。"""
    try:
        api_key()
    except MissingApiKeyError:
        return False
    return True


def api_key() -> str:
    """API キーを返す。環境変数を先に見て、なければ .env から読む。

    os.environ に書き込まない読み方にしているのは、キーが子のプロセスへ
    意図せず受け渡されるのを避けるため。キーの値は画面にも記録にも出さない。
    """
    key = os.environ.get(API_KEY_NAME) or dotenv_values(ENV_PATH).get(API_KEY_NAME)
    if not key:
        raise MissingApiKeyError(
            f"API キーが見つかりません。{ENV_PATH.name} に {API_KEY_NAME}=... を書いてください。"
        )
    return key
