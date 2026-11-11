"""専門職が入力するアセスメントなどの記録のテスト。

役割：書式どおりの記録を保存でき、直すと新しい版になること、本人と家族には見せないこと、
     書式に合わない値は保存しないこと、資料のレビュー、ケアプランのチェック、生活まとめプランにだけ材料として渡ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：3（専門職の記録は本人の言葉と分ける）、5（直しても前の版は残す）。

本物の Claude API は呼ばない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import advocacy_plan, ai, care_form_records
from tests.fake_claude import RoleClaude

ROOT = Path(__file__).resolve().parent.parent


def _save(client, key="r1", **changes):
    body = {
        "form_id": "assessment",
        "record_date": "2036-04-02",
        "values": {"利用者名": "鈴木　誠一", "調理": "一部介助", "清潔（整容）／口腔ケア": "自立", "医療的処置": []},
        "details": {"調理": "紙の手順を見て炊く。最後のボタンが抜ける日がある。"},
        "client_key": key,
        **changes,
    }
    return client.post("/api/advocacy/care-forms/records", json=body)


def test_専門職が入力した記録を保存し_直すと新しい版になって前の版も残る(conn, login):
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]
    assert first["values"]["調理"] == "一部介助"
    # 空の値は落とす
    assert "医療的処置" not in first["values"]

    second = _save(tanaka, key="r2", values={"調理": "できない"} | {"利用者名": "鈴木　誠一"}, replaces_id=first["id"])

    assert second.status_code == 201
    items = tanaka.get("/api/advocacy/care-forms/records").json()["items"]
    assert [item["id"] for item in items] == [second.json()["record"]["id"]]
    assert conn.execute("SELECT COUNT(*) FROM care_form_records").fetchone()[0] == 2


def test_同じclient_keyなら足さない(conn, login):
    tanaka = login("田中さん")
    _save(tanaka)
    _save(tanaka)

    assert conn.execute("SELECT COUNT(*) FROM care_form_records").fetchone()[0] == 1


def test_本人と家族には見せない(login):
    record = _save(login("田中さん")).json()["record"]

    for name in ("誠一さん", "美穂さん"):
        client = login(name)
        assert client.get("/api/advocacy/care-forms/records").status_code == 404
        assert client.get(f"/api/advocacy/care-forms/records/{record['id']}").status_code == 404
        assert client.get("/api/advocacy/care-forms/forms").status_code == 404
        assert _save(client, key=f"x-{name}").status_code == 404


def test_書式に合わない値は保存しない(login):
    tanaka = login("田中さん")

    assert _save(tanaka, key="a", values={"調理": "とてもできる"}).status_code == 400
    assert _save(tanaka, key="b", values={"知らない欄": "あ"}).status_code == 400
    assert _save(tanaka, key="c", details={"利用者名": "詳細の欄がない"}).status_code == 400


def test_書式の定義を返す(login):
    items = login("田中さん").get("/api/advocacy/care-forms/forms").json()["items"]

    assert [item["id"] for item in items] == ["assessment", "face_sheet", "life_support_plan"]


def test_入力した記録をケアプランのチェックに渡す(conn, login):
    _save(login("田中さん"))

    note = advocacy_plan.form_note(conn)

    assert "アセスメントシート（2036-04-02、田中さんが入力）" in note or "アセスメントシート（2036-04-02、田中が入力）" in note
    assert "- 調理：一部介助（詳細：紙の手順を見て炊く。最後のボタンが抜ける日がある。）" in note


def test_記録がなければチェックに何も足さない(conn):
    assert advocacy_plan.form_note(conn) == ""


def test_入力した記録は_専門職だけが見る処理にしか渡さない():
    # ご家族も受け取る代弁と手紙に渡すと、本人と家族に見せない決まりが破れるため
    users = {path.name for path in (ROOT / "app").glob("*.py") if "care_form_lines" in path.read_text(encoding="utf-8")}

    # professional_records.py は資料のレビュー。ケアプランの道具を持つ専門職だけが読む（tyumajinさんの決定）
    assert users == {"care_form_records.py", "advocacy_plan.py", "life_plan.py", "professional_records.py"}


def test_いちばん新しい版だけを材料にする(conn, login):
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]
    _save(tanaka, key="r2", values={"利用者名": "鈴木　誠一", "調理": "できない"}, replaces_id=first["id"])

    text = "\n".join(care_form_records.care_form_lines(conn))

    assert "調理：できない" in text
    assert "調理：一部介助" not in text


# ---- ファイルから読み込む（PDF はツインが書式の欄に読み、保存はしない） ----

PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF\n"


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["書式を読む"] = {
        "record_date": "2036-04-02",
        "person_name": "鈴木　誠一",
        "entries": [
            {"key": "調理", "value": "一部介助", "choices": [], "detail": "紙の手順を見て炊く。"},
            {"key": "麻痺", "value": "とても強い", "choices": [], "detail": ""},
            {"key": "知らない欄", "value": "あ", "choices": [], "detail": ""},
            {"key": "医療的処置", "value": "", "choices": ["点滴", "魔法"], "detail": ""},
            {"key": "清潔（整容） ／口腔ケア", "value": "自立", "choices": [], "detail": ""},
        ],
        "rows": [],
    }
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


def _read(client, form_id="assessment", data=PDF):
    return client.post(f"/api/advocacy/care-forms/read?form_id={form_id}&file_name=a.pdf", content=data, headers={"content-type": "application/pdf"})


def test_PDFを書式の欄に読み_合わない値は捨てて_保存はしない(conn, login, claude):
    read = _read(login("田中さん")).json()["read"]

    assert read["values"]["調理"] == "一部介助"
    assert read["details"]["調理"] == "紙の手順を見て炊く。"
    assert read["values"]["医療的処置"] == ["点滴"]
    assert read["values"]["清潔（整容）／口腔ケア"] == "自立"
    assert "麻痺" not in read["values"]
    assert {"麻痺", "知らない欄"} <= set(read["skipped"])
    assert read["record_date"] == "2036-04-02"
    assert read["name_matches"] is True
    assert conn.execute("SELECT COUNT(*) FROM care_form_records").fetchone()[0] == 0
    assert claude.bodies("書式を読む")[0]["model"] == "claude-sonnet-5-5"


def test_何件も書く欄はまとまりごとの行にする(login, claude):
    claude.answers["書式を読む"] = {
        "record_date": None,
        "person_name": "",
        "entries": [],
        "rows": [
            {"group": "緊急連絡先", "index": 0, "name": "氏名", "value": "美穂さん"},
            {"group": "緊急連絡先", "index": 0, "name": "続柄", "value": "長女"},
            {"group": "緊急連絡先", "index": 7, "name": "氏名", "value": "数が多すぎる"},
        ],
    }

    read = _read(login("田中さん"), form_id="face_sheet").json()["read"]

    assert read["values"]["緊急連絡先"] == [{"氏名": "美穂さん", "続柄": "長女"}]
    assert "緊急連絡先／氏名" in read["skipped"]


def test_名前が合わない資料は知らせる(login, claude):
    claude.answers["書式を読む"] = {"record_date": None, "person_name": "山田　花子", "entries": [], "rows": []}

    assert _read(login("田中さん")).json()["read"]["name_matches"] is False


def test_PDFでないファイルと_本人と家族は読めない(login, claude):
    assert _read(login("田中さん"), data=b"not a pdf").status_code == 400
    assert _read(login("美穂さん")).status_code == 404
    assert _read(login("誠一さん")).status_code == 404
    assert claude.bodies("書式を読む") == []


def test_AIが見出しを付けた欄の名前でも当てはめる(login, claude):
    # 実測：AI が「利用者基本情報/氏名」のように、見出しと半角の区切りを付けて返した
    claude.answers["書式を読む"] = {
        "record_date": None,
        "person_name": "",
        "entries": [
            {"key": "利用者基本情報/氏名", "value": "鈴木　誠一", "choices": [], "detail": ""},
            {"key": "利用者基本情報／介護度", "value": "要支援2", "choices": [], "detail": ""},
        ],
        "rows": [],
    }

    read = _read(login("田中さん"), form_id="face_sheet").json()["read"]

    assert read["values"]["氏名"] == "鈴木　誠一"
    assert read["values"]["介護度"] == "要支援2"
    assert read["skipped"] == []


def test_削除すると前の版もまとめて一覧と中身とAIの材料から外れ_行は残る(conn, login):
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]
    second = _save(tanaka, key="r2", replaces_id=first["id"]).json()["record"]

    response = tanaka.delete(f"/api/advocacy/care-forms/records/{second['id']}")

    assert response.status_code == 200
    assert tanaka.get("/api/advocacy/care-forms/records").json()["items"] == []
    # 前の版が新しい版として戻ってこない
    for record_id in (first["id"], second["id"]):
        assert tanaka.get(f"/api/advocacy/care-forms/records/{record_id}").status_code == 404
    assert care_form_records.latest_records(conn) == []
    assert care_form_records.care_form_lines(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM care_form_records").fetchone()[0] == 2
    # 削除した記録は直せない
    assert _save(tanaka, key="r3", replaces_id=second["id"]).status_code == 400


def test_削除しても_ほかの記録は材料に残る(conn, login):
    tanaka = login("田中さん")
    older = _save(tanaka, key="old", record_date="2036-03-15").json()["record"]
    newer = _save(tanaka, key="new").json()["record"]

    tanaka.delete(f"/api/advocacy/care-forms/records/{newer['id']}")

    assert [row["id"] for row in care_form_records.latest_records(conn)] == [older["id"]]


def test_前の版は消せず_本人と家族は消せない(login):
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]
    _save(tanaka, key="r2", replaces_id=first["id"])

    assert tanaka.delete(f"/api/advocacy/care-forms/records/{first['id']}").status_code == 400
    record = _save(tanaka, key="r3", record_date="2036-05-01").json()["record"]
    for name in ("誠一さん", "美穂さん"):
        assert login(name).delete(f"/api/advocacy/care-forms/records/{record['id']}").status_code == 404
    assert tanaka.get(f"/api/advocacy/care-forms/records/{record['id']}").status_code == 200


def test_削除の印は書き換えも消しもできない(conn, login):
    tanaka = login("田中さん")
    record = _save(tanaka).json()["record"]
    tanaka.delete(f"/api/advocacy/care-forms/records/{record['id']}")

    with pytest.raises(Exception, match="記録の削除の印は追記だけです"):
        conn.execute("DELETE FROM care_form_deletions")


def test_コピーは別の日付の新しい記録になり_もとの記録も残る(login):
    tanaka = login("田中さん")
    source = _save(tanaka).json()["record"]

    response = _save(tanaka, key="copy", record_date="2036-05-10", copied_from=source["id"])

    assert response.status_code == 201
    copy = response.json()["record"]
    assert copy["id"] != source["id"]
    assert copy["record_date"] == "2036-05-10"
    assert copy["copied_from"] == source["id"]
    assert copy["copied_from_date"] == "2036-04-02"
    assert copy["values"]["調理"] == "一部介助"
    items = tanaka.get("/api/advocacy/care-forms/records").json()["items"]
    assert {item["id"] for item in items} == {source["id"], copy["id"]}
    # コピーを直しても、どの記録のコピーかは引き継ぐ
    fixed = _save(tanaka, key="copy2", record_date="2036-05-10", replaces_id=copy["id"]).json()["record"]
    assert fixed["copied_from_date"] == "2036-04-02"


def test_コピーのもとがないか書式が違えば断り_直しと一緒にはできない(login):
    tanaka = login("田中さん")
    source = _save(tanaka).json()["record"]

    face = {"form_id": "face_sheet", "values": {}, "details": {}}
    assert _save(tanaka, key="a", copied_from=source["id"], **face).status_code == 400
    assert _save(tanaka, key="b", copied_from=source["id"], replaces_id=source["id"]).status_code == 400
    tanaka.delete(f"/api/advocacy/care-forms/records/{source['id']}")
    assert _save(tanaka, key="c", copied_from=source["id"]).status_code == 400


def test_中身には第何版かと前の版の一覧が付く(login):
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]
    second = _save(tanaka, key="r2", replaces_id=first["id"]).json()["record"]

    record = tanaka.get(f"/api/advocacy/care-forms/records/{second['id']}").json()["record"]
    older = tanaka.get(f"/api/advocacy/care-forms/records/{first['id']}").json()["record"]

    assert record["version"] == 2
    assert [item["id"] for item in record["earlier"]] == [first["id"]]
    assert record["latest_id"] == second["id"]
    # 前の版も開いて読める。いちばん新しい版の番号で、新しい版へ戻れる
    assert older["version"] == 1
    assert older["latest_id"] == second["id"]


def test_直すときも記録の日付を変えられる(login):
    # tyumajinさん：修正のときに日付を固定してはいけない。修正でも日付を変えたい
    tanaka = login("田中さん")
    first = _save(tanaka).json()["record"]

    fixed = _save(tanaka, key="r2", record_date="2036-04-05", replaces_id=first["id"]).json()["record"]

    assert fixed["record_date"] == "2036-04-05"
    assert fixed["version"] == 2
    assert tanaka.get(f"/api/advocacy/care-forms/records/{first['id']}").json()["record"]["record_date"] == "2036-04-02"
