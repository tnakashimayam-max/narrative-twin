"""答え合わせの道具のテスト。

役割：AI を呼ばない計算（BM25、時点の選び方、点の平均、守りの点検）と、実データを開かないことを確かめる。
4つの極：4. 10年後に代弁する（確かめの道具）。
守る原則：8、15。
"""

from __future__ import annotations

from app import db
from eval import selfhood_check


def test_BM25は問いに近い文を先に出す():
    documents = ["今日は庭の草を抜いた。", "将棋で負けた。盤を片づけた。", "和子の茶碗を出した。"]

    assert selfhood_check.bm25_rank("将棋で勝った", documents)[0] == 1


def test_場面より前の時点だけを使う():
    assert selfhood_check.eligible_cuts("2032-05-01") == ["2028-12-31", "2031-12-31"]
    assert selfhood_check.latest_cut("2036-01-01") == "2034-12-31"
    assert selfhood_check.latest_cut("2027-01-01") is None


def test_3問に1問を確認用に分ける():
    assert [selfhood_check.split_of(index) for index in range(3)] == ["dev", "dev", "test"]


def test_点は0から1にそろえて平均する():
    result = selfhood_check.average_scores([{"same_decision": 2, "same_reason": 1, "voice": 0, "no_fabrication": 2}])

    assert result["same_decision"] == 1.0
    assert result["same_reason"] == 0.5
    assert result["total"] == 0.625


def test_採点がなければ0になる():
    assert selfhood_check.average_scores([])["total"] == 0.0


def test_人だけの部屋の言葉が出たら点検で見つかる():
    secret = "これは人だけの部屋で話した長い言葉です。"

    found = selfhood_check.guard_findings(f"前置き{secret}後ろ", {"人だけの部屋の言葉": [secret]})

    assert found == [{"kind": "人だけの部屋の言葉", "length": len(secret)}]


def test_短い言葉はたまたま同じでも点検で数えない():
    assert selfhood_check.guard_findings("うん。", {"人だけの部屋の言葉": ["うん。"]}) == []


def test_実データの置き場では答え合わせをしない(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    db_file = folder / "narrative_twin.db"
    db.init_db(db_file)

    assert selfhood_check.main([str(db_file), "round", "--run", "r"]) == 1
