"""専門職の書式の定義のテスト。

役割：forms/ の書式が読めること、欄の型と選択肢がそろっていること、入れない決まりのもの（管理の欄、番地）が
     入っていないこと、出力の紙の形が入力の欄を正しく指していることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：17（医療の欄に印があること。医療の欄も材料にし、診断と服薬の指示はしない）。
"""

from __future__ import annotations

import pytest

from app import care_forms

# 業務システムの管理のための欄。書式としては要らないので入れない（forms/README.md）
MANAGEMENT_NAMES = {"利用者ID", "アセスメントID", "フェイスシートID", "ピリオド", "ターム", "タイミング", "更新日"}


def _walk(items):
    for item in items:
        yield item
        if item["type"] == "group":
            yield from _walk(item["fields"])


@pytest.mark.parametrize("form_id", care_forms.INPUT_FORMS)
def test_入力の書式は読めて_欄の型は決まった型だけ(form_id):
    form = care_forms.load(form_id)

    assert form["id"] == form_id
    for section in form["sections"]:
        for item in _walk(section["fields"]):
            assert item["type"] in care_forms.FIELD_TYPES, item


@pytest.mark.parametrize("form_id", care_forms.INPUT_FORMS)
def test_選ぶ欄には選択肢がある(form_id):
    for row in care_forms.fields(form_id):
        if row["type"] in {"select", "multi_select"}:
            assert row["choices"], row


@pytest.mark.parametrize("form_id", care_forms.INPUT_FORMS)
def test_同じまとまりの中で欄の名前が重ならない(form_id):
    keys = [(row["section"], key) for row, key in zip(care_forms.fields(form_id), care_forms.field_keys(form_id))]

    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("form_id", care_forms.INPUT_FORMS)
def test_業務システムの管理の欄は入れない(form_id):
    names = {row["name"] for row in care_forms.fields(form_id)}

    assert not names & MANAGEMENT_NAMES


@pytest.mark.parametrize("form_id", care_forms.INPUT_FORMS + care_forms.PRINT_FORMS)
def test_書式のファイルに番地を入れない(form_id):
    text = (care_forms.FORMS_DIR / f"{form_id}.json").read_text(encoding="utf-8")

    assert "http" not in text


def test_共通の選択肢の組は_中身に置き換えて返す():
    rows = {row["name"]: row for row in care_forms.fields("assessment")}

    assert rows["寝返り"]["choices"] == ["つかまらないで出来る", "何かにつかまればできる", "できない"]
    assert rows["麻痺"]["detail"] is True


def test_医療の欄には印がある():
    assessment = {row["name"]: row for row in care_forms.fields("assessment")}
    face = {row["name"]: row for row in care_forms.fields("face_sheet")}

    assert assessment["医療的処置"]["medical"] is True
    assert face["病名"]["medical"] is True
    assert face["主治医"]["medical"] is False


def test_まとまりを書ける数を持つ():
    face = {"／".join([*row["path"], row["name"]]): row for row in care_forms.fields("face_sheet")}

    assert face["病名・既往歴／病名"]["repeat"] == 6
    assert face["緊急連絡先／電話番号"]["repeat"] == 3


def test_生活まとめプランの紙は_場面15行と5つの書く列():
    form = care_forms.load("life_plan")

    assert len(form["rows"]) == 15
    assert [row for row in form["rows"] if row["name"] == "食事"][0]["parts"] == ["朝食", "昼食", "夕食", "飲水"]
    assert [column["name"] for column in form["columns"]][1:] == ["今まで", "出来ること", "出来ないこと", "支援して欲しいこと", "誰が"]
    assert sum(column["width"] for column in form["columns"]) == pytest.approx(100, abs=0.5)


def _froms(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def test_ライフサポートプランの紙は_入力の欄を指している():
    form = care_forms.load("life_support_plan_print")
    keys = set(care_forms.field_keys("life_support_plan"))
    pointed = []
    for item in [*form["header"], *form["blocks"]]:
        pointed += _froms(item.get("from"))
        for column in item.get("columns", []):
            pointed += _froms(column.get("from"))
        for extra in item.get("with", []):
            pointed += _froms(extra.get("from"))

    assert pointed
    assert set(pointed) <= keys, set(pointed) - keys


def test_知らない書式の名前は読まない():
    with pytest.raises(KeyError):
        care_forms.load("../.env")
