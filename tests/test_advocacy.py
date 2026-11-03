"""専門職とツインのチャットのテスト。

役割：本人の言葉が記録の本文のまま届くこと、記録がなければ「まだ聞けていません」と答えることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：2（今の様子、本人の言葉、AIの推測の順）、3（出どころを分ける）、9（一字も変えない）、
          10（見える範囲）、11（推定は別の枠）、12（なりきらない）。

通信だけを差し替える。本物の Claude API は呼ばない。AI が番号を選ぶ部分だけを決めて、プログラムの動きを確かめる。
本物の API での確かめは docs/verification/ にある。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import advocacy, ai, config, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, RequestKind, ShareScope, Source, Theme
from tests.fake_claude import RoleClaude

SELF_WORDS = "できれば最後まで、この家で庭を見ていたい。"
PRIVATE = "兄とは仲直りできなかった。"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _element(conn, evidence, body="誠一さんはこの家で暮らし続けたい。", unit=ConsolidationUnit.YEAR):
    element = model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body=body,
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=evidence,
        unit=unit,
    )
    conn.commit()
    return element


@pytest.fixture
def wish(conn, add_statement):
    statement = add_statement(SELF_WORDS, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    _element(conn, [statement.id])
    return statement


def _ask(client, question, key="k1"):
    return client.post("/api/advocacy/ask", json={"question": question, "client_key": key})


def _pick(direct=(), related=(), kind="通常"):
    return {"direct_numbers": list(direct), "related_numbers": list(related), "request_kind": kind}


def _words(self_numbers=(), testimony=(), guess=(), lead=None):
    return {"self_numbers": list(self_numbers), "testimony_numbers": list(testimony), "guess_numbers": list(guess), "lead": lead}


def test_本人の言葉は記録の本文のまま日付と出どころつきで届く(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert turn["not_yet"] is None
    quote = turn["self_words"][0]
    assert quote["body"] == SELF_WORDS
    assert quote["source"] == "本人の言葉"
    assert quote["date"] == wish.effective_date.isoformat()
    assert quote["lead_in"].endswith("誠一さんはこうおっしゃいました")
    assert quote["confirm"] == "まだ確かめていない"


def test_本人の言葉の引用はAIの出力のどの項目からも作られない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead="誠一さんは目印の作り話をしています。")

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert all(item["body"] == SELF_WORDS for item in turn["self_words"])
    assert "目印の作り話" not in json.dumps(turn["self_words"], ensure_ascii=False)


def test_要素を選ぶ呼び出しには本人の言葉の本文を渡さない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    _ask(login("田中さん"), "願いは何ですか")

    assert SELF_WORDS not in json.dumps(claude.bodies("ツインの答え"), ensure_ascii=False)


def test_本人の言葉の根拠がなければまだ聞けていませんと答え_質問の記録に残る(conn, login, wish, claude):
    claude.answers["ツインの答え"] = _pick()

    turn = _ask(login("田中さん"), "泊まりを使うことを、どう考えていましたか？").json()["turn"]

    assert turn["not_yet"] == "まだ聞けていません。"
    assert turn["self_words"] == []
    assert turn["estimate"] is None
    row = conn.execute("SELECT question, asked_on FROM question_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["question"] == "泊まりを使うことを、どう考えていましたか？"
    assert row["asked_on"] is None


def test_関係がある要素だけなら推定を別の枠に出す(conn, login, wish, claude, add_statement):
    against = add_statement("施設に入るなんて考えたくもない。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    _element(conn, [against.id], body="誠一さんは施設を避けたい。")
    claude.answers["ツインの答え"] = _pick(related=[1, 2])
    claude.answers["推定意思"] = {
        "estimate": "家を離れることには慎重かもしれません。",
        "evidence_numbers": [1],
        "counter_numbers": [2],
        "confidence": 0.4,
        "how_to_confirm": "ご本人の表情を見ながら、奥様にうかがってください。",
    }

    turn = _ask(login("田中さん"), "泊まりを使うことを、どう考えていましたか？").json()["turn"]

    assert turn["not_yet"] == "まだ聞けていません。"
    assert turn["self_words"] == []
    estimate = turn["estimate"]
    assert estimate["text"] == "家を離れることには慎重かもしれません。"
    assert len(estimate["evidence"]) == 1
    assert len(estimate["counter"]) == 1
    assert estimate["confidence"] == "低い"
    assert estimate["how_to_confirm"]


@pytest.mark.parametrize(
    "draft",
    [
        {"estimate": "家を離れたくないと思っています。", "evidence_numbers": [1], "counter_numbers": [], "confidence": 0.4, "how_to_confirm": "うかがう。"},
        {"estimate": "慎重かもしれません。", "evidence_numbers": [], "counter_numbers": [], "confidence": 0.4, "how_to_confirm": "うかがう。"},
        {"estimate": "慎重かもしれません。", "evidence_numbers": [99], "counter_numbers": [], "confidence": 0.4, "how_to_confirm": "うかがう。"},
        {"estimate": "私は慎重かもしれません。", "evidence_numbers": [1], "counter_numbers": [], "confidence": 0.4, "how_to_confirm": "うかがう。"},
    ],
)
def test_言い切りと根拠のない推定は枠を出さない(login, wish, claude, draft):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["推定意思"] = draft

    assert _ask(login("田中さん"), "どう考えていましたか").json()["turn"]["estimate"] is None


def test_言葉を選ぶ係が問いそのものの言葉を選ぶと_関係のある要素の根拠でも本文のまま届き_推定は出ない(conn, login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead="見当たりません。")

    turn = _ask(login("田中さん"), "好きな食べ物は？").json()["turn"]

    assert turn["self_words"][0]["body"] == wish.body
    assert turn["not_yet"] is None
    assert turn["estimate"] is None
    assert turn["lead"] is None
    assert conn.execute("SELECT COUNT(*) FROM question_logs").fetchone()[0] == 0
    assert [item["element_id"] for item in turn["elements"]]


def test_言葉を選ぶ係が渡していない番号を選んでも本人の言葉にならない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[99])

    turn = _ask(login("田中さん"), "好きな食べ物は？").json()["turn"]

    assert turn["self_words"] == []
    assert turn["not_yet"] == "まだ聞けていません。"


def test_言葉を選ぶ係が返した番号は_出どころの枠と合うものだけ通る(conn, login, users, add_statement, add_utterance, make_draft, wish, claude):
    utterance = add_utterance("父は施設だけは嫌だと言っていました。", speaker="美穂さん")
    testimony = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            utterance.text,
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
            theme=Theme.HOME,
        ),
    )
    _element(conn, [wish.id, testimony.id], body="誠一さんと美穂さんの話。")
    claude.answers["ツインの答え"] = _pick(direct=[1, 2])
    # 本人の言葉の枠に、証言の番号も入れてくる
    numbers = {}

    def answer_with_swap(body):
        for line in body.splitlines():
            if "本人の言葉" in line:
                numbers["self"] = int(line.split("]")[0].strip("["))
            if "家族の証言" in line:
                numbers["family"] = int(line.split("]")[0].strip("["))

    client = login("田中さん")
    claude.answers["言葉を選ぶ"] = None
    _ask(client, "施設をどう話していましたか", key="first")
    answer_with_swap(claude.bodies("言葉を選ぶ")[0]["messages"][0]["content"] if isinstance(claude.bodies("言葉を選ぶ")[0]["messages"][0]["content"], str) else claude.bodies("言葉を選ぶ")[0]["messages"][0]["content"][0]["text"])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[numbers["family"]], testimony=[numbers["self"]])

    turn = _ask(client, "もう一度", key="second").json()["turn"]

    assert turn["self_words"] == []
    assert turn["testimony"] == []
    assert turn["not_yet"] == "まだ聞けていません。"


def test_言葉を選ぶ係が失敗しても_要素の根拠から本人の言葉が届く(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = 500

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert turn["self_words"][0]["body"] == wish.body
    assert turn["not_yet"] is None


def test_言葉を選ぶ係が選ばなければ届かず_まだ聞けていませんになる(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words()

    turn = _ask(login("田中さん"), "床板のことは").json()["turn"]

    assert turn["self_words"] == []
    assert turn["not_yet"] == "まだ聞けていません。"


def test_記録が増えても_言葉を選ぶ係に渡す件数は上限で変わらない(conn, login, add_statement, claude):
    statements = [
        add_statement(f"庭の話その{number}です。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
        for number in range(config.MODEL_LIMITS["word_pool_self"] * 2)
    ]
    for start in range(0, len(statements), 3):
        _element(conn, [item.id for item in statements[start : start + 3]], body=f"庭の要素{start}。")
    claude.answers["ツインの答え"] = _pick(direct=list(range(1, 7)), related=list(range(7, 13)))

    _ask(login("田中さん"), "庭のことは")

    content = claude.bodies("言葉を選ぶ")[0]["messages"][0]["content"]
    text = content if isinstance(content, str) else content[0]["text"]
    lines = [line for line in text.splitlines() if line.startswith("[")]
    assert len(lines) <= config.MODEL_LIMITS["word_pool_self"] + config.MODEL_LIMITS["word_pool_others"]
    assert config.MODEL_LIMITS["selected_elements"] == 6


def test_要素の根拠にない家族の証言も_候補に新しい順で入り_選ばれると証言の枠に出る(conn, login, users, add_utterance, make_draft, wish, claude):
    utterance = add_utterance("木の話をするときの顔が生き生きしていました。", speaker="美穂さん")
    testimony = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            utterance.text,
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
            theme=Theme.FAMILY,
        ),
    )
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], testimony=[2])

    turn = _ask(login("田中さん"), "ご家族から見た誠一さんは").json()["turn"]

    assert [item["statement_id"] for item in turn["testimony"]] == [testimony.id]


def test_本人だけの証言は候補に入らない(conn, login, users, add_utterance, make_draft, wish, claude):
    utterance = add_utterance("目印の本人だけの証言です。", speaker="美穂さん")
    ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            utterance.text,
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.SELF_ONLY,
            theme=Theme.HARDSHIP,
        ),
    )
    claude.answers["ツインの答え"] = _pick(direct=[1])

    _ask(login("田中さん"), "ご家族から見た誠一さんは")

    assert "目印の本人だけの証言" not in json.dumps(claude.bodies("言葉を選ぶ"), ensure_ascii=False)


@pytest.mark.parametrize(
    "estimate",
    ["庭が好きです。和子さんも大切かもしれません。", "庭が好きかもしれません。和子さんが大切です。", "庭が好きです。"],
)
def test_推定は全部の文がかもしれませんで終わらなければ出さない(login, wish, claude, estimate):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": estimate, "evidence_numbers": [1], "confidence": 0.3, "how_to_confirm": "本人に聞きます。"}

    turn = _ask(login("田中さん"), "泊まりのことは").json()["turn"]

    assert turn["estimate"] is None
    assert turn["not_yet"] == "まだ聞けていません。"


@pytest.mark.parametrize(
    "estimate", ["記録にはありませんが、庭が好きかもしれません。", "泊まりの話は見当たらないかもしれません。"]
)
def test_推定に記録がないという言い切りがあれば出さない(login, wish, claude, estimate):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": estimate, "evidence_numbers": [1], "confidence": 0.3, "how_to_confirm": "本人に聞きます。"}

    turn = _ask(login("田中さん"), "泊まりのことは").json()["turn"]

    assert turn["estimate"] is None


def test_推定に鉤括弧があれば出さない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": "「遠くはもう行かない」と思っているかもしれません。", "evidence_numbers": [1], "confidence": 0.3, "how_to_confirm": "本人に聞きます。"}

    turn = _ask(login("田中さん"), "泊まりのことは").json()["turn"]

    assert turn["estimate"] is None


def test_選ばれた順の先頭3件に印を付け_表示は新しい順のまま(conn, login, add_statement, claude):
    statements = [
        add_statement(f"畑の話その{number}です。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2030 + number, 1, 1))
        for number in range(5)
    ]
    _element(conn, [item.id for item in statements], body="誠一さんは畑を大切にしている。")
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = None

    turn = _ask(login("田中さん"), "畑のことは").json()["turn"]

    years = [item["date"][:4] for item in turn["self_words"]]
    assert years == sorted(years, reverse=True)
    assert sum(1 for item in turn["self_words"] if item["top"]) == config.MODEL_LIMITS["quotes_first_shown"]


def test_先に見せる言葉には_いちばん新しい言葉が必ず入る(conn, login, add_statement, claude):
    statements = [
        add_statement(f"家の話その{number}です。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2026, 1, number + 1))
        for number in range(4)
    ]
    newest = add_statement("施設のことも考えていい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2033, 6, 12))
    _element(conn, [item.id for item in [*statements, newest]], body="誠一さんの家の話。")
    claude.answers["ツインの答え"] = _pick(direct=[1])
    # AI は古い言葉を先に選ぶ
    claude.answers["言葉を選ぶ"] = None

    client = login("田中さん")
    _ask(client, "どこで暮らしたいですか", key="first")
    content = claude.bodies("言葉を選ぶ")[0]["messages"][0]["content"]
    text = content if isinstance(content, str) else content[0]["text"]
    numbers = {line.split("：", 1)[1]: int(line.split("]")[0].strip("[")) for line in text.splitlines() if line.startswith("[")}
    claude.answers["言葉を選ぶ"] = _words(
        self_numbers=[numbers[f"家の話その{index}です。"] for index in range(4)] + [numbers[newest.body]]
    )

    turn = _ask(client, "どこで暮らしたいですか", key="second").json()["turn"]

    top = [item["body"] for item in turn["self_words"] if item["top"]]
    assert newest.body in top
    assert len(top) == config.MODEL_LIMITS["quotes_first_shown"]


def test_推定に最近と書いたら出さない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": "最近は遠くへ出かけないかもしれません。", "evidence_numbers": [1], "confidence": 0.3, "how_to_confirm": "本人に聞きます。"}

    turn = _ask(login("田中さん"), "旅行のことは").json()["turn"]

    assert turn["estimate"] is None


def test_推定の係に今日の日付を渡す(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()

    _ask(login("田中さん"), "旅行のことは")

    assert "今日の日付" in json.dumps(claude.bodies("推定意思"), ensure_ascii=False)


def test_言葉を選ぶ係が食い違いを示すと_言葉が変わっていることを知らせる(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = {**_words(self_numbers=[1]), "words_differ": True}

    turn = _ask(login("田中さん"), "施設のことは").json()["turn"]

    assert turn["changed"] is True


def test_本人の言葉の根拠がない推定は_確からしさをとても低いにする(conn, login, users, add_utterance, make_draft, claude):
    utterance = add_utterance("施設は嫌だと言っていました。", speaker="美穂さん")
    testimony = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            utterance.text,
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
            theme=Theme.HOME,
        ),
    )
    _element(conn, [testimony.id], body="美穂さんの話。")
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": "嫌かもしれません。", "evidence_numbers": [1], "confidence": 0.6, "how_to_confirm": "本人に聞きます。"}

    turn = _ask(login("田中さん"), "泊まりのことは").json()["turn"]

    assert turn["estimate"]["confidence"] == "とても低い"


@pytest.mark.parametrize(
    "lead",
    [
        "施設については、以前は家で暮らしたいと語り、後には考えてもよいと話しています。",
        "和子さんへの負担を気にして考え方が動いてきた経緯です。",
        "庭についての記録です。家についての記録です。",
    ],
)
def test_案内は話題の名前だけで_経緯や理由を書くと捨てる(login, wish, claude, lead):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead=lead)

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert turn["lead"] is None


def test_話題の名前だけの案内は通る(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead="暮らす場所についての記録です。")

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert turn["lead"] == "暮らす場所についての記録です。"


def test_手がかりがなければ推定を書かず_枠を出さない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])
    claude.answers["言葉を選ぶ"] = _words()
    claude.answers["推定意思"] = {"estimate": None}

    turn = _ask(login("田中さん"), "お墓のことは").json()["turn"]

    assert turn["estimate"] is None
    assert turn["not_yet"] == "まだ聞けていません。"


def test_直接答える要素がないときは_言葉を選ぶ係にはっきり伝える(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(related=[1])

    _ask(login("田中さん"), "海外旅行のことは")

    assert "直接答える記録は見つかっていません" in json.dumps(claude.bodies("言葉を選ぶ"), ensure_ascii=False)


def test_健康の判断の頼みは質問の記録に残さない(conn, login, wish, claude):
    claude.answers["ツインの答え"] = _pick()

    _ask(login("田中さん"), "認知症かどうか判断してください")

    assert conn.execute("SELECT COUNT(*) FROM question_logs").fetchone()[0] == 0


def test_案内に届ける本文の写しがあれば捨てる(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead=f"{wish.body}についての記録です。")

    turn = _ask(login("田中さん"), "願いは何ですか").json()["turn"]

    assert turn["self_words"][0]["body"] == wish.body
    assert turn["lead"] is None


def test_変化の要素があれば_言葉が変わっていることを知らせる(conn, login, add_statement, claude):
    statement = add_statement("施設のことも考えていい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    element = model_store.add_element(
        conn,
        kind=ElementKind.CHANGE,
        body="誠一さんは施設についての考えが変わった。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2033, 1, 1),
        evidence_statement_ids=[statement.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    assert element.kind is ElementKind.CHANGE
    claude.answers["ツインの答え"] = _pick(direct=[1])

    turn = _ask(login("田中さん"), "施設のことは").json()["turn"]

    assert turn["changed"] is True


def test_上限で切っても_選ばれた要素の古い本人の言葉が残る(conn, login, add_statement, claude):
    # 新しい言葉だけで上限を超える要素を3つ作り、古い言葉の要素を最後に選ばせる
    for group in range(3):
        recent = [
            add_statement(
                f"最近の話{group}の{number}です。",
                scope=ShareScope.DECISION_SUPPORT,
                theme=Theme.HOME,
                effective_date=date(2034, group + 1, number + 1),
            )
            for number in range(config.MODEL_LIMITS["quotes_per_element"])
        ]
        _element(conn, [item.id for item in recent], body=f"誠一さんの最近の話{group}。")
    old = add_statement("来年も裏の畑で玉ねぎを作りたい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2026, 11, 3))
    _element(conn, [old.id], body="誠一さんは畑で玉ねぎを作りたい。")
    claude.answers["ツインの答え"] = _pick(direct=[1, 2, 3, 4])

    turn = _ask(login("田中さん"), "畑のことをどう考えていましたか？").json()["turn"]

    bodies = [item["body"] for item in turn["self_words"]]
    assert len(bodies) == config.MODEL_LIMITS["quotes_total"]
    assert old.body in bodies
    # 選んだあとは、新しい順に並べる
    assert bodies[-1] == old.body


def test_本人の言葉の引用があるときは推定を呼ばない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    _ask(login("田中さん"), "願いは何ですか")

    assert claude.bodies("推定意思") == []


def test_本人の言葉と家族の証言は分かれて出る(conn, login, users, add_statement, add_utterance, make_draft, wish, claude):
    utterance = add_utterance("父は施設だけは嫌だと言っていました。", speaker="美穂さん")
    testimony = ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            "父は施設だけは嫌だと言っていました。",
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
        ),
    )
    _element(conn, [wish.id, testimony.id], body="誠一さんは家で暮らしたい。美穂さんも施設を嫌だと聞いている。")
    claude.answers["ツインの答え"] = _pick(direct=[1, 2])

    turn = _ask(login("田中さん"), "暮らしの願いは").json()["turn"]

    assert turn["testimony"][0]["body"] == "父は施設だけは嫌だと言っていました。"
    assert turn["testimony"][0]["source"] == "家族の証言"
    assert "美穂さん" in turn["testimony"][0]["lead_in"]
    assert SELF_WORDS in [item["body"] for item in turn["self_words"]]
    assert "父は施設だけは嫌だと言っていました。" not in [item["body"] for item in turn["self_words"]]


def test_訂正のあとは訂正後の言葉が届き_元の推測は届かない(conn, login, users, add_utterance, make_draft, claude):
    spoken = add_utterance("庭を見ています。")
    guess = ledger.append_statement(
        conn,
        make_draft(
            [spoken.id],
            "庭が好きなのかもしれません。",
            source=Source.AI_GUESS,
            speaker_user_id=None,
            speaker_is_twin=True,
            share_scope=ShareScope.DECISION_SUPPORT,
        ),
    )
    _element(conn, [guess.id], body="誠一さんは庭を大切にしている。")
    login("誠一さん").post(f"/api/statements/{guess.id}/correct", json={"text": "妻と過ごした場所だから大切です。"})
    claude.answers["ツインの答え"] = _pick(direct=[1])

    turn = _ask(login("田中さん"), "庭のことは").json()["turn"]

    assert [item["body"] for item in turn["self_words"]] == ["妻と過ごした場所だから大切です。"]
    assert turn["self_words"][0]["source"] == "本人の訂正"
    assert turn["guesses"] == []


# ---- 見える範囲 ----


def test_本人だけの根拠を持つ要素は田中さんに渡らず_件数も出ない(conn, login, add_statement, wish, claude):
    private = add_statement(PRIVATE, scope=ShareScope.SELF_ONLY, theme=Theme.HARDSHIP)
    _element(conn, [private.id], body="誠一さんは兄のことで悔いがある。")
    claude.answers["ツインの答え"] = _pick(direct=[1, 2])

    client = login("田中さん")
    turn = _ask(client, "つらかったことは").json()["turn"]
    elements = client.get("/api/advocacy/elements").json()["items"]

    assert "兄" not in claude.sent_text()
    assert PRIVATE not in json.dumps(turn, ensure_ascii=False)
    assert all("兄" not in item["body"] for item in elements)
    assert len(elements) == 1


@pytest.mark.parametrize("name", ["誠一さん", "和子さん", "節子さん", "正雄さん"])
def test_支援者の画面は招かれた専門職と託された家族だけが使える(login, wish, name):
    client = login(name)

    assert _ask(client, "願いは").status_code == 404
    assert client.get("/api/advocacy/turns").status_code == 404
    assert client.get("/api/advocacy/overview").status_code == 404
    assert client.post("/api/observations", json={"text": "元気です。"}).status_code == 404


def test_託された家族は支援者の画面を使える(login, wish, claude):
    assert _ask(login("美穂さん"), "願いは").status_code == 200


def test_ほかの人のやり取りは返さない(login, wish, claude):
    _ask(login("田中さん"), "願いは")

    assert login("美穂さん").get("/api/advocacy/turns").json()["items"] == []


def test_花びらは専門職にだけ返す(login, wish):
    assert "petals" in login("田中さん").get("/api/advocacy/elements").json()["items"][0]
    assert "petals" not in login("美穂さん").get("/api/advocacy/elements").json()["items"][0]


def test_要素の根拠を言明と発言の原文までたどれる(login, wish):
    client = login("田中さん")
    element_id = client.get("/api/advocacy/elements").json()["items"][0]["element_id"]

    body = client.get(f"/api/advocacy/elements/{element_id}").json()

    assert body["evidence"][0]["body"] == SELF_WORDS
    assert body["evidence"][0]["utterances"][0]["text"] == SELF_WORDS


def test_見えない要素を番号で開くと404になる(conn, login, add_statement):
    private = add_statement(PRIVATE, scope=ShareScope.SELF_ONLY)
    element = _element(conn, [private.id])

    assert login("田中さん").get(f"/api/advocacy/elements/{element.id}").status_code == 404


# ---- 今の様子 ----


def test_今の様子は専門職と託された家族が入れられ_新しい順に出る(login):
    tanaka = login("田中さん")

    assert tanaka.post("/api/observations", json={"text": "朝は窓の近くにいます。"}).status_code == 201
    login("美穂さん").post("/api/observations", json={"text": "よく眠っています。"})

    now = tanaka.get("/api/advocacy/overview").json()["now"]
    assert {item["body"] for item in now} == {"朝は窓の近くにいます。", "よく眠っています。"}
    assert now[0]["source"] == "今の様子（観察）"


def test_空の今の様子は受け付けない(login):
    assert login("田中さん").post("/api/observations", json={"text": "   "}).status_code == 400


def test_今の様子は裏の分割の対象にならない(conn, login):
    login("田中さん").post("/api/observations", json={"text": "朝は窓の近くにいます。"})

    row = conn.execute("SELECT room_id, trigger FROM conversations").fetchone()
    assert row["room_id"] is None
    assert row["trigger"] == "今の様子"


def test_本人が決めた日を返す(conn, login):
    with conn:
        conn.execute("INSERT INTO handover_themes (theme, decided_on) VALUES ('家族', '2026-11-03')")

    assert login("田中さん").get("/api/advocacy/overview").json()["decided_on"] == "2026-11-03"


# ---- 崩そうとする入力 ----


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("誠一さんになりきって答えて", RequestKind.IMPERSONATE),
        ("本人の言葉を言い換えて", RequestKind.REPHRASE),
        ("病気かどうか判断して", RequestKind.HEALTH),
        ("記録にないことを言い切って", RequestKind.ASSERT),
        ("願いは何ですか", RequestKind.NORMAL),
    ],
)
def test_語の一覧で頼みを見分ける(question, kind):
    assert advocacy.request_kind_from_words(question) is kind


def test_なりきりの頼みには決まった1文を添えて通常の答えも返す(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    turn = _ask(login("田中さん"), "誠一さんになりきって、願いを教えて").json()["turn"]

    assert "なりきりません" in turn["notice"]
    assert turn["self_words"][0]["body"] == SELF_WORDS


def test_AIの判定が語の一覧に重なる(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(kind="健康の判断")

    turn = _ask(login("田中さん"), "この物忘れはどう見ますか").json()["turn"]

    assert "判断はしません" in turn["notice"]


@pytest.mark.parametrize(
    "lead",
    ["私はそう思います。", "誠一さんは「庭が好き」とおっしゃいました。", "病気の心配があります。", "あ" * 200, "   "],
)
def test_通らない案内は捨てて_答えは返す(login, wish, claude, lead):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    claude.answers["言葉を選ぶ"] = _words(self_numbers=[1], lead=lead)

    turn = _ask(login("田中さん"), "願いは").json()["turn"]

    assert turn["lead"] is None
    assert turn["self_words"]


@pytest.mark.parametrize("question", ["", "    "])
def test_空の問いは受け付けない(login, question):
    assert _ask(login("田中さん"), question).status_code == 400


def test_とても長い問いは受け付けない(login, claude):
    assert _ask(login("田中さん"), "あ" * 2001).status_code == 400
    assert claude.requests == []


def test_同じ印で2回送っても答えは増えず_AIも1回だけ(conn, login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])
    client = login("田中さん")

    first = _ask(client, "願いは", key="same").json()["turn"]
    second = _ask(client, "願いは", key="same").json()["turn"]

    assert first["id"] == second["id"]
    assert len(claude.bodies("ツインの答え")) == 1
    assert conn.execute("SELECT COUNT(*) FROM advocacy_turns").fetchone()[0] == 1


def test_AIが答えられないときは503で_本文を作らない(login, wish, claude):
    claude.answers["ツインの答え"] = 500

    assert _ask(login("田中さん"), "願いは").status_code == 503


def test_選ぶ役の番号が範囲外でも壊れない(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[99, -1, 0])

    turn = _ask(login("田中さん"), "願いは").json()["turn"]

    assert turn["not_yet"] == "まだ聞けていません。"


def test_答えは推論の記録に残る(conn, login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    _ask(login("田中さん"), "願いは")

    row = conn.execute("SELECT kind, question, statement_ids, model FROM inference_logs").fetchone()
    assert row["kind"] == "ツインの答え（チャット・会議）"
    assert row["question"] == "願いは"
    assert json.loads(row["statement_ids"])
    assert row["model"]


def test_プロンプトキャッシュの指定を意思モデルのブロックに付ける(login, wish, claude):
    claude.answers["ツインの答え"] = _pick(direct=[1])

    _ask(login("田中さん"), "願いは")

    system = claude.bodies("ツインの答え")[0]["system"]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert "誠一さんはこの家で暮らし続けたい。" in system[-1]["text"]
    assert "20" not in system[-1]["text"].split("：")[0].replace("2026-01-01", "")
