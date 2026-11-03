"""実行結果の見本を docs/examples.md に書き出す。

役割：開発のログ（logs/fictional.jsonl）から、役割ごとの実際の入力と出力を選び、手を加えずに書き出す。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のログだけを読む。自分用のログは読まない）。

見本を手で整えると、実際の出力と食い違うので、ログの文字をそのまま写す。
どの行を選ぶかだけを、下の EXAMPLES で決める。入力か答えに含まれる語で選ぶ。

使い方：.venv\\Scripts\\python -m scripts.make_examples --since 2026-11-03T15:00
関連するテスト：tests/test_make_examples.py
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "fictional.jsonl"
OUT = ROOT / "docs" / "examples.md"
_MAX_CHARS = 6000


@dataclass(frozen=True)
class Example:
    """見本の1件。"""

    title: str
    op: str
    words: tuple[str, ...] = ()
    note: str = ""


EXAMPLES = (
    Example("ツインの返事", "AI：ツインの会話", note="本人の発言に受け止めと問いを返す。問いは1回に1つ。"),
    Example("言明への分割", "AI：言明への分割", note="発言を言明に分け、出どころ、花びら、4つの視点を付ける。本人の言葉の一字一句はプログラムが確かめる。"),
    Example("固める処理（月）", "AI：固める処理（月）", note="月の言明から意思モデルの要素を作る。根拠は入力の番号で受け取る。"),
    Example("固める処理（年と人生の段階）", "AI：固める処理", note="下の段の要素から、年と人生の段階の要素を作る。"),
    Example(
        "田中さんへの回答（要素を選ぶ）",
        "AI：ツインの答え",
        words=("暮らし",),
        note="入力は問いだけで、意思モデルはキャッシュのブロックにある。AI は番号を選ぶだけで、本人の言葉の本文を読まない。",
    ),
    Example(
        "推定の枠",
        "AI：推定意思",
        words=("泊まり",),
        note="本人の言葉がない問いで、根拠の本文を読んで推定を書く。画面では本人の言葉とは別の枠に出す。",
    ),
)


def read_lines(path: Path) -> list[dict]:
    """ログを読む。自分用のログは読まない。"""
    if "mine" in path.name.lower():
        raise SystemExit("自分用のログは読みません。")
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def texts_of(line: dict) -> dict:
    """ログの text の項目を辞書で返す。古い行は文字列のことがある。"""
    text = line.get("text")
    if isinstance(text, dict):
        return text
    if isinstance(text, str):
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def pick(lines: list[dict], example: Example, since: str) -> dict | None:
    """条件に合う、いちばん新しい成功の行を返す。"""
    for line in reversed(lines):
        if line.get("op") != example.op or line.get("outcome") != "成功" or line.get("at", "") < since:
            continue
        texts = texts_of(line)
        if example.words and not any(word in str(texts.get("入力", "")) for word in example.words):
            continue
        if "入力" in texts and "答え" in texts:
            return line
    return None


def _clip(text: str) -> str:
    """長い入力は先頭だけを見せる。切ったことを書く。文字は変えない。"""
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + f"\n……（ここまで。全体は {len(text):,} 字）"


def _pretty(answer: str) -> str:
    """AI の答えの JSON を、読めるように字下げする。中の文字は変えない。"""
    try:
        return json.dumps(json.loads(answer), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return answer


def render(lines: list[dict], since: str) -> tuple[str, list[str]]:
    """見本の文書と、見つからなかった見本の名前を返す。"""
    parts = [
        "# 実行結果の見本",
        "",
        "`scripts/make_examples.py` が、架空用のサーバーの開発のログから写したものです。文字は変えていません。人物と記録はすべて架空です。",
    ]
    missing = []
    for example in EXAMPLES:
        line = pick(lines, example, since)
        if line is None:
            missing.append(example.title)
            continue
        texts = texts_of(line)
        cache = f"、キャッシュ読み {line['cache_read_tokens']}" if line.get("cache_read_tokens") not in (None, 0, "0") else ""
        parts += [
            "",
            f"## {example.title}",
            "",
            example.note,
            "",
            f"{line['at']}／モデル {line.get('model')}／入力 {line.get('input_tokens')}／出力 {line.get('output_tokens')}{cache}／{line.get('ms')} ミリ秒",
            "",
            "入力：",
            "",
            "```text",
            _clip(str(texts["入力"])),
            "```",
            "",
            "出力：",
            "",
            "```json",
            _pretty(str(texts["答え"])),
            "```",
        ]
    return "\n".join(parts) + "\n", missing


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="実行結果の見本を書き出します。")
    parser.add_argument("--since", default="", help="この時刻より後の行だけを使う（例：2026-11-03T15:00）")
    parser.add_argument("--log", type=Path, default=LOG)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    text, missing = render(read_lines(args.log), args.since)
    args.out.write_text(text, encoding="utf-8")
    print(f"{args.out} に書きました。")
    if missing:
        print("見つからなかった見本：" + "、".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
