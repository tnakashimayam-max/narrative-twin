"""フック：アプリのコードを直したら、ツインの約束のテストを流す。

役割：app/ の Python を直したあと、一字一句の照合と同意の絞り込みのテストを自動で流し、
      落ちたら直した本人（Claude Code）に知らせる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：9（一字も変えない）、10（見せる範囲は本人が決める）。

全部のテストではなく約束に関わる分だけを流すのは、編集のたびに待ち時間を増やさないため。
全部のテストはコミットの前に流す。設定は .claude/settings.json、選んだ理由は docs/tools.md にある。

終了コード 2 は、Claude Code に stderr の内容を伝える決まり。
落ちたことを隠さないよう、テストを動かせなかったときも 2 で知らせる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# 約束に関わるテストのファイル。
# test_verbatim と test_splitter があるのは、本人の言葉の切り方の細かい崩れ（改行、かっこ、読点）に
# 台帳と同意のテストだけでは気づかなかったため。わざと崩して確かめた結果は docs/tools.md にある。
PROMISE_TESTS = [
    "tests/test_ledger.py",
    "tests/test_consent.py",
    "tests/test_ids.py",
    "tests/test_verbatim.py",
    "tests/test_splitter.py",
]
# 結果の末尾だけを見せる。全文を渡すと会話を圧迫するため。
TAIL_LINES = 25
TIMEOUT_SECONDS = 120


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])


def is_app_code(file_path: str, root: Path) -> bool:
    """直したファイルが app/ の Python かどうかを返す。"""
    try:
        relative = Path(file_path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return relative.parts[:1] == ("app",) and relative.suffix == ".py"


def python_command(root: Path) -> str:
    """プロジェクトの環境の Python を選ぶ。なければ、今動いている Python を使う。"""
    for candidate in (root / ".venv/Scripts/python.exe", root / ".venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> int:
    # Windows の既定の文字コードで読むと日本語の入力が壊れるので、UTF-8 に固定する
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("約束のテストのフック：入力を読めませんでした。", file=sys.stderr)
        return 2

    root = project_root()
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if not is_app_code(file_path, root):
        return 0

    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            [python_command(root), "-m", "pytest", "-q", "--no-header", *PROMISE_TESTS],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"約束のテストを動かせませんでした：{error}", file=sys.stderr)
        return 2

    if result.returncode == 0:
        return 0

    tail = "\n".join(result.stdout.splitlines()[-TAIL_LINES:])
    print(
        f"ツインの約束のテストが落ちました（直したファイル：{file_path}）。\n{tail}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
