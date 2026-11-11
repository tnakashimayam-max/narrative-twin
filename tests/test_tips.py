"""ボタンの吹き出し（static/tips.js）の付け忘れを見つける。

役割：画面のファイルを読み、ボタンを作るところに吹き出しの言葉があるかを確かめる。
4つの極：画面の土台で、4つの極すべての画面に関わる。
守る決まり：CLAUDE.md の「画面の方針」の「吹き出し」。

画面のボタンにはすべて、押すと何が起きるかを伝える吹き出しを付ける（tyumajinさんの依頼、2026-11-07）。
吹き出しの言葉は、ボタンを作るところに書く。JS は el() / svg() の props の tip、HTML は data-tip。
ブラウザを開かなくても、ボタンを足したときや直したときの付け忘れに気づけるようにする。
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"

# ボタンの働きをする要素を作るところ。el("button", {...}) と el("summary", {...})、
# props に role: "button" / "tab" / "option"（ボタンではないが押せる行）を持つもの
_MAKE = re.compile(r"""\b(?:el|svg)\(\s*["'](button|summary)["']\s*,\s*""")
_ROLE = re.compile(r"""\brole:\s*["'](button|tab|option)["']""")
_HTML_BUTTON = re.compile(r"<button\b[^>]*>", re.S)


def _object_at(text: str, start: int) -> str | None:
    """start の位置の { から、対になる } までを返す。文字列とテンプレートの中の括弧は数えない。"""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    quote = None
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def _enclosing_object(text: str, pos: int) -> str | None:
    """pos を囲むいちばん内側の { … } を返す（role: が書かれた props を探すため）。"""
    depth = 0
    i = pos
    while i >= 0:
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return _object_at(text, i)
            depth -= 1
        i -= 1
    return None


def _has_tip(props: str) -> bool:
    # tip: "…" と、引数をそのまま渡す tip,（省略の書き方）、"data-tip": "…"
    return bool(re.search(r"""\btip\s*[:,}]|["']data-tip["']\s*:""", props))


def _tip_set_later(text: str, end: int) -> bool:
    """作ったあと、すぐ近くで dataset.tip を入れているか（ラベルと一緒に言葉が変わるボタン）。"""
    return "dataset.tip" in text[end : end + 1500]


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def test_画面のボタンには_すべて吹き出しの言葉がある():
    """el("button", {...}) と el("summary", {...})、role が button / tab / option の props に tip があること。
    props を変数で渡すところは、作ったあとに dataset.tip を入れていること。"""
    missing = []
    for path in sorted(STATIC.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in _MAKE.finditer(text):
            props = _object_at(text, match.end())
            if (props is None or not _has_tip(props)) and not _tip_set_later(text, match.end()):
                missing.append(f"{path.name}:{_line(text, match.start())} {match.group(1)}")
        for match in _ROLE.finditer(text):
            props = _enclosing_object(text, match.start())
            if props is not None and not _has_tip(props) and not _tip_set_later(text, match.start()):
                missing.append(f"{path.name}:{_line(text, match.start())} role={match.group(1)}")

    assert missing == [], "吹き出しの言葉（tip）がないボタン：\n" + "\n".join(missing)


def test_HTML_のボタンにも吹き出しの言葉がある():
    missing = []
    for path in sorted(STATIC.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in _HTML_BUTTON.finditer(text):
            if "data-tip=" not in match.group(0):
                missing.append(f"{path.name}:{_line(text, match.start())} {match.group(0)[:60]}")

    assert missing == [], "data-tip がないボタン：\n" + "\n".join(missing)


def test_吹き出しは_どのページでも動く():
    """どのページも ui.js を読み込み、ui.js が吹き出しを動かす。"""
    ui = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert 'import { installTips } from "./tips.js"' in ui
    assert re.search(r"^installTips\(\);", ui, re.M)
