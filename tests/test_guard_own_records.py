"""自分の記録を守るフックのテスト。

役割：data/mine/ と自分用の起動に触れる呼び出しを止め、ふだんの開発の呼び出しを止めないことを確かめる。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（自分の記録は手元だけに置く）。

フックを別のプロセスで動かして、終了コードを見る。本物の data/mine/ は読まない。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_own_records.py"


def run_hook(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    body = json.dumps({"tool_name": tool_name, "tool_input": tool_input}, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=body.encode("utf-8"), capture_output=True, timeout=30
    )


def bash(command: str) -> tuple[str, dict]:
    return ("Bash", {"command": command})


HEREDOC_TEXT = "cat > docs/note.md <<'EOF'\n自分の記録は data/mine/ に置く。ポート 8001 で動く。\nEOF"
HEREDOC_COMMIT = "git commit -F - <<'EOF'\npython -m app --mine を足す\nEOF"
HEREDOC_RUN = "python - <<'EOF'\nprint(open('data/mine/narrative_twin.db','rb').read(10))\nEOF"

STOP = [
    bash("cat data/mine/narrative_twin.db"),
    bash("ls data/mine"),
    bash("ls data\\mine\\photos"),
    bash('cat "data"/"mine"/narrative_twin.db'),
    bash("sqlite3 C:/dev/narrative-twin/data/mine/narrative_twin.db .tables"),
    bash("git ls-files; cat data/mine/x"),
    bash("cat .gitignore; cat data/mine/x"),
    bash("git add -f data/mine/x"),
    bash("git show HEAD:data/mine/x"),
    bash("cp -r data /tmp/backup"),
    bash("tar czf a.tgz data/"),
    bash("grep -r 誠一 data"),
    bash("cat data/m*/narrative_twin.db"),
    bash("cat data/*/narrative_twin.db"),
    bash("cd data && cat mine/narrative_twin.db"),
    bash("python -c \"print(open('data/mine/x').read())\""),
    bash(HEREDOC_RUN),
    bash(".venv/Scripts/python -m app --mine"),
    bash("python app/__main__.py --mine"),
    bash("curl http://127.0.0.1:8001/api/me"),
    bash("curl localhost:8001"),
    bash("grep 'x' 'data/mine/y'"),
    bash("grep -r 'x' data"),
    bash('echo a; grep x "data/mine/y"'),
    bash("git ls-files | xargs cat data/mine/x"),
    bash("git status && cat data/mine/x 2>&1 | head -3"),
    bash("cat data/mine/x & echo 終わり"),
    bash('echo "$(cat data/mine/x)"'),
    bash("echo a > data/mine/y"),
    bash("echo `cat data/mine/x`"),
    ("PowerShell", {"command": "Get-Content data\\mine\\x"}),
    ("Monitor", {"command": "tail -f data/mine/x", "description": "d"}),
    ("Read", {"file_path": "C:\\dev\\narrative-twin\\data\\mine\\narrative_twin.db"}),
    ("Edit", {"file_path": "data/mine/x", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "data/mine/x", "content": "a"}),
    ("Grep", {"pattern": "x", "path": "data/mine"}),
    ("Grep", {"pattern": "x", "glob": "data/mine/**"}),
    ("Glob", {"pattern": "data/mine/**"}),
    ("mcp__playwright__browser_navigate", {"url": "http://127.0.0.1:8001/"}),
    ("mcp__playwright__browser_file_upload", {"paths": ["C:\\dev\\narrative-twin\\data\\mine\\photos\\a.jpg"]}),
    ("WebFetch", {"url": "http://localhost:8001/", "prompt": "x"}),
]

PASS = [
    bash("git check-ignore -v data/mine/x.db data/photos/a.jpg"),
    bash("git ls-files data/mine"),
    bash("git -C /c/dev/narrative-twin ls-files data/mine"),
    bash("git check-ignore data/mine/x && git ls-files data/mine"),
    bash("git log --all -- data/mine"),
    bash('git commit -m "python -m app --mine の説明を直す"'),
    bash(HEREDOC_COMMIT),
    bash(HEREDOC_TEXT),
    bash(".venv/Scripts/python -m app"),
    bash(".venv/Scripts/python -m pytest -q"),
    bash("curl http://127.0.0.1:8000/api/mode"),
    bash("curl http://127.0.0.1:80010/"),
    bash("netstat -ano | grep -E ':(8000|8001) '"),
    bash("du -sh data"),
    bash("ls data"),
    bash("ls -la data/trial/verify"),
    bash("cat data/README.md"),
    bash("cat data/trial/verify/round2.md"),
    bash("cp data/narrative_twin.db data/trial/backup.db"),
    bash("grep -rn verbatim app"),
    bash("git grep -n 'data/mine' HEAD --"),
    # 公開前の点検で使う形。引用符の中の | は区切りではない
    bash("git ls-files | grep -E '^\\.env$|CLAUDE\\.local|^data/mine/|^data/photos/|^data/trial/' || echo なし"),
    bash('grep -n "data/mine" docs/tools.md .gitignore'),
    bash("echo '--- data/mine の無視 ---'; git check-ignore -v data/mine/x || echo '無視の対象外'; git ls-files data/mine | wc -l"),
    bash('git commit -m "分ける; data/mine の説明を直す"'),
    bash("python -m pytest -q 2>&1 | tail -3"),
    bash('U=http://127.0.0.1:8000; curl -s --data \'{"user_id": 1}\' "$U/api/login" 2>&1'),
    ("Read", {"file_path": "C:\\dev\\narrative-twin\\app\\main.py"}),
    ("Read", {"file_path": "C:\\dev\\narrative-twin\\data\\mine_notes.md"}),
    ("Grep", {"pattern": "data/mine", "path": "docs"}),
    ("Write", {"file_path": "docs/tools.md", "content": "自分用は data/mine/ に置き、ポート 8001 で動く。"}),
    ("Edit", {"file_path": "CLAUDE.md", "old_string": "a", "new_string": "data/mine/"}),
    ("Agent", {"prompt": "data/mine は読まないでください"}),
    ("mcp__playwright__browser_navigate", {"url": "http://127.0.0.1:8000/"}),
]


@pytest.mark.parametrize(("tool", "tool_input"), STOP)
def test_自分の記録に触れる呼び出しは止める(tool, tool_input):
    done = run_hook(tool, tool_input)
    assert done.returncode == 2
    assert "止めました" in done.stderr.decode("utf-8")


@pytest.mark.parametrize(("tool", "tool_input"), PASS)
def test_ふだんの呼び出しは止めない(tool, tool_input):
    done = run_hook(tool, tool_input)
    assert done.returncode == 0, done.stderr.decode("utf-8")


def test_入力が読めないときは止める():
    done = subprocess.run([sys.executable, str(HOOK)], input=b"not json", capture_output=True, timeout=30)
    assert done.returncode == 2
