"""AI が分けた言明を確かめて保存する処理のテスト。

役割：AI の結果がどうであっても、台帳の決まりに合うものだけが入ることを確かめる。
4つの極：3. 読み解き、積み上げる。
守る原則：3（出どころを分ける）、4（推測は言い切らない）、9（一字も変えない）、10（共有範囲）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import db, ledger, splitter
from app.prompts import TWIN_LABEL
from app.schemas import (
    CommentDraft,
    ConfirmStatus,
    InterpretationStatus,
    LetterDraft,
    Perspective,
    Period,
    Petal,
    Role,
    ShareScope,
    Source,
    SplitInterpretation,
    SplitResult,
    SplitStatement,
    Theme,
)


def _item(body, *, source=Source.SELF_WORDS, speaker="誠一さん", subject="誠一さん", numbers=(1,), **changes):
    return SplitStatement(
        source=source,
        speaker=speaker,
        subject=subject,
        body=body,
        period=Period.NOW,
        theme=Theme.LIKES,
        petals=[Petal.COMFORT],
        utterance_numbers=list(numbers),
        **changes,
    )


def _interpretation(perspective=Perspective.INTENT, confidence=0.9, caution=None):
    return SplitInterpretation(
        perspective=perspective,
        viewpoint_role=Role.SELF,
        body="庭で落ち着くのかもしれません",
        confidence=confidence,
        caution=caution,
    )


@pytest.fixture
def save(conn):
    """発言と AI の結果を渡して保存する。"""

    def _save(utterances, *items):
        return splitter.save_split(conn, SplitResult(statements=list(items)), utterances, db.list_users(conn))

    return _save


# ---- プログラムが決めること ----


def test_共有範囲はすべて本人だけで入る(conn, save, add_utterance):
    said = add_utterance("毎朝この庭を見るのが楽しみなんです。")

    report = save([said], _item("毎朝この庭を見るのが楽しみなんです。"))

    assert ledger.get_statement(conn, report.statement_ids[0]).share_scope is ShareScope.SELF_ONLY


def test_確認の状態は未確認で入る(conn, save, add_utterance):
    said = add_utterance("毎朝この庭を見るのが楽しみなんです。")

    report = save([said], _item("毎朝この庭を見るのが楽しみなんです。"))

    assert ledger.get_statement(conn, report.statement_ids[0]).confirm_status is ConfirmStatus.UNCONFIRMED


def test_発言の番号と日付は発言から引き継ぐ(conn, save, add_utterance):
    said = add_utterance("毎朝この庭を見るのが楽しみなんです。")

    report = save([said], _item("毎朝この庭を見るのが楽しみなんです。"))

    statement = ledger.get_statement(conn, report.statement_ids[0])
    assert statement.source_utterance_ids == [said.id]
    assert statement.effective_date == date(2026, 11, 2)


# ---- 本人の言葉 ----


@pytest.mark.parametrize(
    ("text", "body", "reason"),
    [
        ("家で暮らしたいとは思いません。", "家で暮らしたい", "文の途中"),
        ("庭が好きです。", "海が好きです。", "原文にありません"),
        ("本当は家にいたいけど、迷惑はかけたくない。", "迷惑はかけたくない。", "文の途中"),
    ],
)
def test_文の途中や原文にない本人の言葉は捨てて数える(conn, save, add_utterance, text, body, reason):
    said = add_utterance(text)

    report = save([said], _item(body))

    assert report.statement_ids == []
    assert report.dropped[0].body == body
    assert reason in report.dropped[0].reason


def test_捨てても書き換えて救わない(conn, save, add_utterance):
    said = add_utterance("家で暮らしたいとは思いません。")

    save([said], _item("家で暮らしたい"))

    assert ledger.current_statements(conn) == []


def test_本人の訂正は分ける処理からは作れない(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    report = save([said], _item("庭が好きです。", source=Source.SELF_CORRECTION))

    assert report.statement_ids == []
    assert "訂正の処理" in report.dropped[0].reason


# ---- 話した人と誰についてか（TSS）----


def test_話した人と誰についてかを分けて持つ(conn, save, users, add_utterance):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    report = save(
        [said],
        _item("庭が好きなようだ", source=Source.FAMILY_TESTIMONY, speaker="美穂さん"),
    )

    statement = ledger.get_statement(conn, report.statement_ids[0])
    assert statement.speaker_user_id == users["美穂さん"].id
    assert statement.subject_user_id == users["誠一さん"].id


def test_家族の発言を本人の言葉として入れない(conn, save, add_utterance):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    report = save([said], _item("父は庭が好きです。", speaker="美穂さん"))

    assert report.statement_ids == []


def test_家族の発言を本人が話したことにしても入らない(conn, save, add_utterance):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    report = save([said], _item("父は庭が好きです。"))

    assert report.statement_ids == []


def test_縁のある人の発言を家族の証言にしても入らない(conn, save, add_utterance):
    said = add_utterance("誠一さんの庭はきれいです。", speaker="節子さん")

    report = save(
        [said],
        _item("庭がきれいだ", source=Source.FAMILY_TESTIMONY, speaker="節子さん"),
    )

    assert report.statement_ids == []


def test_一覧にない人は捨てる(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    report = save(
        [said],
        _item("庭が好きです。", subject="和子さん"),
        _item("庭が好きです。", speaker="和子さん"),
    )

    assert report.statement_ids == []
    assert len(report.dropped) == 2


@pytest.mark.parametrize("numbers", [(), (0,), (2,)], ids=["なし", "0", "入力にない"])
def test_入力にない発言の番号は捨てる(conn, save, add_utterance, numbers):
    said = add_utterance("庭が好きです。")

    report = save([said], _item("庭が好きです。", numbers=numbers))

    assert report.statement_ids == []


# ---- AIの推測 ----


def test_AIの推測はツインの言明として入る(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    report = save(
        [said],
        _item("庭を大切にしているのかもしれません", source=Source.AI_GUESS, speaker=TWIN_LABEL),
    )

    statement = ledger.get_statement(conn, report.statement_ids[0])
    assert statement.source is Source.AI_GUESS
    assert statement.speaker_is_twin is True


@pytest.mark.parametrize(
    "body",
    [
        "庭を大切にしている",
        "誠一さんは自分らしい暮らしを大切にしている。",
        "庭が好きなのではなく、妻と過ごした場所だから大切だ。",
        "庭が大切だ。合っているかもしれません。",
    ],
)
def test_言い切ったAIの推測は捨てる(conn, save, add_utterance, body):
    said = add_utterance("庭が好きです。")

    report = save([said], _item(body, source=Source.AI_GUESS, speaker=TWIN_LABEL))

    assert report.statement_ids == []
    assert "言い切っています" in report.dropped[0].reason


def test_文がすべて言い切らない形の推測は入る(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")
    body = "遠慮かもしれません。本当は続けたいのかもしれません"

    report = save([said], _item(body, source=Source.AI_GUESS, speaker=TWIN_LABEL))

    assert len(report.statement_ids) == 1


def test_ツインの問いだけを根拠にした推測は捨てる(conn, save, add_utterance):
    asked = add_utterance("誠一さん、庭はお好きですか？", is_twin=True)

    report = save(
        [asked],
        _item("庭が好きなのかもしれません", source=Source.AI_GUESS, speaker=TWIN_LABEL),
    )

    assert report.statement_ids == []
    assert "人の発言" in report.dropped[0].reason


def test_AIの推測に解釈は付けない(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    save(
        [said],
        _item(
            "庭を大切にしているのかもしれません",
            source=Source.AI_GUESS,
            speaker=TWIN_LABEL,
            interpretations=[_interpretation()],
        ),
    )

    assert conn.execute("SELECT COUNT(*) FROM interpretations").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("source", "said_by", "written_by"),
    [
        (Source.FAMILY_TESTIMONY, "節子さん", "美穂さん"),
        (Source.FAMILY_TESTIMONY, "誠一さん", "美穂さん"),
        (Source.OBSERVATION, "美穂さん", "田中さん"),
    ],
)
def test_AIが話した人を偽っても入らない(save, add_utterance, source, said_by, written_by):
    said = add_utterance("施設は嫌です。", speaker=said_by)

    report = save([said], _item("施設は嫌なようだ", source=source, speaker=written_by))

    assert report.statement_ids == []


def test_スレッドのコメントは今の様子にできない(conn, users, save):
    letter = ledger.add_letter(
        conn,
        LetterDraft(body="便りです。", share_with=ShareScope.FAMILY, shared_at=datetime(2026, 11, 3)),
    )
    comment = ledger.add_comment(
        conn,
        CommentDraft(
            letter_id=letter.id,
            author_user_id=users["美穂さん"].id,
            body="父は元気そうでした。",
            commented_at=datetime(2026, 11, 3),
        ),
    )
    said = ledger.get_utterance(conn, comment.utterance_id)

    report = save([said], _item("元気そうだった", source=Source.OBSERVATION, speaker="美穂さん"))

    assert report.statement_ids == []


def test_ツイン以外のAIの推測は入らない(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    report = save([said], _item("庭を大切にしているのかもしれません", source=Source.AI_GUESS))

    assert report.statement_ids == []


@pytest.mark.parametrize(
    ("question", "theme"),
    [
        ("庭はお好きですか？合っていますか？", Theme.LIKES),
        ("庭がお好きなのかもしれません。", Theme.LIKES),
        ("つらかったのかもしれません。合っていますか？", Theme.HARDSHIP),
    ],
    ids=["問いが2つ", "問いでない", "つらかったこと"],
)
def test_確かめる問いにならない候補は残さない(conn, save, add_utterance, question, theme):
    said = add_utterance("迷惑はかけたくないです。")

    report = save(
        [said],
        _item(
            "遠慮しているのかもしれません",
            source=Source.AI_GUESS,
            speaker=TWIN_LABEL,
            confirm_question=question,
        ).model_copy(update={"theme": theme}),
    )

    assert report.question_count == 0
    assert report.dropped_question_count == 1
    assert conn.execute("SELECT COUNT(*) FROM question_logs").fetchone()[0] == 0


def test_確かめる問いの候補は聞いていない質問として残る(conn, save, add_utterance):
    said = add_utterance("迷惑はかけたくないです。")

    report = save(
        [said],
        _item(
            "本当は手を借りたいのかもしれません",
            source=Source.AI_GUESS,
            speaker=TWIN_LABEL,
            confirm_question="手を借りたいと思うこともあるかもしれません。合っていますか？",
        ),
    )

    assert report.question_count == 1
    row = conn.execute("SELECT * FROM question_logs").fetchone()
    assert row["question"].endswith("合っていますか？")
    assert row["asked_on"] is None
    assert row["result"] is None


# ---- 解釈（NAK-4V と仮説リーフ）----


def test_解釈は仮説として言明の下に入る(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    report = save([said], _item("庭が好きです。", interpretations=[_interpretation()]))

    row = conn.execute("SELECT * FROM interpretations").fetchone()
    assert row["status"] == InterpretationStatus.HYPOTHESIS.value
    assert row["statement_id"] == report.statement_ids[0]
    assert report.interpretation_count == 1


def test_本人が話していれば本人の真意の確からしさはそのまま(conn, save, add_utterance):
    said = add_utterance("庭が好きです。")

    save([said], _item("庭が好きです。", interpretations=[_interpretation(confidence=0.9)]))

    assert conn.execute("SELECT confidence FROM interpretations").fetchone()[0] == 0.9


def test_本人の発言がなければ本人の真意は0点4までで注意書きが付く(conn, save, add_utterance):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    save(
        [said],
        _item(
            "庭が好きなようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker="美穂さん",
            interpretations=[_interpretation(confidence=0.9)],
        ),
    )

    row = conn.execute("SELECT confidence, caution FROM interpretations").fetchone()
    assert row["confidence"] == 0.4
    assert row["caution"]


def test_本人が別の発言をしていても根拠が家族の発言なら0点4まで(conn, save, add_utterance):
    self_said = add_utterance("庭が好きです。")
    family_said = add_utterance("父は施設は嫌だと思っているはずです。", speaker="美穂さん")

    save(
        [self_said, family_said],
        _item(
            "施設は嫌だと思っているようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker="美穂さん",
            numbers=(2,),
            interpretations=[_interpretation(confidence=0.9)],
        ),
    )

    row = conn.execute("SELECT confidence, caution FROM interpretations").fetchone()
    assert row["confidence"] == 0.4
    assert "本人の発言がない" in row["caution"]


@pytest.mark.parametrize("caution", [None, "", "   ", "特になし"])
def test_本人の発言がなければ注意書きが必ず付く(conn, save, add_utterance, caution):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    save(
        [said],
        _item(
            "庭が好きなようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker="美穂さん",
            interpretations=[_interpretation(caution=caution)],
        ),
    )

    assert "本人の発言がない" in conn.execute("SELECT caution FROM interpretations").fetchone()[0]


def test_本人の発言がなくても本人の真意以外の視点は変えない(conn, save, add_utterance):
    said = add_utterance("父は庭が好きです。", speaker="美穂さん")

    save(
        [said],
        _item(
            "庭が好きなようだ",
            source=Source.FAMILY_TESTIMONY,
            speaker="美穂さん",
            interpretations=[_interpretation(perspective=Perspective.SURFACE, confidence=0.9)],
        ),
    )

    assert conn.execute("SELECT confidence FROM interpretations").fetchone()[0] == 0.9


def test_捨てた言明の解釈は入らない(conn, save, add_utterance):
    said = add_utterance("家で暮らしたいとは思いません。")

    save([said], _item("家で暮らしたい", interpretations=[_interpretation()]))

    assert conn.execute("SELECT COUNT(*) FROM interpretations").fetchone()[0] == 0


# ---- AI に渡す形 ----


def test_AIに渡す文に発言の本当の番号は入らない(conn, add_utterance):
    said = add_utterance("庭が好きです。")
    twin = add_utterance("誠一さん、庭の何がお好きですか？", is_twin=True)

    transcript = splitter.format_transcript("日記と会話", date(2026, 11, 2), [said, twin], db.list_users(conn))

    assert "[1] 誠一さん（本人）：庭が好きです。" in transcript
    assert f"[2] {TWIN_LABEL}：誠一さん、庭の何がお好きですか？" in transcript
    assert str(said.id) not in transcript


def test_便りの本文は文脈として渡し番号を振らない(conn, add_utterance):
    said = add_utterance("庭が好きです。")

    transcript = splitter.format_transcript(
        "スレッド", date(2026, 11, 3), [said], db.list_users(conn), "縁側の便りです。"
    )

    assert "便りの本文" in transcript
    assert "[1] 誠一さん" in transcript
    assert "[2]" not in transcript


def test_空の本文は型で止める():
    with pytest.raises(ValueError):
        _item("")


def test_本文の問題をやり直しの文にする(add_utterance):
    said = add_utterance("家で暮らしたいとは思いません。")
    result = SplitResult(statements=[_item("家で暮らしたい"), _item("家で暮らしたいとは思いません。")])

    problems = splitter.quote_problems(result, [said])

    assert len(problems) == 1
    assert "文の途中" in problems[0]


# ---- 保存は1つの処理にまとめる ----


def test_保存の途中で失敗すると何も残らず同じ発言からやり直せる(conn, save, add_utterance, monkeypatch):
    utterance = add_utterance("庭が好きです。花も好きです。")
    items = (
        _item("庭が好きです。", interpretations=[_interpretation()]),
        _item("花も好きです。", confirm_question="花が好きなのかもしれません。合っていますか？"),
    )

    def _fail(*args, **kwargs):
        raise RuntimeError("途中で失敗")

    with monkeypatch.context() as patched:
        patched.setattr(ledger, "add_pending_question", _fail)
        with pytest.raises(RuntimeError):
            save([utterance], *items)

    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM interpretations").fetchone()[0] == 0

    report = save([utterance], *items)

    assert len(report.statement_ids) == 2


def test_決まりに合わない言明を捨てても_ほかの言明は保存される(conn, save, add_utterance):
    utterance = add_utterance("庭が好きです。")

    report = save([utterance], _item("海が好きです。"), _item("庭が好きです。"))

    assert len(report.statement_ids) == 1
    assert len(report.dropped) == 1
    assert conn.in_transaction is False
