"""専門職の書式（アセスメントシート、フェイスシート、ライフサポートプラン）と、出力の紙の形を読む。

役割：forms/ に置いた書式の定義を読み、欄の一覧を返す。専門職のツールが、資料を読み取るときや紙に出すときに、
     同じ欄の名前と並びを使うため。
4つの極：4. 10年後に代弁する。
守る原則：17（書式の病名や医療的処置の欄も材料にする。診断と服薬の指示はしない。medical の印で医療の欄を見分ける）。

書式の定義は、開発者の事業所で使っている書式の欄の名前と並びを、ハッカソンの期間中に書き起こしたもの。
業務システムのコード、事業所の名前、実在の人の情報は入れていない（forms/README.md）。

関連するテスト：tests/test_care_forms.py
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

FORMS_DIR = Path(__file__).resolve().parent.parent / "forms"
# 入力の書式と、出力の紙の形。ここにない名前は読まない（ファイルの名前を外から受け取らないため）
INPUT_FORMS = ("assessment", "face_sheet", "life_support_plan")
PRINT_FORMS = ("life_plan", "life_support_plan_print")
FIELD_TYPES = frozenset({"text", "long_text", "date", "number", "select", "multi_select", "group"})


@lru_cache(maxsize=None)
def load(form_id: str) -> dict:
    """書式の定義を読む。知らない書式の名前は KeyError にする。"""
    if form_id not in INPUT_FORMS + PRINT_FORMS:
        raise KeyError(form_id)
    return json.loads((FORMS_DIR / f"{form_id}.json").read_text(encoding="utf-8"))


def choices_of(form: dict, field: dict) -> list[str]:
    """選ぶ欄の選択肢を返す。共通の選択肢の組（choice_set）は、ここで中身に置き換える。"""
    if "choice_set" in field:
        return list(form["choice_sets"][field["choice_set"]])
    return list(field.get("choices", []))


def fields(form_id: str) -> list[dict]:
    """入力の書式の欄を、上から順に平らに並べて返す。

    まとまり（group）の中の欄は、path にまとまりの名前を入れる。repeat は、そのまとまりを書ける数。
    medical は、医療の欄か（まとまりに印があれば、中の欄にも付ける）。
    """
    if form_id not in INPUT_FORMS:
        raise KeyError(form_id)
    form = load(form_id)
    rows: list[dict] = []

    def walk(section: str, path: list[str], items: list[dict], medical: bool, repeat: int) -> None:
        for item in items:
            is_medical = medical or bool(item.get("medical"))
            if item["type"] == "group":
                walk(section, [*path, item["name"]], item["fields"], is_medical, repeat * item.get("repeat", 1))
                continue
            rows.append(
                {
                    "section": section,
                    "path": path,
                    "name": item["name"],
                    "type": item["type"],
                    "choices": choices_of(form, item),
                    "detail": bool(item.get("detail")),
                    "medical": is_medical,
                    "repeat": repeat,
                }
            )

    for section in form["sections"]:
        walk(section["title"], [], section["fields"], False, 1)
    return rows


def field_keys(form_id: str) -> list[str]:
    """欄を「まとまり／欄」の形の名前で返す。出力の紙の from と同じ書き方。"""
    return ["／".join([*row["path"], row["name"]]) for row in fields(form_id)]


TEXT_LIMIT = 2000


def problems(form_id: str, values: dict, details: dict) -> list[str]:
    """値を書式の定義と照らし、合わないところを文で返す。合っていれば空。

    書式にない欄、選択肢にない値、決まった数より多いまとまり、詳細の欄がない欄への詳細、長すぎる文を見る。
    入力の保存（app/care_form_records.py）と、架空の資料の PDF（fiction/documents/make_forms.py）で同じものを使う。
    """
    rows = fields(form_id)
    by_key = dict(zip(field_keys(form_id), rows))
    repeats = {row["path"][0]: row["repeat"] for row in rows if row["path"] and row["repeat"] > 1}
    found: list[str] = []
    for key, value in values.items():
        if key in repeats:
            names = {row["name"] for row in rows if row["path"] and row["path"][0] == key}
            if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
                found.append(f"{key} は、行の一覧で入れてください。")
                continue
            if len(value) > repeats[key]:
                found.append(f"{key} は {repeats[key]} 件までです。")
            for entry in value:
                if set(entry) - names:
                    found.append(f"{key} にない欄です：{'、'.join(sorted(set(entry) - names))}")
                if any(not isinstance(text, str) or len(text) > TEXT_LIMIT for text in entry.values()):
                    found.append(f"{key} の文が長すぎるか、文ではありません。")
            continue
        row = by_key.get(key)
        if row is None:
            found.append(f"書式にない欄です：{key}")
        elif row["type"] == "multi_select":
            if not isinstance(value, list) or not set(value) <= set(row["choices"]):
                found.append(f"{key} の選択肢にない値です：{value}")
        elif row["type"] == "select":
            if value not in row["choices"]:
                found.append(f"{key} の選択肢にない値です：{value}")
        elif not isinstance(value, str) or len(value) > TEXT_LIMIT:
            found.append(f"{key} の文が長すぎるか、文ではありません。")
    for key, text in details.items():
        row = by_key.get(key)
        if row is None or not row["detail"]:
            found.append(f"詳細の欄がない欄です：{key}")
        elif not isinstance(text, str) or len(text) > TEXT_LIMIT:
            found.append(f"{key} の詳細が長すぎるか、文ではありません。")
    return found
