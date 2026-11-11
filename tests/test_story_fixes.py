"""流し終えた物語に、あとから当てる直しのテスト。

役割：一言の行だけが外れ、発言の台帳と言明と番号が変わらないこと、家系図と候補の直しが何度当てても同じことを確かめる。
4つの極：特定の極に属さない土台。
守る原則：5（元の記録を残す）、8（提出物とデモは架空）。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app import ai, db, ledger, room_store
from app.schemas import ConversationDraft, Place, RoomKind, ShareScope
from fiction import story_fixes

AT = datetime(2036, 5, 1, 22, 0)


def _room(conn):
    return room_store.ensure_twin_room(conn, db.self_user(conn), AT.isoformat()).id


def _say(conn, conversation_id, text, *, sender=None, at=AT):
    return room_store.say_in_ledger(
        conn,
        room_id=_room(conn),
        conversation_id=conversation_id,
        place=Place.DIARY_AND_TALK,
        is_twin=sender is None,
        sender_user_id=sender,
        text=text,
        sent_at=at,
    )


@pytest.fixture
def story(conn, users):
    """本人の発言のあとに、受け止めと一言が同じ時刻に続く会話を2つ作る。"""
    self_id = users["誠一さん"].id
    made = []
    for day, said, reply, tip in (
        (1, "和子を帰してやれんかった。", "話してくださって、ありがとうございます。", "夜は冷えるので、温かくして休まれるとよいですね。"),
        (2, "今日は庭の草を抜いた。", "庭のことを聞かせてくださいました。", "明日の朝は涼しいうちに水をやるとよさそうです。"),
    ):
        moment = datetime(2036, 5, day, 22, 0)
        conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=moment.date(), trigger="試し", room_id=_room(conn), life_stage=ledger.life_stage_on(conn, moment.date())))
        _say(conn, conversation.id, said, sender=self_id, at=moment)
        _say(conn, conversation.id, reply, at=moment)
        _say(conn, conversation.id, tip, at=moment)
        made.append(conversation.id)
    conn.commit()
    return made


def _dump(conn, table):
    return [tuple(row) for row in conn.execute(f'SELECT rowid, * FROM "{table}" ORDER BY rowid')]


def test_今日の一言の行は_受け止めと同じ時刻の最後のツインの行である(conn, story):
    rows = story_fixes.closing_rows(conn)

    assert [row["text"] for row in rows] == ["夜は冷えるので、温かくして休まれるとよいですね。", "明日の朝は涼しいうちに水をやるとよさそうです。"]


def test_受け止めだけの会話には今日の一言の行がない(conn, users):
    moment = datetime(2036, 5, 3, 22, 0)
    conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=moment.date(), trigger="試し", room_id=_room(conn), life_stage=ledger.life_stage_on(conn, moment.date())))
    _say(conn, conversation.id, "風呂に入った。", sender=users["誠一さん"].id, at=moment)
    _say(conn, conversation.id, "聞かせてくださいました。", at=moment)
    conn.commit()

    assert story_fixes.closing_rows(conn) == []


def _closing_answer(*, system, text, client=None):
    """重い話の会話には空の一言を返す。受け止めと一言は入力に入っていないことも確かめる。"""
    assert "話してくださって" not in text
    assert "夜は冷える" not in text
    tip = "" if "和子を帰してやれんかった" in text else "新しい一言です。"
    return SimpleNamespace(value=SimpleNamespace(tip=tip), model="claude-haiku-5-5", input_tokens=10, output_tokens=5)


def test_空の一言を返した会話だけを外す判断にする(conn, story, monkeypatch):
    monkeypatch.setattr(ai, "twin_closing", _closing_answer)

    result = story_fixes.judge(conn)

    assert result["judged"] == 2
    assert result["failed"] == 0
    assert [item["text"] for item in result["removed"]] == ["夜は冷えるので、温かくして休まれるとよいですね。"]


def test_判断できなかった行は外さない(conn, story, monkeypatch):
    def fails(*, system, text, client=None):
        raise ai.AIUnavailableError("試し")

    monkeypatch.setattr(ai, "twin_closing", fails)

    result = story_fixes.judge(conn)

    assert result["failed"] == 2
    assert result["removed"] == []


def test_一言の行だけが外れ_発言の台帳と言明とほかの行の番号は変わらない(conn, story, add_statement):
    add_statement("和子を帰してやれんかった。", scope=ShareScope.DECISION_SUPPORT)
    conn.commit()
    rows = story_fixes.closing_rows(conn)
    before = {table: _dump(conn, table) for table in ("utterances", "statements", "room_messages")}
    conn.isolation_level = None

    result = story_fixes.apply(conn, {"removed": rows[:1]}, family=False)

    assert result["hidden_tips"] == 1
    assert _dump(conn, "utterances") == before["utterances"]
    assert _dump(conn, "statements") == before["statements"]
    after = _dump(conn, "room_messages")
    gone = [row for row in before["room_messages"] if row not in after]
    assert len(gone) == 1
    assert [row for row in before["room_messages"] if row in after] == after


def test_外した行の分だけ読んだ位置を前へずらす(conn, story, users):
    room = _room(conn)
    conn.execute(
        "INSERT INTO room_reads (room_id, user_id, last_read_seq, updated_at) VALUES (?, ?, 4, '2036-05-02T00:00:00+09:00')",
        (room, users["誠一さん"].id),
    )
    conn.commit()
    conn.isolation_level = None
    rows = story_fixes.closing_rows(conn)

    story_fixes.apply(conn, {"removed": rows}, family=False)

    seq = conn.execute("SELECT last_read_seq FROM room_reads WHERE room_id = ?", (room,)).fetchone()[0]
    assert seq == 3


def _pair_room(conn, users, *names):
    room = room_store.create_room(conn, kind=RoomKind.PAIR, name="試しの部屋", member_ids=[users[name].id for name in names], moment=AT.isoformat())
    conversation = ledger.add_conversation(conn, ConversationDraft(conversation_date=AT.date(), trigger="試し", room_id=room.id, life_stage=ledger.life_stage_on(conn, AT.date())))
    return room.id, conversation.id


def _speak(conn, room_id, conversation_id, user, text, minute):
    moment = datetime(2036, 5, 3, 10, minute)
    room_store.say_in_ledger(conn, room_id=room_id, conversation_id=conversation_id, place=Place.CHAT, sender_user_id=user.id, text=text, sent_at=moment)


def _unread_total(conn, user_id):
    rooms = conn.execute(
        "SELECT m.room_id FROM room_members m JOIN rooms r ON r.id = m.room_id WHERE m.user_id = ? AND r.kind <> ?",
        (user_id, RoomKind.TWIN.value),
    ).fetchall()
    return sum(room_store.unread(conn, room_id, user_id)[0] for (room_id,) in rooms)


@pytest.fixture
def talk(conn, users):
    me, miho = users["誠一さん"], users["美穂さん"]
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "美穂さん")
    for minute, (person, text) in enumerate(
        [(miho, "一つ目。"), (me, "二つ目。"), (miho, "三つ目。"), (miho, "四つ目。"), (me, "五つ目。"), (miho, "六つ目。")]
    ):
        _speak(conn, room_id, conversation_id, person, text, minute)
    conn.commit()
    conn.isolation_level = None
    return room_id


def test_ほかの人の発言の最後の3件だけを未読に残す(conn, users, talk):
    me = users["誠一さん"].id
    assert room_store.unread(conn, talk, me) == (4, 1)

    story_fixes.apply(conn, {"removed": []}, family=False)

    # 美穂さんの四つの発言のうち、三つ目から未読。自分の発言は数えない
    assert room_store.unread(conn, talk, me) == (3, 3)


def test_ほかの人の発言が3件より少ない人は_全部が未読のまま(conn, users, talk):
    miho = users["美穂さん"].id

    story_fixes.apply(conn, {"removed": []}, family=False)

    assert room_store.unread(conn, talk, miho) == (2, 2)


def test_部屋をまたいで_いちばん新しい3件だけが残る(conn, users, talk):
    me, seko = users["誠一さん"], users["節子さん"]
    conn.isolation_level = ""
    room_id, conversation_id = _pair_room(conn, users, "誠一さん", "節子さん")
    _speak(conn, room_id, conversation_id, seko, "もっと新しい発言。", 30)
    conn.commit()
    conn.isolation_level = None

    story_fixes.apply(conn, {"removed": []}, family=False)

    assert _unread_total(conn, me.id) == 3
    assert room_store.unread(conn, room_id, me.id) == (1, 1)
    assert room_store.unread(conn, talk, me.id) == (2, 4)


def test_ツインとの部屋には読んだ位置を作らない(conn, users, story):
    conn.isolation_level = None

    story_fixes.apply(conn, {"removed": []}, family=False)

    assert conn.execute("SELECT COUNT(*) FROM room_reads WHERE room_id = ?", (_room(conn),)).fetchone()[0] == 0


def test_読んだ位置は戻さない(conn, users, talk):
    me = users["誠一さん"].id
    conn.execute(
        "INSERT INTO room_reads (room_id, user_id, last_read_seq, updated_at) VALUES (?, ?, 6, '2036-05-03T11:00:00+09:00')",
        (talk, me),
    )

    story_fixes.apply(conn, {"removed": []}, family=False)

    assert conn.execute("SELECT last_read_seq FROM room_reads WHERE room_id = ? AND user_id = ?", (talk, me)).fetchone()[0] == 6


def test_亡くなった方の読んだ位置は作らない(conn, users, talk):
    gone = users["和子さん"].id
    conn.execute("UPDATE users SET is_deceased = 1 WHERE id = ?", (gone,))
    conn.execute("INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?, ?, '2036-01-01T00:00:00+09:00')", (talk, gone))

    story_fixes.apply(conn, {"removed": []}, family=False)

    assert conn.execute("SELECT COUNT(*) FROM room_reads WHERE user_id = ?", (gone,)).fetchone()[0] == 0


def test_外したあとも部屋の行は追記だけの決まりで守られている(conn, story):
    conn.isolation_level = None
    story_fixes.apply(conn, {"removed": story_fixes.closing_rows(conn)[:1]}, family=False)

    with pytest.raises(Exception, match="追記だけ"):
        conn.execute("DELETE FROM room_messages")


def test_家系図の関係と候補の片付けは何度当てても同じになる(conn, users, story):
    utterance = conn.execute("SELECT id FROM utterances LIMIT 1").fetchone()["id"]
    for name in ("小雪", "夫"):
        conn.execute(
            "INSERT INTO person_candidates (name, relative_kind, gender, evidence_utterance_id, status, created_at) VALUES (?, 'その他', '不明', ?, '候補', '2036-05-01T00:00:00+09:00')",
            (name, utterance),
        )
    conn.commit()
    conn.isolation_level = None
    decisions = {"removed": []}

    first = story_fixes.apply(conn, decisions)
    second = story_fixes.apply(conn, decisions)

    assert first["rejected_candidates"] == 1
    assert second == {"hidden_tips": 0, "family_links": 0, "rejected_candidates": 0, "doctor": 0, "read_positions": 0, "doctor_entrusted": 0}
    assert first["family_links"] == conn.execute("SELECT COUNT(*) FROM family_links").fetchone()[0]
    statuses = dict(tuple(row) for row in conn.execute("SELECT name, status FROM person_candidates"))
    assert statuses == {"小雪": "ちがう", "夫": "候補"}


def test_家系図を足さない控えには足さない(conn, story):
    conn.isolation_level = None

    assert story_fixes.apply(conn, {"removed": []}, family=False)["family_links"] == 0
    assert conn.execute("SELECT COUNT(*) FROM family_links").fetchone()[0] == 0


def test_井ノ口先生に託す行を1回だけ足す(conn, story):
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name, relation, residence) VALUES (77, '井ノ口先生', '専門職', '井ノ口先生', '主治医', '近く')"
        )
    conn.isolation_level = None

    first = story_fixes.apply(conn, {"removed": []})
    second = story_fixes.apply(conn, {"removed": []})

    assert (first["doctor_entrusted"], second["doctor_entrusted"]) == (1, 0)
    row = conn.execute("SELECT scope, decided_on FROM delegations WHERE delegate_user_id = 77").fetchone()
    assert tuple(row) == ("意思決定支援用", story_fixes.DOCTOR_ENTRUSTED_ON)


def test_2026年の控えでは井ノ口先生に託さない(conn, story):
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, role, called_name, relation, residence) VALUES (77, '井ノ口先生', '専門職', '井ノ口先生', '主治医', '近く')"
        )
    conn.isolation_level = None

    assert story_fixes.apply(conn, {"removed": []}, family=False)["doctor_entrusted"] == 0


def test_自分用の置き場のデータベースは扱わない(tmp_path, capsys):
    folder = tmp_path / "mine"
    folder.mkdir()
    db_file = folder / "narrative_twin.db"
    db.init_db(db_file)

    assert story_fixes.main(["apply", str(db_file)]) == 1
