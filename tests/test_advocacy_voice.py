"""ツインのアドボカシーのテスト。

役割：ツインが自分の言葉で主張し、根拠の本人の言葉は記録の本文のまま添えられることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）、10（見える範囲）、12（本人になりきらない）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import advocacy_prompts, advocacy_voice, ai, config, ledger, model_store
from app.schemas import Place, AdvocacyDraft, ConsolidationUnit, ElementKind, ElementStatus, Role, ShareScope, Source, Theme
from tests.fake_claude import RoleClaude

HOME = "家におれんようになったら、美穂が決めてよか。俺と同じ後悔はさせたくなか。"
STAY = "和子ば施設に入れて悪かった。俺はこの家から一歩も出らん。"
PRIVATE = "兄貴、俺が悪かった。"
QUESTION = "どうしても家で暮らせなくなったときのことを、誠一さんは何か話していましたか"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _element(conn, evidence, body, year=2033, unit=ConsolidationUnit.YEAR, kind=ElementKind.VALUE):
    element = model_store.add_element(
        conn,
        kind=kind,
        body=body,
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(year, 1, 1),
        evidence_statement_ids=evidence,
        unit=unit,
    )
    conn.commit()
    return element


@pytest.fixture
def words(conn, add_statement):
    """2033年と2034年の、向きの違う本人の言葉。"""
    home = add_statement(HOME, scope=ShareScope.DECISION_SUPPORT, place=Place.DIARY_AND_TALK, theme=Theme.HOME, effective_date=date(2033, 11, 17))
    stay = add_statement(STAY, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2034, 11, 17))
    _element(conn, [home.id], "誠一さんは判断を美穂さんに任せる。", year=2033)
    _element(conn, [stay.id], "誠一さんはこの家を離れない。", year=2034)
    return home, stay


def _turn(client, claude, question=QUESTION, key="k1"):
    claude.answers["ツインの答え"] = {"direct_numbers": [1, 2], "related_numbers": [], "request_kind": "通常"}
    return client.post("/api/advocacy/ask", json={"question": question, "client_key": key}).json()["turn"]


def _advocate(client, turn_id, **body):
    return client.post(f"/api/advocacy/turns/{turn_id}/advocate", json=body)


def _draft(**changes):
    return {
        "claim": "ツインは、誠一さんの本心はこの家で最期まで暮らすことだと考えます。負担を減らす方法は施設だけではありません。",
        "reasons": [{"text": "和子さんを家に帰せなかった後悔があるからです。", "element_numbers": [1], "evidence_numbers": [1]}],
        "objections": [{"number": 2, "answer": "翌年の言葉とも矛盾しません。"}],
        "requests": ["家か施設かの二択にしないでください。"],
        "ask_back": "美穂さんの負担がいちばん重いのはどこですか？",
        **changes,
    }


def _advocate_user_text(claude):
    return claude.bodies("ツインのアドボカシー")[-1]["messages"][0]["content"]


def test_ツインの主張には根拠の本人の言葉が記録の本文のまま添えられる(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["claim"].startswith("ツインは、誠一さんの本心は")
    assert voice["reasons"][0]["text"] == "和子さんを家に帰せなかった後悔があるからです。"
    quote = voice["reasons"][0]["evidence"][0]
    assert quote["body"] == HOME
    assert quote["date"] == "2033-11-17"
    assert quote["source"] == "本人の言葉"
    assert quote["lead_in"] == "2033年11月17日、誠一さんはこうおっしゃいました"


def test_反対の言葉も本文のまま添えられ_ツインの答えと組になる(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    objection = _advocate(client, turn["id"]).json()["voice"]["objections"][0]

    assert objection["quote"]["body"] == STAY
    assert objection["answer"] == "翌年の言葉とも矛盾しません。"


def test_ツインの言い切りは止めない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(claim="誠一さんの本心は、この家で暮らし続けることです。")

    response = _advocate(client, turn["id"])

    assert response.status_code == 200
    assert response.json()["voice"]["claim"] == "誠一さんの本心は、この家で暮らし続けることです。"


@pytest.mark.parametrize(
    "claim",
    [
        "誠一さんは「一歩も出らん」と話しています。",
        "いちばん近いのは28と29です。28の言葉だけで決めないでください。",
        "ツインは、28を、許しとは読みません。",
    ],
)
def test_鉤括弧の文と記録の番号を書いた文は型で止まる(claim):
    with pytest.raises(ValueError):
        AdvocacyDraft(claim=claim)


@pytest.mark.parametrize("claim", ["俺はこの家から出らん。", "私はこの家で暮らしたいです。"])
def test_本人として一人称で代弁してよい(claim):
    assert AdvocacyDraft(claim=claim).claim == claim


def test_日付や回数の数字は止めない():
    assert AdvocacyDraft(claim="2033年の命日に話しました。週に2〜3日から始めてください。5年たっても変わりません。").claim


def test_渡していない番号の根拠は本文にならない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(
        reasons=[{"text": "理由です。", "element_numbers": [1], "evidence_numbers": [99, 0, -1]}],
        objections=[{"number": 99, "answer": "番号のない答えです。"}],
    )

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["reasons"][0]["evidence"] == []
    assert voice["objections"] == []


def test_理由と根拠と反対の言葉は上限までに切る(conn, login, words, add_statement, claude):
    for day in range(1, 6):
        add_statement(f"足した言葉{day}です。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2030, 1, day))
    client = login("田中さん")
    claude.answers["ツインの答え"] = {"direct_numbers": [1, 2], "related_numbers": [], "request_kind": "通常"}
    turn = client.post("/api/advocacy/ask", json={"question": QUESTION, "client_key": "k1"}).json()["turn"]
    claude.answers["ツインのアドボカシー"] = _draft(
        reasons=[{"text": f"理由{number}です。", "evidence_numbers": [1, 2]} for number in range(5)],
        objections=[{"number": 1, "answer": "答えです。"}, {"number": 2, "answer": "答えです。"}, {"number": 1, "answer": "答えです。"}],
    )
    voice = _advocate(client, turn["id"]).json()["voice"]

    assert len(voice["reasons"]) == config.MODEL_LIMITS["advocacy_reasons"]
    assert len(voice["objections"]) == config.MODEL_LIMITS["advocacy_objections"]


def test_理由ごとの根拠は上限までに切る(monkeypatch, login, words, claude):
    monkeypatch.setitem(config.MODEL_LIMITS, "advocacy_quotes_per_reason", 1)
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(reasons=[{"text": "理由です。", "element_numbers": [1], "evidence_numbers": [1, 2]}])

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert len(voice["reasons"][0]["evidence"]) == 1


def test_本人の言葉の本文はAIの文からは作られない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    voice = _advocate(client, turn["id"]).json()["voice"]

    quotes = [item for reason in voice["reasons"] for item in reason["evidence"]] + [item["quote"] for item in voice["objections"]]
    assert {item["body"] for item in quotes} <= {HOME, STAY}


def test_今は話したくないにした記録は材料に入らない(conn, login, users, words, add_statement, claude):
    hidden = add_statement(PRIVATE, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2033, 8, 14))
    ledger.rest_statement(conn, hidden.id, actor_user_id=users["誠一さん"].id)
    conn.commit()
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert PRIVATE not in json.dumps(claude.bodies("ツインのアドボカシー"), ensure_ascii=False)


def test_本人だけの記録は材料に入らない(login, words, add_statement, claude):
    add_statement(PRIVATE, scope=ShareScope.SELF_ONLY, theme=Theme.HOME)
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert PRIVATE not in json.dumps(claude.bodies("ツインのアドボカシー"), ensure_ascii=False)


def test_届けた言葉は要素に選ばれなくても材料に入る(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインの答え"] = {"direct_numbers": [], "related_numbers": [], "request_kind": "通常"}

    _advocate(client, turn["id"])

    text = _advocate_user_text(claude)
    assert HOME in text
    assert STAY in text


def test_記録は古い順に番号つきで渡す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    text = _advocate_user_text(claude)
    assert text.index(HOME) < text.index(STAY)
    assert "[1] 2033-11-17／本人の言葉" in text
    assert "[2] 2034-11-17／本人の言葉" in text


def test_理由ごとに_もとにした意思モデルの要素を返す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)

    claude.answers["ツインのアドボカシー"] = _draft(reasons=[{"text": "理由です。", "element_numbers": [1], "evidence_numbers": []}])

    reason = _advocate(client, turn["id"]).json()["voice"]["reasons"][0]

    assert reason["elements"][0]["body"] == "誠一さんは判断を美穂さんに任せる。"
    assert reason["evidence"] == []


def test_要素を指さない理由があれば1回だけやり直す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = [
        _draft(reasons=[{"text": "理由です。", "element_numbers": [], "evidence_numbers": []}]),
        _draft(reasons=[{"text": "理由です。", "element_numbers": [2], "evidence_numbers": []}]),
    ]

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert len(claude.bodies("ツインのアドボカシー")) == 2
    assert voice["reasons"][0]["elements"][0]["body"] == "誠一さんはこの家を離れない。"


def test_人物像と気持ちの読みを返す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(selfhood="自分で決めてきた人です。", feeling="家を離れる不安があります。")

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["selfhood"] == "自分で決めてきた人です。"
    assert voice["feeling"] == "家を離れる不安があります。"


def test_言葉のAIの読みをAIに渡す(conn, login, words, claude):
    home, _ = words
    ledger.add_interpretation(
        conn, statement_id=home.id, viewpoint_role=Role.SELF, body="娘に背負わせたくないのかもしれません", confidence=0.5, caution=None
    )
    conn.commit()
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    text = _advocate_user_text(claude)
    assert "娘に背負わせたくないのかもしれません" in text


def test_本人だけの言葉の読みはAIに渡らない(conn, add_statement, login, words, claude):
    hidden = add_statement("だれにも言わん話たい。", scope=ShareScope.SELF_ONLY, theme=Theme.HOME, effective_date=date(2034, 1, 1))
    ledger.add_interpretation(
        conn, statement_id=hidden.id, viewpoint_role=Role.SELF, body="隠しておきたい気持ち", confidence=0.5, caution=None
    )
    conn.commit()
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert "隠しておきたい気持ち" not in claude.sent_text()


def test_ツインにだけ話した言葉には印を付けてAIに渡す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert "ツインにだけ話した：" in _advocate_user_text(claude)


def test_今の様子も読む(login, words, claude):
    client = login("田中さん")
    client.post("/api/observations", json={"text": "縁側で庭を見ておられました。"})
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert "縁側で庭を見ておられました。" in _advocate_user_text(claude)


def test_読めていないことを相手に聞く文を返す(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["ask_back"] == "美穂さんの負担がいちばん重いのはどこですか？"
    assert voice["requests"] == ["家か施設かの二択にしないでください。"]


def test_作ってあればAIを呼ばずに返し_作り直すと前の行も残る(conn, login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(claim="1回目の主張です。")
    first = _advocate(client, turn["id"]).json()["voice"]
    calls = len(claude.bodies("ツインのアドボカシー"))

    second = _advocate(client, turn["id"]).json()["voice"]
    assert second["id"] == first["id"]
    assert len(claude.bodies("ツインのアドボカシー")) == calls

    claude.answers["ツインのアドボカシー"] = _draft(claim="2回目の主張です。")
    third = _advocate(client, turn["id"], again=True).json()["voice"]
    assert third["claim"] == "2回目の主張です。"
    assert conn.execute("SELECT COUNT(*) FROM advocacy_voices").fetchone()[0] == 2


def test_使ったモデルとトークン数と根拠の番号を記録に残す(conn, login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    _advocate(client, turn["id"])

    row = conn.execute(
        "SELECT kind, question, statement_ids, model, input_tokens FROM inference_logs WHERE kind = 'ツインのアドボカシー'"
    ).fetchone()
    assert row["question"] == QUESTION
    assert row["model"]
    assert row["input_tokens"] > 0
    assert len(json.loads(row["statement_ids"])) == 2
    calls = conn.execute("SELECT COUNT(*) FROM ai_calls WHERE role = 'ツインのアドボカシー'").fetchone()[0]
    assert calls == 1


def test_託されていない人には見えない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)

    assert _advocate(login("節子さん"), turn["id"]).status_code == 404


def test_ほかの人の問いには呼べない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)

    assert _advocate(login("美穂さん"), turn["id"]).status_code == 404


def test_AIに届かないときは分かる言葉で返し_記録を増やさない(conn, login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = {"claim": "誠一さんは「帰りたい」と話しています。"}

    response = _advocate(client, turn["id"])

    assert response.status_code == 503
    assert conn.execute("SELECT COUNT(*) FROM advocacy_voices").fetchone()[0] == 0


def test_年と人生の段階の要素を古い順に読み_どの年も落とさない(conn, add_statement, monkeypatch):
    monkeypatch.setitem(config.MODEL_LIMITS, "advocacy_element_chars", 400)
    statement = add_statement("言葉です。", scope=ShareScope.DECISION_SUPPORT)
    elements = []
    for year in (2036, 2026, 2031):
        for number in range(6):
            elements.append(_element(conn, [statement.id], f"{year}年の要素{number}です。" + "長" * 30, year=year))
    elements.append(_element(conn, [statement.id], "月の要素です。", year=2030, unit=ConsolidationUnit.MONTH))

    picked = model_store.for_advocacy(elements, ledger.head_map(conn))

    years = [item.valid_from.year for item in picked]
    assert years == sorted(years)
    assert {2026, 2031, 2036} <= set(years)
    assert all(item.unit is not ConsolidationUnit.MONTH for item in picked)


def test_上の段の要素がまだなければ月の要素を読む(conn, add_statement):
    statement = add_statement("言葉です。", scope=ShareScope.DECISION_SUPPORT)
    month = _element(conn, [statement.id], "月の要素です。", unit=ConsolidationUnit.MONTH)

    assert [item.id for item in model_store.for_advocacy([month], ledger.head_map(conn))] == [month.id]


def test_ツインの決まりには_診断と服薬の指示と説得の手口を書かないとある():
    system = advocacy_prompts.voice_system("誠一さん")

    assert "病気の診断と、服薬の指示はしません" in system
    assert "医療や健康の助言に見えることを書きません" not in system
    assert "説得する言い方や手口を書きません" in system
    assert "自分の意思で決められるように" in system


def test_ツインの考えの画面の部品を配る(login):
    assert login("田中さん").get("/voice.js").status_code == 200


def test_証言は本人の言葉と別の出どころのまま渡す(conn, login, users, add_utterance, make_draft, words, claude):
    utterance = add_utterance("父は、この家から絶対に動かないと言っています。", speaker="美穂さん")
    ledger.append_statement(
        conn,
        make_draft(
            [utterance.id],
            "父は、この家から絶対に動かないと言っています。",
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
            theme=Theme.HOME,
            effective_date=date(2035, 12, 16),
        ),
    )
    conn.commit()
    client = login("田中さん")
    turn = _turn(client, claude)

    _advocate(client, turn["id"])

    assert "2035-12-16／家族の証言／美穂さん" in _advocate_user_text(claude)


# ---- ガードレール ----


def test_ツインの考えに決める人の文と読んだ記録の期間と件数を添える(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["decide"] == "決めるのは、ご本人とご家族と専門職です。"
    assert voice["reading"] == "読んだ記録：意思モデルの要素2件（2033年〜2034年）、言葉2件（2033年11月17日〜2034年11月17日）"
    # ツインの文は変えない
    assert voice["claim"] == _draft()["claim"]


def test_保存したツインの考えを読み直しても_読んだ記録の文は同じでAIを呼ばない(login, words, claude):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft()
    first = _advocate(client, turn["id"]).json()["voice"]
    called = len(claude.bodies("ツインのアドボカシー"))

    second = _advocate(client, turn["id"]).json()["voice"]

    assert second["reading"] == first["reading"]
    assert len(claude.bodies("ツインのアドボカシー")) == called


def test_ツインの文に服薬の指示があればやり直す(conn):
    good = AdvocacyDraft(**_draft())
    bad = AdvocacyDraft(**_draft(claim="ツインは、避難中は飲まなくても大丈夫ですと考えます。"))
    answers = [bad, good]
    extras = []

    def call(extra):
        extras.append(extra)
        return ai.AIResult(value=answers.pop(0), model=config.MODEL_BY_ROLE["ツインのアドボカシー"], input_tokens=1, output_tokens=1)

    result = advocacy_voice.ask_without_medical_orders(conn, call, "ツインのアドボカシー", "試し")

    assert result.value is good
    assert extras[0] == ""
    assert "「飲まなくても大丈夫です」のように、病気の診断や服薬の指示を書かないでください" in extras[1]


@pytest.mark.parametrize("claim", ["ツインは、血圧が高い方ですねと見ます。", "ツインは、先に診てもらう必要がありますと考えます。", "ツインは、〇〇錠のことですねと考えます。"])
def test_やり直しても診断か服薬の指示が残ればツインの考えを出さない(login, words, claude, claim):
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(claim=claim)

    response = _advocate(client, turn["id"])

    assert response.status_code == 503
    assert len(claude.bodies("ツインのアドボカシー")) == 2


@pytest.mark.parametrize("word", ["減塩", "血圧", "栄養"])
def test_健康の話題の語だけではやり直さず_ツインの考えを出す(login, words, claude, word):
    # 原則17：一般的な健康の助言はしてよい。話題の語では止めない
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(claim=f"ツインは、{word}に気をつけるとよいと考えます。")

    response = _advocate(client, turn["id"])

    assert response.status_code == 200
    assert len(claude.bodies("ツインのアドボカシー")) == 1


def test_本人が話した血圧の言葉を引いてもやり直さない(conn, login, add_statement, claude):
    said = "血圧は百四十の八十たい。"
    number = add_statement(said, scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME, effective_date=date(2033, 11, 17))
    _element(conn, [number.id], "誠一さんは自分の体の数字を覚えている。", year=2033)
    client = login("田中さん")
    turn = _turn(client, claude)
    claude.answers["ツインのアドボカシー"] = _draft(reasons=[{"text": "自分で数字を覚えています。", "element_numbers": [1], "evidence_numbers": [1]}], objections=[])

    voice = _advocate(client, turn["id"]).json()["voice"]

    assert voice["reasons"][0]["evidence"][0]["body"] == said
    assert len(claude.bodies("ツインのアドボカシー")) == 1


def test_本人の言葉だけを本人の意向の欄へ選ぶ():
    quotes = [
        {"statement_id": 1, "source": "家族の証言", "body": "父は家が好きです。"},
        {"statement_id": 2, "source": "本人の言葉", "body": "この家におる。"},
        {"statement_id": 2, "source": "本人の言葉", "body": "この家におる。"},
        {"statement_id": 3, "source": "本人の訂正", "body": "庭は妻の場所だ。"},
        {"statement_id": 4, "source": "本人の言葉", "body": "三つ目は載せない。"},
    ]

    assert [item["statement_id"] for item in advocacy_voice.own_words(quotes)] == [2, 3]
