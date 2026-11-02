"""評価用の質問セットの入れ物のテスト。

役割：質問、正解、質問の種類を読み書きできることを確かめる。
4つの極：特定の極に属さない土台（評価の道具）。
守る原則：8（評価の質問も架空）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def cases_path(tmp_path):
    return tmp_path / "cases.json"


@pytest.fixture
def client(db_path, conn, cases_path):
    return TestClient(create_app(db_path, eval_cases_path=cases_path))


CASE = {"question": "誠一さんにとって、この家で大切なことは？", "correct_answer": "妻と過ごした場所だから", "question_type": "今の考え"}


def test_最初は空(client):
    assert client.get("/api/eval/cases").json() == {"items": []}


def test_質問と正解と種類を入れて読める(client):
    created = client.post("/api/eval/cases", json=CASE)

    assert created.status_code == 201
    item = client.get("/api/eval/cases").json()["items"][0]
    assert item["question"] == CASE["question"]
    assert item["correct_answer"] == CASE["correct_answer"]
    assert item["question_type"] == "今の考え"
    assert item["id"] == created.json()["id"]


@pytest.mark.parametrize("question_type", ["今の考え", "変化", "知らないこと", "一字一句"])
def test_決めた種類はすべて入る(client, question_type):
    assert client.post("/api/eval/cases", json={**CASE, "question_type": question_type}).status_code == 201


def test_決めていない種類は入らない(client):
    assert client.post("/api/eval/cases", json={**CASE, "question_type": "そのほか"}).status_code == 422


def test_空の質問は入らない(client):
    assert client.post("/api/eval/cases", json={**CASE, "question": ""}).status_code == 422


def test_正解はあとから入れられる(client):
    created = client.post("/api/eval/cases", json={**CASE, "correct_answer": ""}).json()

    response = client.put(f"/api/eval/cases/{created['id']}", json=CASE)

    assert response.status_code == 200
    assert client.get("/api/eval/cases").json()["items"][0]["correct_answer"] == CASE["correct_answer"]


def test_ない番号は直せない(client):
    assert client.put("/api/eval/cases/none", json=CASE).status_code == 404


def test_ファイルに日本語のまま保存される(client, cases_path):
    client.post("/api/eval/cases", json=CASE)

    text = cases_path.read_text(encoding="utf-8")
    assert "誠一さんにとって" in text
    assert json.loads(text)[0]["question_type"] == "今の考え"


def test_置いてあるファイルは正解の入れ物の形をしている():
    from pathlib import Path

    from app.eval_cases import read_cases

    path = Path(__file__).resolve().parent.parent / "eval" / "cases.json"
    assert isinstance(read_cases(path), list)


def test_入力の画面が配られる(client):
    page = client.get("/eval.html")

    assert page.status_code == 200
    assert "架空データ" in page.text
    assert "正解" in page.text
