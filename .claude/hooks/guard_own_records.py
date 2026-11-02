"""フック：開発者の自分の記録に、Claude Code が触れないようにする。

役割：道具を使う前に呼び出しを確かめ、data/mine/（開発者が手元で試す本物の記録）と、
      自分用の起動（--mine、ポート 8001）に触れるものを止める。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（提出物とデモは架空。自分の記録は手元だけに置き、Git に入れない）。

なぜ入れているか：
開発者は、自分の本当の言葉と写真を data/mine/ に入れて、このアプリを使いながら作っている。
開発中の Claude Code は許可の確認を省いた設定で動くので、止める仕組みがなければ命令はそのまま実行される。
本物の記録を読むと AI に渡り、テストや文書に写れば公開リポジトリに入るおそれがある。
「読まない」と文書で決めるだけでは、うっかり触れることを防げない。だからプログラムで止める。

なぜフックで止めるか：
.claude/settings.json の deny は Read や Edit の道具を止めるが、Bash の命令の中までは見ない。
そこで命令の文を読んで止める層を足している。
編集のあとに動く check_promises.py は壊れたことに気づくためのもので、触れる前に止めることはできない。
実際のブラウザを操作する道具は、開いている画面を URL の指定なしで読めて、呼び出しの文では判断できない。
そのため、このフックではなく deny で道具ごと止めている。
設定と選んだ理由、選ばなかった案は docs/tools.md にある。

うっかり触れることを止める仕組みで、わざと避ける動きまでは止められない。
スクリプトを書いてから実行する、変数で道を組み立てる、grep -r のように親のフォルダから読む、などは見えない。

終了コード 2 が止める合図で、stderr の文が Claude Code に伝わる。
入力が読めないときも 2 で止める。守れない状態で先へ進まないため。
"""

from __future__ import annotations

import json
import re
import sys

MINE = re.compile(r"data/+mine(?![\w-])", re.I)
PORT = re.compile(r"(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):8001(?!\d)", re.I)
START = re.compile(r"(-m\s+app|__main__\.py)\b[^\n]*\s--mine\b")
# data/m* や data/*/x のように、言葉を書かずに自分の記録へ届く形
MINE_GLOB = re.compile(r"data/+(m[^/\s]*[*?\[]|\*+/|[?\[])", re.I)
# data/ 全体を読む、写す命令。ls や du のように名前と大きさだけを見る命令は止めない
READS_TREE = re.compile(
    r"^(\w+=\S+\s+)*(cat|type|more|less|head|tail|cp|mv|scp|tar|zip|7z|sqlite3|strings|xxd|od|base64|"
    r"grep|egrep|fgrep|rg|sed|awk|Get-Content|Copy-Item|Move-Item|Compress-Archive|Select-String)\b"
    r"[^\n]*\s(\./)?data(/+\*{0,2})?(\s|$)",
    re.I,
)
NAMES_ONLY = re.compile(r"^(ls|du|dir)\b")
ENTER_DATA = re.compile(r"\b(cd|pushd|Set-Location)\s+(\./)?data/*(\s|$)", re.I)
BARE_MINE = re.compile(r"(^|[\s/])mine(/|\s|$)")
# 中身を読まない確かめ。git log と commit は文の中に言葉が出るだけで、git grep は追跡中のファイルしか読まない
SAFE_GIT = re.compile(r"^(\w+=\S+\s+)*git(\s+-[cC]\s+\S+)*\s+(check-ignore|ls-files|log|status|commit|grep)\b")
# 画面に文字を出すだけの命令。echo '--- data/mine の無視 ---' のような見出しは読むことではない。
# 命令の結果を埋め込む形と、ファイルへ書く形は除く
ONLY_PRINTS = re.compile(r"^(echo|printf)\b(?![^\n]*(\$\(|`|>))")
# grep の探す言葉（命令と選択肢のすぐ後の引用符）。grep x "data/mine/y" の引用符は場所なので当てはめない
SEARCH_WORD = re.compile(r"^((?:\w+=\S+\s+)*(?:grep|egrep|fgrep|rg|Select-String)(?:\s+-\S+)*)\s+(?:\"[^\"]*\"|'[^']*')", re.I)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
INTERPRETER = re.compile(r"(^|[\s|/\\])(python3?|py|node|bash|sh|zsh|pwsh|powershell|perl|ruby|php)(\.exe)?(\s|$)")

MINE_MESSAGE = (
    "止めました：data/mine/ は開発者が手元で試す本物の記録で、Claude Code は読み書きしません（原則8）。"
    "Git に入っていないことの確かめは git check-ignore か git ls-files でできます。"
    "コードや文書にこの言葉を書くときは Write か Edit を使ってください。"
)
PORT_MESSAGE = (
    "止めました：自分用の起動（--mine、ポート 8001）は開発者だけが使います（原則8）。"
    "確かめには架空用の python -m app（ポート 8000）を使ってください。"
)


def normalize(text: str) -> str:
    """書き方の違いをそろえる。data\\mine や "data"/"mine" でも同じように見つけるため。"""
    return text.replace("\\", "/").replace('"', "").replace("'", "")


def drop_text_heredocs(command: str) -> str:
    """ファイルに書くだけのヒアドキュメントの本文を除く。

    文書やコードの本文に data/mine と書くのは読むことではない。
    python - <<EOF のように実行される本文は読む動きになりうるので、残して確かめる。
    """
    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        found = HEREDOC.search(line)
        if not found:
            continue
        body: list[str] = []
        while index < len(lines) and lines[index].strip() != found.group(2):
            body.append(lines[index])
            index += 1
        index += 1  # 終わりの印を飛ばす
        # 同じ行の前の命令は関係ない。python a.py; cat >> .gitignore <<EOF の本文は実行されない
        if INTERPRETER.search(re.split(r";|&&|\|\|", line)[-1]):
            kept.extend(body)
    return "\n".join(kept)


def split_command(command: str) -> list[str]:
    """命令を、引用符の外の区切りで分ける。

    引用符の中の | や ; は区切りではない。grep -E 'a|data/mine/' を分けてしまうと、
    公開前の点検で使う命令まで止めてしまうため。
    """
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "\n" or char == ";":
            parts.append("".join(current))
            current = []
        elif char in "&|" and following == char:
            parts.append("".join(current))
            current = []
            index += 1
        elif char == "|" or (char == "&" and following != ">" and not "".join(current).endswith(">")):
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def check_command(command: str) -> str | None:
    """命令を区切りごとに確かめ、止める理由を返す。

    区切りごとに見るのは、git ls-files; cat data/mine/x を通さないため。
    """
    raw = drop_text_heredocs(command)
    for piece in split_command(raw):
        # 探す言葉は場所ではない。grep 'data/mine' docs は記録を読まない
        part = normalize(SEARCH_WORD.sub(r"\1", piece, count=1))
        if SAFE_GIT.match(part) or ONLY_PRINTS.match(part):
            continue
        if PORT.search(part) or START.search(part):
            return PORT_MESSAGE
        if MINE.search(part) or READS_TREE.search(part):
            return MINE_MESSAGE
        if MINE_GLOB.search(part) and not NAMES_ONLY.match(part):
            return MINE_MESSAGE
    text = normalize(raw)
    if ENTER_DATA.search(text) and BARE_MINE.search(text):
        return MINE_MESSAGE
    return None


def decide(payload: dict) -> str | None:
    """道具の呼び出しを確かめ、止める理由を返す。止めないときは None。"""
    name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    # ブラウザの道具や WebFetch は、URL や渡す内容の全体を見る
    if name.startswith("mcp__") or name == "WebFetch":
        text = normalize(json.dumps(tool_input, ensure_ascii=False))
        if PORT.search(text):
            return PORT_MESSAGE
        return MINE_MESSAGE if MINE.search(text) else None
    # Bash だけでなく、PowerShell や Monitor など命令を持つ道具を同じに扱う
    if isinstance(tool_input.get("command"), str):
        return check_command(tool_input["command"])
    # Grep の pattern は探す言葉で、場所ではない。Glob の pattern は場所を表す
    keys = ["file_path", "path", "notebook_path", "glob"]
    if name == "Glob":
        keys.append("pattern")
    paths = normalize(" ".join(str(tool_input.get(key, "")) for key in keys))
    return MINE_MESSAGE if MINE.search(paths) else None


def main() -> int:
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        print("止めました：フックが入力を読めませんでした。自分の記録を守れないので先へ進みません。", file=sys.stderr)
        return 2
    reason = decide(payload)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
