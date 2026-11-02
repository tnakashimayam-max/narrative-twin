"""開発のフックのテスト。

役割：アプリのコードを直したときだけ約束のテストが流れ、壊れていたら知らせが出ることを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：9（一字も変えない）、10（見せる範囲は本人が決める）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "check_promises.py"


def _run_hook(payload: bytes, project: Path) -> subprocess.CompletedProcess:
    # 親の pytest が設定する環境変数を外し、フックの中のテストと混ざらないようにする
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    environment["CLAUDE_PROJECT_DIR"] = str(project)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        env=environment,
        timeout=180,
        check=False,
    )


def _payload(file_path: Path | str, new_string: str = "変更") -> bytes:
    body = {"tool_name": "Edit", "tool_input": {"file_path": str(file_path), "new_string": new_string}}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _project_copy(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    for name in ("app", "tests", "static"):
        shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pytest.ini", ".gitignore"):
        shutil.copy(ROOT / name, project / name)
    return project


def test_アプリのコード以外ではテストを流さない():
    result = _run_hook(_payload(ROOT / "README.md", "日本語の入力"), ROOT)

    assert result.returncode == 0
    assert result.stderr == b""


def test_テストのファイルを直しても流さない():
    assert _run_hook(_payload(ROOT / "tests" / "test_ledger.py"), ROOT).returncode == 0


def test_日本語を含む入力を読める():
    """Windows の既定の文字コードで読むと壊れる入力。"""
    text = "本人の言葉は、一字も変えずに届ける。\n「お休み」"

    assert _run_hook(_payload(ROOT / "README.md", text), ROOT).returncode == 0


def test_読めない入力は知らせる():
    result = _run_hook(b"not json", ROOT)

    assert result.returncode == 2
    assert "入力を読めませんでした" in result.stderr.decode("utf-8")


def test_約束を壊すと知らせが出る(tmp_path):
    project = _project_copy(tmp_path)
    consent = project / "app" / "consent.py"
    original = consent.read_text(encoding="utf-8")
    broken = original.replace(
        "Role.CONNECTED: frozenset({ShareScope.CONNECTED}),", "Role.CONNECTED: frozenset(ShareScope),"
    )
    assert broken != original
    consent.write_text(broken, encoding="utf-8")

    result = _run_hook(_payload(consent), project)

    assert result.returncode == 2
    assert "ツインの約束のテストが落ちました" in result.stderr.decode("utf-8")


def test_壊していなければ知らせは出ない(tmp_path):
    project = _project_copy(tmp_path)

    result = _run_hook(_payload(project / "app" / "consent.py"), project)

    assert result.returncode == 0, result.stderr.decode("utf-8")
