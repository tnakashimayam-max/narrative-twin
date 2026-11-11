"""実行結果の見本を docs/examples.md に書き出す。

役割：開発のログから役割ごとの実際の入力と出力を、架空用のデータベースの推論の記録から
      田中さんに届いた答えを選び、手を加えずに書き出す。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（架空用のログとデータベースだけを読む。自分用の記録は読まない）。

見本を手で整えると、実際の出力と食い違うので、ログの文字をそのまま写す。
どの行を選ぶかだけを、下の EXAMPLES で決める。入力か答えに含まれる語で選ぶ。

ログは物語を流したときと確かめのときで分かれるので、複数を渡せる。あとに渡したログの行を優先する。

使い方：.venv\\Scripts\\python -m scripts.make_examples --since 2026-11-04T21:57 --log ログ1 ログ2 --db データベース1 データベース2
関連するテスト：tests/test_make_examples.py
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from app.schemas import InferenceKind

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "fictional.jsonl"
DB = ROOT / "data" / "narrative_twin.db"
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
    Example("言明への分割", "AI：言明への分割", note="発言を言明に分け、出どころ、花びら、AI の読みを付ける。本人の言葉の一字一句はプログラムが確かめる。"),
    Example("固める処理（月）", "AI：固める処理（月）", note="月の言明から意思モデルの要素を作る。根拠は入力の番号で受け取る。"),
    Example("固める処理（年と人生の段階）", "AI：固める処理", note="下の段の要素から、年と人生の段階の要素を作る。"),
    Example(
        "田中さんへの回答（要素を選ぶ）",
        "AI：ツインの答え",
        words=("どうしても家で",),
        note="入力は問いだけで、意思モデルはキャッシュのブロックにある。AI は番号を選ぶだけで、本人の言葉の本文を読まない。",
    ),
    Example(
        "田中さんへの回答（言葉を選ぶ）",
        "AI：言葉を選ぶ",
        words=("どうしても家で",),
        note="選ばれた要素の根拠の本文を読み、問いに合う言葉の番号を返す。本文は書かない。プログラムが番号を確かめ、本文を記録から取り出す。",
    ),
    Example(
        "田中さんへの回答（確かめた言葉を足す）",
        "AI：確かめた言葉を足す",
        words=("施設に入れた",),
        note="言葉を選ぶ係のあとで、枠が空いていれば呼ぶ。本人が確かめた言葉の一覧はキャッシュのブロックにあり、入力は問いと空いている枠だけ。番号を返し、本文はプログラムが記録から取り出す。",
    ),
    Example(
        "推定の枠",
        "AI：推定意思",
        words=("泊まり",),
        note="本人の言葉がない問いで、根拠の本文を読んで推定を書く。画面では本人の言葉とは別の枠に出す。",
    ),
    Example(
        "ツインの考え",
        "AI：ツインのアドボカシー",
        words=("どうしても家で",),
        note="主語はツイン（AI）。本人の言葉は番号で指し、本文はプログラムが記録から入れる。枠の決めるのは人の文と、読んだ記録の期間と件数もプログラムが添える。",
    ),
    Example(
        "1枚の紙",
        "AI：ツインのアドボカシー",
        words=("1枚の紙",),
        note="ケア会議の机に置く紙。本人の言葉は番号で指す。",
    ),
    Example(
        "ケアプラン案への異議",
        "AI：根拠チェック",
        words=("ケアプラン案",),
        note="人が書いた第1版に、ツインの文として考えを返す。",
    ),
    Example(
        "ケアプラン案の第2版",
        "AI：原案",
        words=("第2版",),
        note="異議を受けて第2版を書く。どの版を使うかは人が選ぶ。",
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


def pick(lines: list[dict], example: Example, since: str, until: str = "9999") -> dict | None:
    """条件に合う、いちばん新しい成功の行を返す。"""
    for line in reversed(lines):
        moment = line.get("at", "")
        if line.get("op") != example.op or line.get("outcome") != "成功" or not since <= moment < until:
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


@dataclass(frozen=True)
class Delivered:
    """田中さんに届いた答えの見本の1件。"""

    title: str
    kind: str
    word: str
    note: str = ""


DELIVERED = (
    Delivered(
        "田中さんに届いた答え（本人の言葉がある問い）",
        InferenceKind.TWIN_ANSWER.value,
        "どうしても家で",
        "本文はプログラムが記録から取り出す。前置きもプログラムが付ける。AI が書くのは案内（lead）だけ。",
    ),
    Delivered(
        "田中さんに届いた答え（本人の言葉がない問い）",
        InferenceKind.TWIN_ANSWER.value,
        "泊まり",
        "「まだ聞けていません。」は決まった文。推定は estimate の枠に別に出る。",
    ),
    Delivered("田中さんに届いた推定の枠", InferenceKind.ESTIMATED_WILL.value, "泊まり", "根拠と反対の根拠の本文はプログラムが記録から取り出す。"),
)


def read_delivered(path: Path, item: Delivered) -> dict | None:
    """架空用のデータベースの推論の記録から、問いの語に合う、いちばん新しい答えを読む。"""
    if "mine" in {part.lower() for part in path.resolve().parts}:
        raise SystemExit("自分用の記録は読みません。")
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT question, result FROM inference_logs WHERE kind = ? AND question LIKE ? ORDER BY rowid DESC LIMIT 1",
            (item.kind, f"%{item.word}%"),
        ).fetchone()
    finally:
        connection.close()
    return {"question": row[0], "result": json.loads(row[1])} if row else None


def render_delivered(*paths: Path) -> tuple[str, list[str]]:
    """届いた答えの見本の文書と、見つからなかった見本の名前を返す。先に渡したデータベースから探す。"""
    parts: list[str] = []
    missing = []
    for item in DELIVERED:
        found = next((row for row in (read_delivered(path, item) for path in paths) if row is not None), None)
        if found is None:
            missing.append(item.title)
            continue
        parts += [
            "",
            f"## {item.title}",
            "",
            item.note,
            "",
            f"問い：{found['question']}",
            "",
            "```json",
            json.dumps(found["result"], ensure_ascii=False, indent=2),
            "```",
        ]
    return "\n".join(parts), missing


def render(lines: list[dict], since: str, until: str = "9999") -> tuple[str, list[str]]:
    """見本の文書と、見つからなかった見本の名前を返す。"""
    parts = [
        "# 実行結果の見本",
        "",
        "`scripts/make_examples.py` が、架空用のサーバーの開発のログから写したものです。文字は変えていません。人物と記録はすべて架空です。",
    ]
    missing = []
    for example in EXAMPLES:
        line = pick(lines, example, since, until)
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
    parser.add_argument("--until", default="9999", help="この時刻より前の行だけを使う")
    parser.add_argument("--log", type=Path, nargs="+", default=[LOG])
    parser.add_argument("--db", type=Path, nargs="+", default=[DB])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    text, missing = render([line for path in args.log for line in read_lines(path)], args.since, args.until)
    delivered, not_found = render_delivered(*args.db)
    missing += not_found
    args.out.write_text(text.rstrip("\n") + "\n" + delivered + "\n", encoding="utf-8")
    print(f"{args.out} に書きました。")
    if missing:
        print("見つからなかった見本：" + "、".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
