"""開発のためのログをファイルに書き出す（決まった手順で動く層）。

役割：操作、AI の呼び出し、ツインの返事の作り方、裏の処理を、1行1件の JSON で logs/ に残す。
      開発中の Claude Code がこのファイルを読み、失敗と遅さに早く気づくために使う。画面には出さない。
4つの極：特定の極に属さない土台（開発の道具）。
守る原則：8（自分の記録は手元だけに置く）、15（人だけの部屋は AI に渡さない）。

架空用と自分用で、書く中身を分ける。
- 架空用（logs/fictional.jsonl）：渡された項目をすべて書く。発言、AI の入力と答えも text に入れる。
- 自分用（logs/mine.jsonl）：操作の種類、時刻、かかった時間、結果、エラーの種類、文字数、トークン数だけを書く。

自分用のログは Claude Code が読めるよう data/mine/ の外に置くので、本文を入れないことをここで決める。
呼ぶ側を信用せず、_mine_line が決めた項目と値の形だけを通す。知らない項目は捨て、本文の入る余地を作らない。
例外の文は書かず、種類の名前だけを書く。Pydantic や SQLite の例外の文には入力が混ざることがあるため。

リクエストの本文は読まない。発言や AI の入力は、処理の途中で必要なものだけを渡す。
写真のデータと、人だけの部屋の本文がログに入らないようにするため。

1行ごとに開いて閉じる。テストが多くのアプリを作っても、ファイルを開いたままにしないため。
書き出しの失敗ではアプリを止めない。ログは開発の道具で、本人の発言を受け取ることより優先しない。

関連するテスト：tests/test_devlog.py
決めた経緯：docs/decisions.md
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from app import config

# 大きくなったら1世代だけ残す。架空用は AI の入力も書くので、上限がないと増え続けるため。
_MAX_BYTES = 5_000_000
_MAX_TEXT_CHARS = 20_000
# ツインの会話の実測の最大は約8秒（docs/tools.md）。これを超えたら気づけるようにする。
_SLOW_MS = 10_000

# 結果の言葉。自分用のログには、ここにある言葉だけを通す。
SUCCESS = "成功"
FAILED = "失敗"
REJECTED = "拒否"
SLOW = "遅い"
AI_REFUSED = "断られた"
AI_BAD_ANSWER = "型が合わない"
AI_UNREACHABLE = "届かない"
ASKED = "問いあり"
NOT_ASKED = "問いなし"
QUESTION_DROPPED = "問いを落とした"
REPLY_TRIMMED = "受け止めを短くした"
REPLY_REPLACED = "決まった文に替えた"
STATEMENT_DROPPED = "入らなかった言明あり"
NOT_YET = "まだ聞けていない"
_RESULTS = frozenset(
    {
        NOT_YET,
        SUCCESS,
        FAILED,
        REJECTED,
        SLOW,
        AI_REFUSED,
        AI_BAD_ANSWER,
        AI_UNREACHABLE,
        ASKED,
        NOT_ASKED,
        QUESTION_DROPPED,
        REPLY_TRIMMED,
        REPLY_REPLACED,
        STATEMENT_DROPPED,
    }
)

START = "起動"
TWIN_TURN = "ツインの返事"
_NO_ROUTE = "（道なし）"
_FIXED_OPS = frozenset(
    {START, TWIN_TURN, "言明への分割", "会話の要約", "人の候補", "グループのツイン", "スレッドの問い", "専門職の問い", "固める処理"}
)
# 道の形はコードに書いたものだけで、番号の部分は {room_id} のままになる。実際の URL は通さない。
_ROUTE_OP = re.compile(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/api[/\w{}:-]*|（道なし）)", re.ASCII)
_ERROR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}")


def _known_op(op: str) -> bool:
    if op in _FIXED_OPS or _ROUTE_OP.fullmatch(op):
        return True
    return op.startswith("AI：") and op.removeprefix("AI：") in config.MODEL_BY_ROLE


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mine_line(op: str, fields: dict) -> dict:
    """自分用のログの1行を作る。決めた項目と値の形だけを通し、ほかは捨てる。"""
    line: dict = {"op": op if _known_op(op) else "不明"}
    for key, value in fields.items():
        if key in {"ms", "status"} or key.endswith(("_chars", "_tokens")):
            if _is_number(value):
                line[key] = value
        elif key == "outcome":
            if value in _RESULTS:
                line[key] = value
        elif key == "notes":
            kept = [item for item in value if item in _RESULTS]
            if kept:
                line[key] = kept
        elif key == "error" and isinstance(value, str) and _ERROR_NAME.fullmatch(value):
            line[key] = value
    return line


class DevLog:
    """起動の種類ごとに1つ持つ、ログの書き出し先。"""

    def __init__(self, path: Path, *, mine: bool) -> None:
        self.path = path
        self.mine = mine
        self._lock = threading.Lock()

    def write(self, op: str, *, text: dict | None = None, **fields) -> None:
        """1件を書く。text は発言や AI の入力などの本文で、架空用のログにだけ入る。"""
        if self.mine:
            line = _mine_line(op, fields)
        else:
            line = {"op": op, **fields}
            if text:
                line["text"] = {
                    key: value[:_MAX_TEXT_CHARS] if isinstance(value, str) else value for key, value in text.items()
                }
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), **line}
        try:
            with self._lock:
                self._keep_small()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as file:
                    file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def _keep_small(self) -> None:
        if self.path.exists() and self.path.stat().st_size >= _MAX_BYTES:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))


# 今の処理が書くログ。twin や ai の関数に引数を増やさず、架空用と自分用を取り違えないようにするため。
# 決まっていないときは何も書かない。行き先が分からない本文を、どこにも書かないため。
_current: ContextVar[DevLog | None] = ContextVar("devlog", default=None)


@contextmanager
def use(log: DevLog) -> Iterator[None]:
    """この中の record を、指定したログに書く。裏の処理の入口で使う。"""
    token = _current.set(log)
    try:
        yield
    finally:
        _current.reset(token)


def record(op: str, *, text: dict | None = None, **fields) -> None:
    """今の処理のログに1件書く。ログが決まっていなければ何もしない。"""
    log = _current.get()
    if log is not None:
        log.write(op, text=text, **fields)


def error_name(error: BaseException) -> str:
    return type(error).__name__


def where(error: BaseException) -> str:
    """例外が起きた app/ の中の場所を、新しいほうから5つ返す。架空用のログにだけ入る。"""
    folder = config.ROOT / "app"
    places = []
    for frame in traceback.extract_tb(error.__traceback__):
        path = Path(frame.filename)
        if folder in path.parents:
            places.append(f"{path.relative_to(config.ROOT).as_posix()}:{frame.lineno}")
    return " ".join(places[-5:])


def since(started: float) -> int:
    """time.monotonic() で測り始めてからのミリ秒を返す。"""
    return round((time.monotonic() - started) * 1000)


def _operation(scope: dict) -> str:
    """操作の種類を返す。実際の URL ではなく、コードに書いた道の形を使う。"""
    route_path = getattr(scope.get("route"), "path", None)
    if not isinstance(route_path, str) or not route_path.startswith("/api"):
        route_path = _NO_ROUTE
    return f"{scope['method']} {route_path}"


def _outcome(status: int, ms: int) -> str:
    """結果を決める。422 は画面とサーバーの取り決めのずれなので、失敗に数える。"""
    if status >= 500 or status == 422:
        return FAILED
    if status >= 400:
        return REJECTED
    return SLOW if ms >= _SLOW_MS else SUCCESS


class RequestLog:
    """/api の操作を1件ずつ書く。

    BaseHTTPMiddleware を使わず ASGI のまま書くのは、応答を返したあとに動く裏の処理を
    かかった時間に含めないため。本文は読まない。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return
        log: DevLog = scope["app"].state.devlog
        started = time.monotonic()
        status = 0
        written = False

        async def send_and_note(message) -> None:
            nonlocal status, written
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body") and not written:
                written = True
                ms = since(started)
                log.write(_operation(scope), status=status, outcome=_outcome(status, ms), ms=ms)

        token = _current.set(log)
        try:
            await self.app(scope, receive, send_and_note)
        except Exception as error:
            log.write(
                _operation(scope), outcome=FAILED, error=error_name(error), ms=since(started), where=where(error)
            )
            raise
        finally:
            _current.reset(token)
