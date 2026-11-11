"""架空のフェイスシートとアセスメントシートの PDF を作る。

役割：fiction/documents/ の値（face_sheet_2036-04.json、assessment_2036-04.json）を、forms/ の書式の定義に入れて
     HTML と PDF にする。専門職が資料の取り込み（app/professional_records.py）に渡して試すため。
守る原則：8（提出物とデモは架空）。17（病名の欄も書式どおり埋める。ツインは材料にするが、診断と服薬の指示はしない）。

値は書式の定義と照らしてから作る。欄の名前が書式にない、選んだ値が選択肢にない、ときは止める。
わざと、本人のセルフとずれる中身を残している（本人の意向「デイサービスに通って、友人を作りたい」、
家族の意向の「お風呂はデイサービスで」）。架空のケアプラン（care_plan_2036-05.html）と同じ担当者が、
同じ訪問のあとに書いた、という設定にそろえたため。ツインのチェックが見どころになる。

使い方：python -m fiction.documents.make_forms
Chrome（または Edge）の印刷の機能で PDF にする。AI は呼ばない。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from app import care_forms
from fiction.documents.make_pdfs import run

HERE = Path(__file__).resolve().parent
SOURCES = ("face_sheet_2036-04", "assessment_2036-04")

STYLE = """
@page { size: A4; margin: 12mm; }
body { font: 9.5pt/1.55 "Yu Gothic", "Hiragino Sans", sans-serif; color: #111; margin: 0; }
.sheet { width: 186mm; margin: 0 auto; }
h1 { font-size: 15pt; text-align: center; margin: 0 0 1mm; letter-spacing: 0.2em; }
.fiction { text-align: center; font-size: 8.5pt; color: #555; margin: 0 0 3mm; }
h2 { font-size: 10.5pt; margin: 3mm 0 1mm; padding: 0.6mm 2mm; background: #e8e8e8; border-left: 3px solid #555; }
table { width: 100%; border-collapse: collapse; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #444; padding: 1mm 1.6mm; vertical-align: top; text-align: left; }
th { background: #f4f4f4; font-weight: 600; width: 44mm; }
.inner th { width: auto; text-align: center; background: #fafafa; }
.inner td { height: 5mm; }
.off { color: #777; margin-right: 2.2mm; white-space: nowrap; }
.on { font-weight: 700; margin-right: 2.2mm; white-space: nowrap; }
.detail { margin-top: 0.8mm; font-size: 9pt; }
"""


def _esc(text) -> str:
    return html.escape(str(text)).replace("\n", "<br>")


def check(form_id: str, values: dict, details: dict) -> None:
    """値を書式の定義と照らす。合わなければ止める（書式にない欄や選択肢にない値の資料を作らないため）。"""
    found = care_forms.problems(form_id, values, details)
    if found:
        raise SystemExit("、".join(found))


def _choices(form: dict, item: dict, value) -> str:
    """選ぶ欄は、選択肢をすべて並べ、選んだものに ■ を付ける（紙の書式と同じ見え方にする）。"""
    chosen = set(value) if isinstance(value, list) else {value}
    return "".join(
        f'<span class="{"on" if choice in chosen else "off"}">{"■" if choice in chosen else "□"}{_esc(choice)}</span>'
        for choice in care_forms.choices_of(form, item)
    )


def _field_row(form: dict, item: dict, key: str, label: str, values: dict, details: dict) -> str:
    value = values.get(key, "")
    if item["type"] in {"select", "multi_select"}:
        cell = _choices(form, item, value)
    else:
        cell = _esc(value)
    if item.get("detail") and details.get(key):
        cell += f'<div class="detail">詳細：{_esc(details[key])}</div>'
    return f"<tr><th>{_esc(label)}</th><td>{cell}</td></tr>"


def _items(form: dict, items: list[dict], path: list[str], values: dict, details: dict) -> list[str]:
    rows = []
    for item in items:
        if item["type"] == "group" and item.get("repeat", 1) > 1:
            # 同じまとまりを何件も書く欄は、小さな表にする。空いた行も書式どおり残す
            names = [sub["name"] for sub in item["fields"]]
            entries = values.get(item["name"], [])
            body = "".join(
                "<tr>" + "".join(f"<td>{_esc(entry.get(name, ''))}</td>" for name in names) + "</tr>"
                for entry in [*entries, *[{}] * (item["repeat"] - len(entries))]
            )
            head = "".join(f"<th>{_esc(name)}</th>" for name in names)
            rows.append(f'<tr><th>{_esc(item["name"])}</th><td><table class="inner"><tr>{head}</tr>{body}</table></td></tr>')
        elif item["type"] == "group":
            rows += _items(form, item["fields"], [*path, item["name"]], values, details)
        else:
            key = "／".join([*path, item["name"]])
            rows.append(_field_row(form, item, key, key, values, details))
    return rows


def render(spec: dict) -> str:
    form = care_forms.load(spec["form"])
    values, details = spec["values"], spec.get("details", {})
    check(spec["form"], values, details)
    parts = [f'<h1>{_esc(spec["title"])}</h1>', f'<p class="fiction">{_esc(spec["note"])}</p>']
    for section in form["sections"]:
        parts.append(f'<h2>{_esc(section["title"])}</h2><table>')
        parts += _items(form, section["fields"], [], values, details)
        parts.append("</table>")
    title = _esc(f'{spec["title"]}（架空）')
    return (
        '<!doctype html>\n<html lang="ja"><head><meta charset="utf-8"><title>'
        f"{title}</title><style>{STYLE}</style></head>"
        f'<body><div class="sheet">{"".join(parts)}</div></body></html>\n'
    )


def main() -> None:
    for stem in SOURCES:
        spec = json.loads((HERE / f"{stem}.json").read_text(encoding="utf-8"))
        page = HERE / f"{stem}.html"
        pdf = HERE / f"{stem}.pdf"
        page.write_text(render(spec), encoding="utf-8", newline="\n")
        run(f"--print-to-pdf={pdf}", page.as_uri())
        print(pdf.name, pdf.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
