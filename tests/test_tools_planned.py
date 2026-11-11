"""ツールの画面の「準備中」のボタンのテスト。

役割：ケアマネジャーのツールの3枚のカードに、準備中のツールが1つずつあり、押しても何も書き込まないことを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：1（判断するのは人）。画面の方針の「吹き出し」（押すと何が起きるかを1文で書く）。

準備中のツールは、ハッカソンのあとに増やす予定のツール（tyumajinさんの依頼、2026-11-08）。
ブラウザを開かずに、static/tools.js の書き方を読んで確かめる。
"""

import re
from pathlib import Path

TOOLS_JS = Path(__file__).resolve().parent.parent / "static" / "tools.js"

# カードの id と、準備中のツールの名前（tyumajinさんの決定）
PLANNED = {
    "voice": "想い出「回想法」",
    "plan": "会議の記録をチェックする",
    "letter": "わたしのことシートを作る",
}


def _planned_blocks(text: str) -> dict[str, str]:
    """TOOL_WORDS の各カードの planned: { … } を、カードの id ごとに取り出す。"""
    blocks = {}
    for tool_id in PLANNED:
        start = re.search(rf"^  {tool_id}: \{{", text, re.M)
        assert start, f"TOOL_WORDS に {tool_id} がない"
        planned = re.search(r"planned: \{(.*?)\n    \},", text[start.end() :], re.S)
        assert planned, f"{tool_id} のカードに準備中のツールがない"
        blocks[tool_id] = planned.group(1)
    return blocks


def test_3枚のカードに準備中のツールが1つずつある():
    blocks = _planned_blocks(TOOLS_JS.read_text(encoding="utf-8"))
    for tool_id, name in PLANNED.items():
        assert f'text: "{name}"' in blocks[tool_id]


def test_準備中のツールの吹き出しは_まだ使えないことと押すと出るものを書く():
    blocks = _planned_blocks(TOOLS_JS.read_text(encoding="utf-8"))
    for tool_id, block in blocks.items():
        tip = re.search(r'tip: "([^"]+)"', block)
        assert tip, f"{tool_id} の準備中のツールに吹き出しがない"
        assert tip.group(1).startswith("まだ使えません。")
        assert "カードの下で示します" in tip.group(1)
        # できる予定のことを出す短い説明があり、ツインと分かる（原則12）
        note = re.search(r'note: "([^"]+)"', block)
        assert note and "ツイン（AI）" in note.group(1)


def test_準備中のツールの説明は_範囲の行の下に同じ文字で出す():
    text = TOOLS_JS.read_text(encoding="utf-8")
    # 「見えるのは、〜が決めた範囲です。」（scope）のすぐ下に置く
    assert re.search(r"\bscope,\s*\n\s*plannedList\(tools\)", text)
    body = re.search(r"function plannedList\(.*?\n\}", text, re.S)
    assert body, "plannedList がない"
    # 範囲の行と同じ .hint で出し、準備中と分かる見出しを付ける
    assert body.group(0).count('class: "hint"') == 2
    assert "準備中のツール（" in body.group(0)


def test_準備中のボタンは書き込みもAIも呼ばない():
    text = TOOLS_JS.read_text(encoding="utf-8")
    body = re.search(r"function plannedTool\(.*?\n\}", text, re.S)
    assert body, "plannedTool がない"
    # サーバーを呼ばない（説明を開け閉めするだけ）
    assert "api(" not in body.group(0)
    assert "openTab" not in body.group(0)
