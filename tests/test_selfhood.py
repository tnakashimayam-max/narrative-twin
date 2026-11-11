"""セルフ生成エンジンのテスト。

役割：確からしさの計算、読まないものの漏れ、追記だけの決まり、実データを開かないことを、AI なしで確かめる。
4つの極：3. 読み解き、積み上げる。4. 10年後に代弁する。
守る原則：2、3、5、8、10、15。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db, ledger, selfhood, selfhood_stages, room_store
from app.schemas import (
    ConversationDraft,
    EvidenceStance,
    EvidenceWeightKind,
    PieceStatus,
    RoomKind,
    ShareScope,
    Source,
)
from fiction import selfhood_replay

KEY = "B/譲れないこと"
AS_OF = date(2036, 5, 1)


def _w(stance=EvidenceStance.SUPPORT, kind=EvidenceWeightKind.SELF_WORDS, day=date(2036, 1, 1)):
    return selfhood.Weighted(stance, kind, day)


def test_根拠がなければ空き():
    assert selfhood.stand([], AS_OF).status is PieceStatus.EMPTY


def test_確からしさは式のとおりにプログラムが計算する():
    standing = selfhood.stand([_w(day=AS_OF)] * 3, AS_OF)

    assert standing.support == 6.0
    assert standing.confidence == round(7 / 8, 3)
    assert standing.status is PieceStatus.LIKELY


def test_推測だけのピースは推測の状態になる():
    assert selfhood.stand([_w(kind=EvidenceWeightKind.INFERENCE)], AS_OF).status is PieceStatus.INFERRED


def test_本人が確かめた根拠が2件以上で本人が確かめた状態になる():
    one = [_w(kind=EvidenceWeightKind.CONFIRMED)]

    assert selfhood.stand(one * 2, AS_OF).status is PieceStatus.CONFIRMED
    assert selfhood.stand(one, AS_OF).status is not PieceStatus.CONFIRMED


def test_最近の反証で確からしさが中ほどなら揺れている():
    evidence = [_w(day=AS_OF), _w(EvidenceStance.AGAINST, day=AS_OF)]

    assert selfhood.stand(evidence, AS_OF).status is PieceStatus.WAVERING


def test_古い根拠は3年で重みが半分になる():
    assert selfhood.stand([_w(day=date(2033, 5, 1))], AS_OF).support == pytest.approx(1.0, abs=0.01)


def test_長い年月にわたって支えが続くピースは重みを減らさない():
    evidence = [_w(day=date(2027, 1, 1)), _w(day=date(2036, 1, 1))]

    assert selfhood.stand(evidence, AS_OF).support == 4.0


def test_本人の訂正は重み4の反証になる():
    standing = selfhood.stand([_w(EvidenceStance.AGAINST, EvidenceWeightKind.CORRECTION, AS_OF)], AS_OF)

    assert standing.against == 4.0


def test_足された受け答えは本人の言葉より軽い():
    assert selfhood.TUNING.weights[EvidenceWeightKind.ADDED_REPLY.value] < selfhood.TUNING.weights[EvidenceWeightKind.SELF_WORDS.value]


def test_実データの置き場では動かない(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    db_file = folder / "narrative_twin.db"
    db.init_db(db_file)

    with pytest.raises(selfhood.RealRecordsRefused):
        selfhood.refuse_real_records(db_file)
    assert selfhood_replay.main([str(db_file), "--run", "r"]) == 1


def test_最初の盤面は花びらが5枚かける3面で_人ごとに関係のピースがある(conn, users):
    pieces = selfhood.starting_pieces(conn)
    petals = [piece for piece in pieces if piece["section"] == selfhood.SECTION_PETALS]

    assert len(petals) == 15
    assert f"C/{users['美穂さん'].id}/本人にとって何か" in {piece["key"] for piece in pieces}
    assert not any(piece["person"] == users["誠一さん"].id for piece in pieces)


def test_家族の発言は本人像のピースに入らない():
    assert not selfhood.accepts(KEY, EvidenceWeightKind.TESTIMONY, False)
    assert selfhood.accepts("C/2/本人にとって何か", EvidenceWeightKind.TESTIMONY, False)
    assert selfhood.accepts(KEY, EvidenceWeightKind.SELF_WORDS, True)


def _pair_room(conn, users, *names):
    room = room_store.create_room(conn, kind=RoomKind.PAIR, name="試しの部屋", member_ids=[users[name].id for name in names], moment="2036-05-01T10:00:00+09:00")
    conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=date(2036, 5, 1), trigger="試し", room_id=room.id, life_stage=ledger.life_stage_on(conn, date(2036, 5, 1))))
    return room.id, conversation.id


def _say(conn, room_id, conversation_id, user, text):
    room_store.insert_message(conn, room_id=room_id, sender_user_id=user.id, text=text, client_key=text, sent_at="2036-05-01T10:00:00+09:00")
    conn.commit()


def test_本人のいる部屋のやり取りは読む(conn, users):
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "美穂さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "お昼に行くね。")

    assert "お昼に行くね。" in [row["text"] for row in selfhood.dialogue_rows(conn)]


def test_本人のいない部屋は読まない(conn, users):
    room_id, conversation_id = _pair_room(conn, users, "美穂さん", "和子さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "本人のいない話。")

    assert "本人のいない話。" not in [row["text"] for row in selfhood.dialogue_rows(conn)]


def test_本人だけとお休みの記録は読まない(conn, users, add_statement):
    add_statement("見せてよい言葉。", scope=ShareScope.DECISION_SUPPORT)
    add_statement("本人だけの言葉。", scope=ShareScope.SELF_ONLY)
    rested = add_statement("お休みにした言葉。", scope=ShareScope.DECISION_SUPPORT)
    ledger.rest_statement(conn, rested.id, actor_user_id=users["誠一さん"].id)
    conn.commit()

    bodies = [row["body"] for row in selfhood.statement_rows(conn)]

    assert "見せてよい言葉。" in bodies
    assert "本人だけの言葉。" not in bodies
    assert "お休みにした言葉。" not in bodies


def test_専門職だけの記録はセルフに読まない(conn, add_statement):
    add_statement("意思決定支援用の言葉。", scope=ShareScope.DECISION_SUPPORT)
    add_statement("アセスメントから残した言葉。", scope=ShareScope.PROFESSIONAL_ONLY)
    conn.commit()

    bodies = [row["body"] for row in selfhood.statement_rows(conn)]

    assert "意思決定支援用の言葉。" in bodies
    assert "アセスメントから残した言葉。" not in bodies


def test_AIの推測は材料にしない(conn, users, add_statement, add_utterance, make_draft):
    utterance = add_utterance("庭を見た。")
    ledger.append_statement(
        conn,
        make_draft([utterance.id], "庭が好きかもしれません。", source=Source.AI_GUESS, speaker_is_twin=True, speaker_user_id=None, share_scope=ShareScope.DECISION_SUPPORT),
    )
    conn.commit()

    assert "庭が好きかもしれません。" not in [row["body"] for row in selfhood.statement_rows(conn)]


def _standing():
    return selfhood.Standing(PieceStatus.HYPOTHESIS, 0.6, 2.0, 0.0)


def _version(conn, hypotheses, day, key=KEY):
    selfhood.add_piece_version(conn, run="r", key=key, section=selfhood.SECTION_CORE, name="譲れないこと", person=None, hypotheses=hypotheses, standing=_standing(), replay_date=day)


def test_ピースの版は足すだけで_前の版は消せない(conn):
    _version(conn, ["最初の仮説。"], "2030-01-31")
    _version(conn, ["直した仮説。"], "2031-01-31")
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM selfhood_pieces").fetchone()[0] == 2
    with pytest.raises(Exception, match="追記だけ"):
        conn.execute("DELETE FROM selfhood_pieces")
    with pytest.raises(Exception, match="追記だけ"):
        conn.execute("UPDATE selfhood_pieces SET name = 'x'")


def test_その日までの版と根拠で盤面を引く(conn):
    _version(conn, ["最初の仮説。"], "2030-01-31")
    _version(conn, ["直した仮説。"], "2033-01-31")
    selfhood.add_evidence(conn, run="r", key=KEY, stance=EvidenceStance.SUPPORT, kind=EvidenceWeightKind.SELF_WORDS, source_table="statements", source_id=1, day="2030-01-10")
    selfhood.add_evidence(conn, run="r", key=KEY, stance=EvidenceStance.AGAINST, kind=EvidenceWeightKind.SELF_WORDS, source_table="statements", source_id=2, day="2033-01-10")
    conn.commit()

    early = selfhood.board(conn, "r", "2031-12-31")[0]
    late = selfhood.board(conn, "r", "2036-05-01")[0]

    assert early["hypotheses"] == ["最初の仮説。"]
    assert early["against"] == 0
    assert late["hypotheses"] == ["直した仮説。"]
    assert late["against"] > 0


def test_再生を分けると盤面は混ざらない(conn):
    _version(conn, ["最初の仮説。"], "2030-01-31")
    conn.commit()

    assert selfhood.board(conn, "別の再生", "2031-12-31") == []


def test_セルフの文書がなければ_使う再生はない(conn):
    assert selfhood.active_run(conn) is None


def test_本人のいない部屋は読みの段にも渡らない(conn, users, monkeypatch):
    room_id, conversation_id = _pair_room(conn, users, "美穂さん", "和子さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "本人のいない話。")
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "美穂さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "本人のいる話。")
    seen = []

    def fake(role, *, system, content, schema, client=None, cached_system=None):
        seen.append(content)
        raise selfhood_stages.ai.AIUnavailableError("試し")

    monkeypatch.setattr(selfhood_stages.ai, "call_structured", fake)
    spend = selfhood_stages.Spend(1.0)

    selfhood_stages.read_dialogues(conn, spend, workers=1)

    assert any("本人のいる話。" in text for text in seen)
    assert not any("本人のいない話。" in text for text in seen)
    assert spend.failures


def test_渡していない番号の読みは入れない(conn, users, monkeypatch):
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "美穂さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "お昼に行くね。")
    answer = selfhood_stages.DialogueBatch(readings=[{"number": 99, "reading": "x"}])
    result = type("R", (), {"value": answer, "model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 1})()
    monkeypatch.setattr(selfhood_stages, "_call", lambda *args, **kwargs: result)

    assert selfhood_stages.read_dialogues(conn, selfhood_stages.Spend(1.0), workers=1) == 0


def test_本人以外の発言の読みは確からしさ04で注意書きが付く(conn, users, monkeypatch):
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "美穂さん")
    _say(conn, room_id, conversation_id, users["美穂さん"], "お昼に行くね。")
    answer = selfhood_stages.DialogueBatch(readings=[{"number": 1, "reading": "x"}])
    result = type("R", (), {"value": answer, "model": "claude-haiku-5-5", "input_tokens": 1, "output_tokens": 1})()
    monkeypatch.setattr(selfhood_stages, "_call", lambda *args, **kwargs: result)

    selfhood_stages.read_dialogues(conn, selfhood_stages.Spend(1.0), workers=1)

    row = conn.execute("SELECT confidence, caution FROM dialogue_readings").fetchone()
    assert row["confidence"] <= 0.4
    assert row["caution"]


def test_費用の上限で止まる(conn):
    spend = selfhood_stages.Spend(0.01)
    spend.by_stage["試し"] = 0.02

    with pytest.raises(selfhood_stages.BudgetExceeded):
        spend.check()


def test_経過の年数はかぎかっこの外だけ見つける():
    assert selfhood.elapsed_years("和子さんが亡くなって五年ほどになります。") == ["五年ほど"]
    assert selfhood.elapsed_years("和子さんは2024年に亡くなりました。") == []
    assert selfhood.elapsed_years("本人は「五年は早かったな」と言います。") == []


def test_人物像に経過の年数があれば1回だけ書き直させる(conn, monkeypatch):
    bodies = iter(["亡くなって五年ほどになります。", "2024年に亡くなりました。"])
    calls = []

    def fake(role, system, text, schema, client):
        calls.append(text)
        value = selfhood_stages.SelfhoodWriting(body=next(bodies))
        return type("R", (), {"value": value, "model": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 1})()

    monkeypatch.setattr(selfhood_stages, "_call", fake)
    spend = selfhood_stages.Spend(1.0)

    body = selfhood_stages.write_selfhood(conn, "t", "2036-05-05", spend)

    assert body == "2024年に亡くなりました。"
    assert len(calls) == 2 and "五年ほど" in calls[1]
    assert not spend.failures


def test_専門職の聞き取りは証言と同じ重みで数える(conn, users, add_utterance, make_draft):
    from app.schemas import ProfessionalItemKind, ProfessionalItemStatus

    def observed(text):
        utterance = add_utterance(text, speaker="田中さん")
        draft = make_draft([utterance.id], text, source=Source.OBSERVATION, speaker_user_id=users["田中さん"].id, share_scope=ShareScope.DECISION_SUPPORT)
        return ledger.append_statement(conn, draft)

    heard = observed("家で暮らしたいと話された。")
    seen = observed("台所に立っておられた。")
    for statement, kind in ((heard, ProfessionalItemKind.SELF_HEARD), (seen, ProfessionalItemKind.OBSERVATION)):
        db.insert_row(
            conn,
            "professional_items",
            {
                "teller_user_id": users["田中さん"].id,
                "kind": kind.value,
                "text": statement.body,
                "status": ProfessionalItemStatus.KEPT.value,
                "statement_id": statement.id,
                "created_at": "2036-05-01T00:00:00+09:00",
            },
        )
    conn.commit()

    kinds = {item["id"]: item["kind"] for item in selfhood_stages.month_items(conn, heard.effective_date.isoformat()[:7])}

    assert kinds[heard.id] is EvidenceWeightKind.TESTIMONY
    assert kinds[seen.id] is EvidenceWeightKind.OBSERVATION
