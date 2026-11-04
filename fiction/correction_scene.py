"""訂正の場面まで流す。相手の推測が庭の話でなければ、推測の日の前へ戻して流し直す。

役割：script.jsonl を訂正の行のある日まで流し、推測と訂正の文がかみ合う形にして止まる。そのあとは同じ台本を
      fiction.run_script で続きから流す。
4つの極：3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：4（AI の推測は推測として本人が答える）、8（人物と記録はすべて架空）。

なぜこう作ったか：訂正の文は「庭が好きなのではなく、妻と過ごした場所だから大切」と決まっている。AI の推測は
流すたびに変わり、庭の話でない回がある（流した7回のうち2回）。そのまま訂正すると場面が成り立たない。
順番は次のとおり。
1. 本物の AI の推測を使う。流す道具が、推測が庭の話かを確かめ、違えば訂正の前で止まる。
2. 推測の日の前の控えへ戻して、最大3回流し直す。推測は会話を閉じたあとの言明への分割で出るので、その日から流せば足りる。
3. それでも出なければ、推測01を台本の文として入れた台本（fiction.convert_chat --script-guess）へ切り替える。
   この1件は AI の言葉ではないので、どちらの方法になったかを correction_scene.json に残す。

使い方：
  python -m fiction.correction_scene 台本 推測01を入れた台本 --folder 流す先 --workers 8 --budget 110 --keep-going

関連するテスト：tests/test_correction_scene.py
決まりの説明：docs/script-format.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fiction import run_script
from fiction.convert_chat import GUESS_DAY, HANDOVER_DAY

# 最初の1回のあとに流し直す回数
MAX_RERUNS = 3


def run_to_scene(
    script: Path,
    fallback: Path,
    folder: Path,
    *,
    options: list[str] = (),
    guess_day: date = date.fromisoformat(GUESS_DAY),
    until: date = date.fromisoformat(HANDOVER_DAY),
    reruns: int = MAX_RERUNS,
    run: Callable[[list[str]], int] = run_script.main,
) -> dict:
    """訂正の場面まで流して、場面を返す。推測と無関係の理由で止まったら、流し直さず run_script.StopRun を出す。"""
    checkpoint = f"before-{guess_day.isoformat()}"
    rest = ["--folder", str(folder), "--until", until.isoformat(), "--checkpoint-days", f"{guess_day.isoformat()},{until.isoformat()}", *options]
    code = run([str(script), *rest])
    done = 0
    while code == run_script.GUESS_EXIT and done < reruns:
        run_script.restore_checkpoint(folder, checkpoint)
        done += 1
        code = run([str(script), *rest])
    used = script
    if code == run_script.GUESS_EXIT:
        run_script.restore_checkpoint(folder, checkpoint)
        used = fallback
        code = run([str(fallback), *rest])
    if code != 0:
        raise run_script.StopRun(f"訂正の場面まで流せませんでした（終了の番号 {code}）。同じ命令で続きから流せます。")
    scene = run_script.read_scene(folder)
    if scene is None:
        raise run_script.StopRun(f"{until} までに訂正の行がありませんでした。")
    scene.update(reruns=done, script=str(used))
    (folder / run_script.SCENE_FILE).write_text(json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8")
    return scene


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="訂正の場面まで流します。推測が庭の話でなければ流し直します。")
    parser.add_argument("script", type=Path)
    parser.add_argument("fallback", type=Path, help="推測01を台本の文として入れた台本")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--workers", default=str(run_script.DEFAULT_WORKERS))
    parser.add_argument("--budget", default=str(run_script.DEFAULT_BUDGET_USD))
    parser.add_argument("--keep-going", action="store_true", help="合う記録がない確かめの行を飛ばします。訂正の推測の確かめは止まります")
    args = parser.parse_args(argv)
    try:
        folder = run_script.safe_folder(args.folder)
        options = ["--workers", args.workers, "--budget", args.budget, *(["--keep-going"] if args.keep_going else [])]
        scene = run_to_scene(args.script, args.fallback, folder, options=options)
    except run_script.StopRun as error:
        print(f"止めました：{error}")
        return 2
    print(json.dumps(scene, ensure_ascii=False, indent=1))
    print(f"続きは次の命令で流せます：python -m fiction.run_script {scene['script']} --folder {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
