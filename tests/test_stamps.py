"""スタンプの扱いのテスト。

役割：スタンプだけの発言を AI の材料と問いへの答えにしないこと、ツインに渡す決まりを確かめる。
4つの極：1. 書く・話す。2. 分かち合う。
守る原則：4（推測を事実として書かない）、14（スレッドのツインは問いを1つ書くだけ）。
"""

from __future__ import annotations

from app import prompts, twin


def test_スタンプだけの発言を見分ける():
    assert twin.stamps_only("（スタンプ：太陽）")
    assert twin.stamps_only("（スタンプ：星）（スタンプ：花） （スタンプ：月）")
    assert not twin.stamps_only("（スタンプ：太陽）今日は晴れた")
    assert not twin.stamps_only("")
    assert not twin.stamps_only("（スタンプ：雲）")


def test_ツインの決まりにスタンプから天気や気持ちを決めない文がある():
    for system in (prompts.twin_system("誠一さん"), prompts.twin_closing_system("誠一さん")):
        assert "スタンプから、天気、時間、体調、気持ちを決めません" in system


def test_スタンプだけの発言で話を閉じない決まりがある():
    assert "のように話を閉じません" in prompts.twin_system("誠一さん")
