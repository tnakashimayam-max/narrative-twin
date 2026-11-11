"""評価用の質問セットの入れ物。

役割：eval/cases.json に、質問・正解・質問の種類を読み書きする API を作る。
4つの極：特定の極に属さない土台（評価の道具）。
守る原則：8（評価の質問も架空の人物についてのもの）。

正解は人が作るものなので、AI は呼ばない。ファイルにしているのは、正解を差分で確かめられるように
するため。書き込みは一時ファイルを経由する。途中で止まっても元の正解を壊さないようにするため。

記録を返す API ではなく、架空の評価の道具なので、ログインを求めない。
サーバーは 127.0.0.1 だけで待ち受ける（app/__main__.py）。

関連するテスト：tests/test_eval_cases.py
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import EvalQuestionType

router = APIRouter(prefix="/api/eval")


class EvalCaseDraft(BaseModel):
    """評価の質問の入力。正解は空でもよい。あとから人が入れるため。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    correct_answer: str = ""
    question_type: EvalQuestionType


class EvalCase(EvalCaseDraft):
    """評価の質問。番号はファイルの中で一意の文字列にする。"""

    id: str


def read_cases(path: Path) -> list[EvalCase]:
    """質問セットを読む。ファイルがなければ空にする。"""
    if not path.exists():
        return []
    return [EvalCase.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def write_cases(path: Path, cases: list[EvalCase]) -> None:
    """質問セットを書く。読みやすさと差分のために、整形した日本語のまま保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps([case.model_dump(mode="json") for case in cases], ensure_ascii=False, indent=2)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _path(request: Request) -> Path:
    return request.app.state.eval_cases_path


@router.get("/cases")
def list_cases(request: Request) -> dict[str, list[EvalCase]]:
    return {"items": read_cases(_path(request))}


@router.post("/cases", status_code=201)
def add_case(draft: EvalCaseDraft, request: Request) -> EvalCase:
    cases = read_cases(_path(request))
    case = EvalCase(id=uuid.uuid4().hex, **draft.model_dump())
    write_cases(_path(request), [*cases, case])
    return case


@router.put("/cases/{case_id}")
def update_case(case_id: str, draft: EvalCaseDraft, request: Request) -> EvalCase:
    cases = read_cases(_path(request))
    for index, case in enumerate(cases):
        if case.id == case_id:
            cases[index] = EvalCase(id=case_id, **draft.model_dump())
            write_cases(_path(request), cases)
            return cases[index]
    raise HTTPException(status_code=404, detail="見つかりません")
