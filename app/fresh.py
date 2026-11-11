"""新規のデータ。入口の「新規で始める」で作る、デモとは別のデータ。

役割：data/fresh/<番号>/ に本人のいないデータを作り、要求をそのデータのアプリへ振り分ける。
4つの極：特定の極に属さない土台。
守る原則：8（デモの架空データに混ぜない）、10（見える範囲の決まりは新規でも同じ。同じ API を通す）。

1つのデータに本人は1人で（db.self_user）、動いているサーバーの下でファイルを入れ替えない（fiction/snapshot.py）。
そこで新規ごとに create_app で別のアプリを作り、ヘッダ X-NT-Data の値で要求ごとに振り分ける。
鍵、考え中の部屋、日付のずれはアプリごとに持つので、デモと新規は混ざらない。
サーバー全体の「今のデータ」を切り替える形にしなかったのは、2つのウィンドウと裏の処理が混ざるため。

振り分けは自分用の起動には付けない（app/main.py）。新規のアプリにも付かないので、入れ子にならない。
決めた経緯：docs/decisions.md
関連するテスト：tests/test_fresh.py
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import config, db, devlog, jobs

_HEADER = b"x-nt-data"
_OWN_PATH = "/api/fresh"
# 番号はフォルダの名前になるので、この形だけを受け取る。.. や絶対パスを道に混ぜないため
_ID_FORMAT = "%Y%m%d-%H%M%S-%f"
_ID_PATTERN = re.compile(r"\d{8}-\d{6}-\d{6}")
_NOT_FOUND = "そのデータは見つかりません。"
# 作りかけや壊れたデータベースで出る失敗。そのデータだけを、ないものとして扱う
_UNREADABLE = (sqlite3.Error, db.OldDatabaseError)

router = APIRouter(prefix="/api")


class FreshApps:
    """新規のアプリの入れ物。最初に開いたときに作り、起動の間は持ち続ける。"""

    def __init__(self, top: config.Deployment, build: Callable[..., FastAPI]) -> None:
        self.top = top
        self.root = top.db_path.parent / config.FRESH_FOLDER
        self._build = build
        self._apps: dict[str, FastAPI] = {}
        self._guard = threading.Lock()
        # ログのファイルは新規全体で1つなので、書き出しの鍵も1つにする。別々に持つと、ログを切り詰めるときにぶつかるため
        self._log = devlog.DevLog(top.log_path.with_name(config.FRESH_LOG), mine=True)
        # 止まった処理を流し直すか。本物の API を呼ぶので、python -m app の起動のときだけ真にする
        self.catch_up_on_open = False

    def _deployment(self, fresh_id: str) -> config.Deployment:
        return config.fresh_deployment(self.top, fresh_id)

    def _exists(self, fresh_id: str) -> bool:
        return _ID_PATTERN.fullmatch(fresh_id) is not None and (self.root / fresh_id).is_dir()

    def _ids(self) -> list[str]:
        """新しい順の番号。名前の形が違うフォルダは新規ではないので数えない。"""
        if not self.root.is_dir():
            return []
        return sorted((path.name for path in self.root.iterdir() if self._exists(path.name)), reverse=True)

    def app_of(self, fresh_id: str) -> FastAPI | None:
        """その新規のアプリを返す。形が違う番号と、ない番号は None。"""
        if not self._exists(fresh_id):
            return None
        with self._guard:
            app = self._apps.get(fresh_id)
            if app is None:
                try:
                    app = self._apps[fresh_id] = self._open(fresh_id)
                except _UNREADABLE:
                    return None
        return app

    def _open(self, fresh_id: str) -> FastAPI:
        deployment = self._deployment(fresh_id)
        # 質問セットの既定の置き場は Git に入っているファイルなので、新規の中へ向ける
        app = self._build(deployment=deployment, eval_cases_path=deployment.db_path.parent / "eval_cases.json")
        app.state.devlog = self._log
        if self.catch_up_on_open:
            threading.Thread(target=jobs.catch_up, args=(app,), daemon=True).start()
        return app

    def _self_name(self, fresh_id: str) -> str | None:
        """本人の名前を返す。一覧を読むだけなので、アプリは作らない。止まった処理を流さないため。"""
        path = self._deployment(fresh_id).db_path
        db.init_db(path, seed_demo=False)
        conn = db.connect(path)
        try:
            person = db.self_user(conn)
        finally:
            conn.close()
        return person.name if person else None

    def _names(self) -> list[tuple[str, str | None]]:
        """読める新規の番号と本人の名前を、新しい順に返す。作りかけで読めないものは数えない。"""
        rows = []
        for fresh_id in self._ids():
            try:
                rows.append((fresh_id, self._self_name(fresh_id)))
            except _UNREADABLE:
                continue
        return rows

    def start(self) -> str:
        """本人のいない新規の番号を返す。なければ作る。押すたびに空のフォルダが増えないようにする。"""
        with self._guard:
            for fresh_id, name in self._names():
                if name is None:
                    return fresh_id
            # 日付はサーバーの本物の今日。デモの日付のずれを持ち込まない
            fresh_id = datetime.now().strftime(_ID_FORMAT)
            (self.root / fresh_id).mkdir(parents=True)
            db.init_db(self._deployment(fresh_id).db_path, seed_demo=False)
            return fresh_id

    def listed(self) -> list[dict[str, str]]:
        """本人を登録した新規の一覧。本人のいない新規は「新規で始める」がまた使うので出さない。"""
        return [
            {"id": fresh_id, "name": name, "created_at": datetime.strptime(fresh_id, _ID_FORMAT).astimezone().isoformat()}
            for fresh_id, name in self._names()
            if name is not None
        ]


class FreshRouting:
    """ヘッダ X-NT-Data の付いた要求を、その新規のアプリへ渡す。

    一番外に置く（app/main.py）。デモのアプリのログを通さないため。デモのログには本文が書かれることがあり、
    新規には実在の人の話が入りうる。新規のアプリのログは、自分用と同じく本文を書かない（app/devlog.py）。
    """

    def __init__(self, app, apps: FreshApps) -> None:
        self.app = app
        self.apps = apps

    async def __call__(self, scope, receive, send) -> None:
        value = dict(scope.get("headers", [])).get(_HEADER) if scope["type"] == "http" else None
        path = scope.get("path", "")
        if value is None or not path.startswith("/api/") or path == _OWN_PATH:
            await self.app(scope, receive, send)
            return
        target = await run_in_threadpool(self.apps.app_of, value.decode("latin-1"))
        if target is None:
            await JSONResponse({"detail": _NOT_FOUND}, status_code=404)(scope, receive, send)
            return
        await target(scope, receive, send)


def attach(app: FastAPI, deployment: config.Deployment, build: Callable[..., FastAPI]) -> None:
    """起動のアプリに、新規の道と振り分けを付ける。自分用の起動と新規のアプリには呼ばない。"""
    apps = FreshApps(deployment, build)
    app.state.fresh_apps = apps
    app.include_router(router)
    app.add_middleware(FreshRouting, apps=apps)


@router.post("/fresh", status_code=201)
def start_fresh(request: Request) -> dict[str, str]:
    """新規で始める。ファイルを読むだけで済むので、ログインは求めない。"""
    return {"id": request.app.state.fresh_apps.start()}


@router.get("/fresh")
def list_fresh(request: Request) -> dict[str, list[dict[str, str]]]:
    """本人を登録した新規の一覧。入口に出すので、ログインは求めない。"""
    return {"items": request.app.state.fresh_apps.listed()}

