"""田中さんの問いの一覧を本物の API で流し、届いた言葉を一覧にする。

役割：プログラムの変更やプロンプトの変更の前後で、同じ問いの結果を比べる。
4つの極：4. 10年後に代弁する。
守る原則：8（架空用のサーバーだけに聞く）、9（本人の言葉がないときは「まだ聞けていません」）。

動いている架空用のサーバーに、田中さんとして問いを送る。問いは「記録があるはずの問い」と「記録がないはずの問い」に
分けてあり、記録がないはずの問いに本人の言葉が届いたら印を付ける。問いの一覧は eval/answer_questions.json にある。
1問あたり数セントの費用がかかる。自分用の起動には聞かない。

使い方（架空用のサーバーを動かし、田中さんを招いたあとで）：
  python -m scripts.eval_answers
  python -m scripts.eval_answers --json logs/verify/answers.json

関連する記録：docs/tools.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "answer_questions.json"
BASE = "http://127.0.0.1:8000"


def _call(method: str, path: str, user: int | None = None, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, method=method, data=data)
    request.add_header("Content-Type", "application/json")
    if user is not None:
        request.add_header("X-NT-User", str(user))
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, {}


def tanaka_id() -> int:
    _, body = _call("GET", "/api/users")
    for user in body["items"]:
        if user["name"] == "田中さん":
            return user["id"]
    raise SystemExit("田中さんがいません。美穂さんとして招いてから流してください。")


def judge(expect: str, turn: dict) -> str:
    """問いの種類に合う結果かを返す。"""
    has_words = bool(turn["self_words"])
    if expect == "ある":
        return "○" if has_words else "×取りこぼし"
    return "×作りすぎ" if has_words else "○"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="田中さんの問いの一覧を流します。")
    parser.add_argument("--json", type=Path, help="結果を JSON でも書き出す先")
    args = parser.parse_args(argv)
    user = tanaka_id()
    results = []
    for case in json.loads(QUESTIONS.read_text(encoding="utf-8")):
        started = time.monotonic()
        code, body = _call("POST", "/api/advocacy/ask", user, {"question": case["question"], "client_key": uuid.uuid4().hex})
        seconds = round(time.monotonic() - started, 1)
        if code != 200:
            results.append({**case, "code": code, "judge": f"×{code}"})
            print(f"×{code}｜{case['question']}")
            continue
        turn = body["turn"]
        mark = judge(case["expect"], turn)
        results.append(
            {
                **case,
                "code": code,
                "seconds": seconds,
                "judge": mark,
                "self_words": [item["body"] for item in turn["self_words"]],
                "testimony": [item["body"] for item in turn["testimony"]],
                "guesses": [item["body"] for item in turn["guesses"]],
                "not_yet": turn["not_yet"],
                "estimate": (turn["estimate"] or {}).get("text"),
                "lead": turn["lead"],
                "notice": turn["notice"],
            }
        )
        words = "／".join(item["body"] for item in turn["self_words"][:3])
        print(f"{mark}｜{seconds}秒｜{case['question']}｜本人{len(turn['self_words'])}、証言{len(turn['testimony'])}、推定{'あり' if turn['estimate'] else 'なし'}｜{words[:90]}")
    passed = sum(1 for item in results if item["judge"] == "○")
    print(f"合計 {passed}/{len(results)}")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
