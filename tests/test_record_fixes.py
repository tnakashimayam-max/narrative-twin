"""流し終えた物語の記録の文を、直した行として足す処理のテスト。

役割：fiction/record_fixes.py が、元の行を消さずに直した行を足し、二度当てても増えないことを確かめる。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：5（訂正しても元の記録は消さず、履歴として残す）。
"""

from __future__ import annotations

import json
from datetime import datetime

from app import ledger, observations
from app.schemas import ShareScope, Source
from fiction import record_fixes


def _original(conn, add_utterance, make_draft, users):
    utterance = add_utterance("研ぎ方を見せたそうです。蓋は、誠一さんが開けました。", speaker="田中さん")
    made = datetime(2026, 11, 2, 18, 0).astimezone()
    rows = [
        ledger.append_statement(
            conn,
            make_draft(
                [utterance.id],
                body,
                speaker_user_id=users["田中さん"].id,
                source=Source.OBSERVATION,
                share_scope=ShareScope.DECISION_SUPPORT,
                created_at=made,
            ),
        )
        for body in ("研ぎ方を見せたそうです。", "蓋は、誠一さんが開けました。")
    ]
    conn.commit()
    return rows


def test_直した行を足し_元の行は履歴に残り_書き込みの文の順に出る(conn, users, add_utterance, make_draft, monkeypatch):
    first, lid = _original(conn, add_utterance, make_draft, users)
    fix = {"id": 123456789, "replaces_id": lid.id, "body": "炊飯器の蓋を開けたのも誠一さんでした。", "table_file": "statements_2026.json"}
    monkeypatch.setattr(record_fixes, "FIXES", (fix,))

    assert record_fixes.apply_db(conn) == 1
    assert record_fixes.apply_db(conn) == 0

    assert ledger.get_statement(conn, lid.id).body == "蓋は、誠一さんが開けました。"
    added = ledger.get_statement(conn, 123456789)
    assert added.replaces_id == lid.id
    assert added.created_at == lid.created_at
    assert added.source is Source.OBSERVATION
    now = observations.now_view(conn, users["田中さん"], 4)
    assert [item["body"] for item in now] == ["研ぎ方を見せたそうです。", "炊飯器の蓋を開けたのも誠一さんでした。"]


def test_表には全部の言明の表の続きの番号で足し_二度目は足さない(tmp_path, monkeypatch):
    columns = ["rowid", "id", "body", "replaces_id", "source"]
    (tmp_path / "statements_2026.json").write_text(json.dumps({"columns": columns, "rows": [[1, 10, "元の文。", None, "今の様子（観察）"]]}), encoding="utf-8")
    (tmp_path / "statements_2036.json").write_text(json.dumps({"columns": columns, "rows": [[7, 20, "ほかの文。", None, "本人の言葉"]]}), encoding="utf-8")
    monkeypatch.setattr(record_fixes, "FIXES", ({"id": 30, "replaces_id": 10, "body": "直した文。", "table_file": "statements_2026.json"},))

    assert record_fixes.apply_tables(tmp_path) == 1
    assert record_fixes.apply_tables(tmp_path) == 0

    rows = json.loads((tmp_path / "statements_2026.json").read_text(encoding="utf-8"))["rows"]
    assert rows == [[1, 10, "元の文。", None, "今の様子（観察）"], [8, 30, "直した文。", 10, "今の様子（観察）"]]


def test_リポジトリの表には直した行が入っている():
    tables = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in record_fixes.TABLES.glob("statements_*.json")}
    for fix in record_fixes.FIXES:
        table = tables[fix["table_file"]]
        rows = [dict(zip(table["columns"], row)) for row in table["rows"]]
        added = next(row for row in rows if row["id"] == fix["id"])
        original = next(row for row in rows if row["id"] == fix["replaces_id"])
        assert added["body"] == fix["body"]
        assert added["replaces_id"] == original["id"]
        assert added["created_at"] == original["created_at"]
