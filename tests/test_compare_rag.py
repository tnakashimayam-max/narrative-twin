"""今の方式と BM25 の RAG を比べる道具のテスト。

役割：変えるのが本人の言葉の候補の作り方だけであること、見える範囲が同じであること、数え方が正しいことを確かめる。
4つの極：4. 10年後に代弁する（比べるためだけの道具）。
守る原則：9（届く本文は記録の本文）、10（探す先は同意の絞り込みのあと）。

本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app import advocacy, ai, db, ledger, model_store
from app.schemas import ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Source, Theme, WordPick
from scripts import compare_rag
from scripts.compare_rag import ARM_BM25, ARM_BM25_TOP, ARM_HYBRID, ARM_MODEL
from tests.fake_claude import RoleClaude


def _element(conn, evidence, body="誠一さんはこの家で暮らし続けたい。"):
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
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    return element


@pytest.fixture
def world(conn, users, add_statement, add_utterance, make_draft):
    """田中さんに見える言葉と見えない言葉を入れて開く。"""
    garden = add_statement("この家で庭を見ていたい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HOME)
    add_statement("風呂は一人で入りたいんだ。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HELP)
    add_statement("風呂の話は誰にもしたくない。", scope=ShareScope.SELF_ONLY, theme=Theme.HELP)
    testimony = add_utterance("風呂が怖いと言っていました。", speaker="美穂さん")
    ledger.append_statement(
        conn,
        make_draft(
            [testimony.id],
            "風呂が怖いと言っていました。",
            source=Source.FAMILY_TESTIMONY,
            speaker_user_id=users["美穂さん"].id,
            share_scope=ShareScope.DECISION_SUPPORT,
            theme=Theme.HELP,
        ),
    )
    _element(conn, [garden.id])
    return compare_rag.open_world(conn, users["田中さん"])


def test_BM25の候補に本人だけの言葉は入らない(world):
    bodies = [item.body for item in compare_rag.bm25_hits(world, "風呂について", 30)]

    assert "風呂は一人で入りたいんだ。" in bodies
    assert "風呂の話は誰にもしたくない。" not in bodies


def test_BM25の候補に証言は入らず_本人の言葉だけを探す(world):
    assert all(item.source is Source.SELF_WORDS for item in compare_rag.bm25_hits(world, "風呂について", 30))


def test_BM25の候補にお休みの言葉は入らない(conn, users, add_statement):
    words = add_statement("風呂は静かに入りたい。", scope=ShareScope.DECISION_SUPPORT, theme=Theme.HELP)
    ledger.rest_statement(conn, words.id, actor_user_id=users["誠一さん"].id)

    world = compare_rag.open_world(conn, users["田中さん"])

    assert compare_rag.bm25_hits(world, "風呂について", 30) == []


def test_腕を変えても_本人の言葉でない候補は同じ(world):
    base = compare_rag.bm25_pool(world, advocacy.WordPool([], [], [], []), "風呂")
    others = [item for item in base.statements if item.source not in advocacy._SELF_SOURCES]
    assert others == []
    reference = advocacy._build_word_pool(world.conn, world.viewer, [], [], world.heads, world.scopes, "風呂")

    for pool in (
        compare_rag.bm25_pool(world, reference, "風呂"),
        compare_rag.bm25_caution_pool(world, reference, "風呂"),
        compare_rag.hybrid_pool(world, reference, "風呂"),
    ):
        assert compare_rag.others_of(pool) == compare_rag.others_of(reference)


def test_BM25の候補は注意文が出ない節に_注意文ありの腕は出る節に置く(world):
    reference = advocacy.WordPool([], [], [], [])

    assert compare_rag.bm25_pool(world, reference, "風呂").direct
    assert compare_rag.bm25_caution_pool(world, reference, "風呂").direct == []
    assert compare_rag.bm25_caution_pool(world, reference, "風呂").related


def test_足す腕でも本人の言葉の候補の数は上限を超えない(conn, users, add_statement, monkeypatch):
    monkeypatch.setattr(compare_rag, "SELF_LIMIT", 4)
    monkeypatch.setattr(compare_rag, "HYBRID_RESERVE", 2)
    evidence = [add_statement(f"庭の話の{number}です。", scope=ShareScope.DECISION_SUPPORT).id for number in range(4)]
    add_statement("風呂は一人で入りたいんだ。", scope=ShareScope.DECISION_SUPPORT)
    add_statement("風呂は朝に入るんだ。", scope=ShareScope.DECISION_SUPPORT)
    world = compare_rag.open_world(conn, users["田中さん"])
    element = world.reading[0] if world.reading else _element(conn, evidence)
    base = advocacy._build_word_pool(conn, world.viewer, [element], [], world.heads, world.scopes, "風呂")

    pool = compare_rag.hybrid_pool(world, base, "風呂について")

    bodies = [item.body for item in pool.statements if item.source in advocacy._SELF_SOURCES]
    assert len(bodies) <= 4
    assert "風呂は一人で入りたいんだ。" in bodies


def test_足す腕は_今の方式の候補の番号と本文の並びを保つ(world):
    reference = advocacy._build_word_pool(world.conn, world.viewer, world.reading, [], world.heads, world.scopes, "風呂")

    pool = compare_rag.hybrid_pool(world, reference, "風呂")

    # 通し番号の行と言明の並びが合っている。合わないと、番号が別の言葉を指す
    lines = [*pool.direct, *pool.related, *pool.others]
    assert len(lines) == len(pool.statements)
    for number, (line, item) in enumerate(zip(lines, pool.statements, strict=True), start=1):
        assert line.startswith(f"[{number}]")
        assert line.endswith(item.body)


def test_選んだ番号から届く本文は記録の本文のまま(world):
    reference = advocacy.WordPool([], [], [], [])
    pool = compare_rag.bm25_pool(world, reference, "風呂について")

    picked = advocacy._chosen_statements(WordPick(self_numbers=[1], testimony_numbers=[], guess_numbers=[], lead=None), pool)

    assert compare_rag.delivered(world, picked)["self_words"] == ["風呂は一人で入りたいんだ。"]


def _pick_result(direct):
    return SimpleNamespace(
        value=SimpleNamespace(direct_numbers=direct, related_numbers=[]),
        model="claude-sonnet-5-5",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        seconds=1.0,
    )


def _word_result(numbers):
    return SimpleNamespace(
        value=WordPick(self_numbers=numbers, testimony_numbers=[], guess_numbers=[], lead=None),
        model="claude-sonnet-5-5",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        seconds=1.0,
    )


def test_今の方式の腕は_アプリの答えと同じ言葉を届ける(world, monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    fake.answers["言葉を選ぶ"] = {"self_numbers": [1], "testimony_numbers": [], "guess_numbers": [], "lead": None}

    view = advocacy.answer(world.conn, world.viewer, "庭について")["view"]
    row = compare_rag.run_question(
        world,
        "庭について",
        _pick_result([1]),
        lambda _q, pools: {arm: (_word_result([1]) if pool.statements else None) for arm, pool in pools.items()},
    )

    assert view["self_words"], "テストの前提：言葉が届く"
    assert row["arms"][ARM_MODEL]["self_words"] == [item["body"] for item in view["self_words"]]


def test_要素が選ばれないと今の方式はAIを呼ばず何も届かない(world):
    called = {}

    def runner(_question, pools):
        called.update(pools)
        return {arm: (None if not pool.statements else _word_result([1])) for arm, pool in pools.items()}

    row = compare_rag.run_question(world, "風呂について", _pick_result([]), runner)

    assert row["arms"][ARM_MODEL]["self_words"] == []
    assert row["arms"][ARM_BM25]["self_words"] == ["風呂は一人で入りたいんだ。"]


def test_選ぶ係なしの腕はAIを呼ばず上位をそのまま届ける(world):
    row = compare_rag.run_question(world, "風呂について", _pick_result([]), lambda _q, pools: dict.fromkeys(pools))

    assert row["arms"][ARM_BM25_TOP]["self_words"] == ["風呂は一人で入りたいんだ。"]
    assert row["arms"][ARM_BM25_TOP]["called"] is False


def test_AIの失敗は腕ごとに記録し_ほかの腕を止めない(world):
    def runner(_question, pools):
        return {arm: (RuntimeError("失敗") if arm == ARM_BM25 else None) for arm in pools}

    row = compare_rag.run_question(world, "風呂について", _pick_result([1]), runner)

    assert row["arms"][ARM_BM25]["error"] == "RuntimeError"
    assert row["arms"][ARM_BM25]["self_words"] == []
    assert "error" not in row["arms"][ARM_HYBRID]


def test_届いた割合と正解の割合を数える():
    assert compare_rag.recall(["a", "b"], ["a", "c"]) == 0.5
    assert compare_rag.recall(["a"], []) is None
    assert compare_rag.precision(["a", "b"], ["a"]) == 0.5
    assert compare_rag.precision([], ["a"]) is None


def test_上限で割った割合は_正解が上限より多くても全部当てれば10割になる():
    gold = [str(number) for number in range(40)]

    assert compare_rag.capped_recall(gold[:8], gold, 8) == 1.0
    assert compare_rag.capped_recall(gold[:4], gold, 8) == 0.5
    assert compare_rag.capped_recall(["0"], ["0"], 8) == 1.0
    assert compare_rag.capped_recall(["0"], [], 8) is None


def test_正解が2つの年にまたがるときだけ_新しい言葉が届いたかを数える():
    dates = {"古い": "2027-01-01", "新しい": "2033-01-01"}

    assert compare_rag.latest_delivered(["新しい"], dates) is True
    assert compare_rag.latest_delivered(["古い"], dates) is False
    assert compare_rag.latest_delivered(["古い"], {"古い": "2027-01-01", "同じ年": "2027-06-01"}) is None


def test_判定には腕の名前を出さず_同じ言葉の組は1つにまとめる():
    options, mapping = compare_rag.judge_options({ARM_MODEL: ["a", "b"], ARM_BM25: ["b", "a"], ARM_HYBRID: ["c"], ARM_BM25_TOP: []}, 1)

    assert len(options) == 2
    assert mapping[ARM_MODEL] == mapping[ARM_BM25]
    assert ARM_BM25_TOP not in mapping
    assert all(ARM_MODEL not in label and ARM_BM25 not in label for label, _ in options)


def test_同じ種からは同じ言葉を抜く(world):
    first = compare_rag.draw_sample(world.self_statements, 1, 7)
    second = compare_rag.draw_sample(world.self_statements, 1, 7)

    assert [item.id for item in first] == [item.id for item in second]


def test_短い言葉は抜く先に入れない(conn, users, add_statement):
    add_statement("はい。", scope=ShareScope.DECISION_SUPPORT)
    long_words = "この家で最後まで庭を見ていたいと思います。"
    add_statement(long_words, scope=ShareScope.DECISION_SUPPORT)
    world = compare_rag.open_world(conn, users["田中さん"])

    assert [item.body for item in compare_rag.draw_sample(world.self_statements, 5, 1)] == [long_words]


def test_田中さんは見える言葉を持つ(world):
    assert db.self_user(world.conn).name == "誠一さん"
    assert world.viewer.name == "田中さん"
