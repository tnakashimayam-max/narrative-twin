"""「10年後へ進む」と架空ログの読み込みのテスト。

役割：保存した JSON から AI を呼ばずに作れること、2回押しても増えないこと、本人が決めるまで進めないことを確かめる。
4つの極：3. 読み解き、4. 10年後に代弁する。
守る原則：8（自分用の起動では動かない）、9（本人の言葉は一字も変えない）、10（本人が託す範囲を決める）。

物語の小さな見本をこのファイルの中で作り、data/story/ の本物は読まない。
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import ai, clock, config, db, demo, ledger, model_store
from app.main import create_app
from app.schemas import ConfirmStatus, LifeStage, ShareScope, Theme
from fiction import load, plan, snapshot, stage
from tests.fake_claude import RoleClaude

BEFORE = plan.PlanItem(
    key="2026-10-18",
    day=date(2026, 10, 18),
    stage=LifeStage.ACTIVE,
    age=74,
    theme=Theme.HOME,
    topic="庭",
    setting="",
    must_say="できれば最後まで、この家で庭を見ていたい。",
    scene="",
)
AFTER = plan.PlanItem(
    key="2033-06-12",
    day=date(2033, 6, 12),
    stage=LifeStage.SUPPORT,
    age=81,
    theme=Theme.HOME,
    topic="施設",
    setting="",
    commenters=("美穂さん",),
)
PRIVATE = plan.PlanItem(
    key="2030-03-03",
    day=date(2030, 3, 3),
    stage=LifeStage.ACTIVE,
    age=78,
    theme=Theme.HARDSHIP,
    topic="兄",
    setting="",
    private=True,
)


def _turns(*self_words):
    turns = []
    for words in self_words:
        turns.append({"speaker": "ツイン", "text": "いかがですか？"})
        turns.append({"speaker": "誠一さん", "text": words})
    return turns


def _statement(body, number, theme="住まい・土地", source="本人の言葉", speaker="誠一さん"):
    return {
        "source": source,
        "subject": "誠一さん",
        "speaker": speaker,
        "body": body,
        "period": "今",
        "theme": theme,
        "petals": ["くつろぎ"],
        "utterance_numbers": [number],
        "interpretations": [],
        "confirm_question": None,
    }


LOGS = {
    "2026-10-18": {
        "trigger": "庭",
        "turns": _turns("できれば最後まで、この家で庭を見ていたい。"),
        "confirmation": "合っています",
        "letter": None,
        "comments": [],
    },
    "2030-03-03": {
        "trigger": "兄",
        "turns": _turns("兄とは仲直りできなかった。"),
        "confirmation": "合っています",
        "letter": None,
        "comments": [],
    },
    "2033-06-12": {
        "trigger": "施設",
        "turns": _turns("施設のことも考えていい。"),
        "confirmation": "合っています",
        "letter": "施設の話をしました。",
        "comments": [{"speaker": "美穂さん", "text": "父は施設だけは嫌だと言っていました。"}],
    },
}
SPLITS = {
    "conv-2026-10-18": {"statements": []},
    "conv-2030-03-03": {"statements": [_statement("兄とは仲直りできなかった。", 2, theme="家族")]},
    "conv-2033-06-12": {"statements": [_statement("施設のことも考えていい。", 2)]},
    "letter-2033-06-12": {
        "statements": [_statement("父は施設だけは嫌だと言っていました。", 1, source="家族の証言", speaker="美穂さん")]
    },
}
RUNS = [
    {
        "id": "month-2026-10-01",
        "unit": "月",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "model": "claude-sonnet-5-5",
        "input_tokens": 10,
        "output_tokens": 5,
        "input_keys": ["conv-2026-10-18#fixed"],
        "elements": [
            {
                "kind": "価値",
                "body": "誠一さんはこの家で暮らしたい。",
                "petals": ["くつろぎ"],
                "confidence": 0.8,
                "evidence_keys": ["conv-2026-10-18#fixed"],
            }
        ],
    },
    {
        "id": "year-2033-01-01",
        "unit": "年",
        "start": "2033-01-01",
        "end": "2033-12-31",
        "model": "claude-opus-5-5",
        "input_tokens": 10,
        "output_tokens": 5,
        "input_keys": ["conv-2033-06-12#0"],
        "elements": [
            {
                "kind": "価値",
                "body": "誠一さんは施設のことも考えている。",
                "petals": ["結びつき"],
                "confidence": 0.7,
                "evidence_keys": ["conv-2033-06-12#0"],
            }
        ],
    },
]


@pytest.fixture
def story(monkeypatch, tmp_path):
    """小さな物語を data/story/ の代わりに置く。"""
    folder = tmp_path / "story"
    monkeypatch.setattr(load, "STORY_DIR", folder)
    monkeypatch.setattr(plan, "build_plan", lambda: [BEFORE, PRIVATE, AFTER])
    monkeypatch.setattr(plan, "FIXED_OBSERVATIONS", [plan.FixedObservation(date(2033, 7, 1), "美穂さん", "朝は窓の近くにいます。")])
    load.write_logs(LOGS)
    load.write_splits(SPLITS)
    (folder / "model_2026.json").write_text(json.dumps(RUNS, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def fresh(tmp_path, story):
    """架空の5人だけのデータベースに、デモの日より前を入れたアプリ。"""
    deployment = config.deployment(data_dir=tmp_path / "d")
    app = create_app(deployment=deployment)
    connection = db.connect(deployment.db_path)
    assert stage.load_prologue(connection) is True
    users = {user.name: user for user in db.list_users(connection)}
    yield app, connection, lambda name: TestClient(app, headers={"X-NT-User": str(users[name].id)}), users
    connection.close()


def _set_handover(client, users):
    themes = [theme.value for theme in Theme if theme is not Theme.HARDSHIP]
    assert client.put("/api/handover", json={"themes": themes, "delegate_ids": [users["美穂さん"].id]}).status_code == 200


def test_読み込みにAIを呼ばず_デモの日より前だけが入る(fresh, claude):
    _, conn, _, _ = fresh

    bodies = {statement.body for statement in ledger.current_statements(conn)}

    assert claude.requests == []
    assert "できれば最後まで、この家で庭を見ていたい。" in bodies
    assert "施設のことも考えていい。" not in bodies


def test_分割で落ちた決めた言葉も言明に入る(fresh):
    _, conn, _, _ = fresh

    statement = next(item for item in ledger.current_statements(conn) if item.body.startswith("できれば"))

    assert statement.source.value == "本人の言葉"


def test_デモの日より前は確かめの行を入れず_色が出ない(fresh):
    _, _, person, _ = fresh

    assert person("誠一さん").get("/api/twin/appearance").json()["levels"] == ["なし"] * 5


def test_デモの訂正に使うAIの推測が入る(fresh):
    _, conn, _, _ = fresh

    assert plan.DEMO_GUESS in {statement.body for statement in ledger.current_statements(conn)}


def test_読み込みを2回呼んでも増えない(fresh):
    _, conn, _, _ = fresh
    before = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]

    assert stage.load_prologue(conn) is False

    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == before


def test_読み込みは途中で失敗すると何も残らない(tmp_path, story, monkeypatch):
    path = tmp_path / "broken.db"
    db.init_db(path)
    connection = db.connect(path)
    monkeypatch.setattr(load, "load_model_runs", lambda *_: (_ for _ in ()).throw(RuntimeError("途中の失敗")))

    with pytest.raises(RuntimeError):
        stage.load_prologue(connection)

    assert connection.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM story_marks").fetchone()[0] == 0
    connection.close()


def test_託す設定がなければ進めない(fresh, claude):
    _, _, person, _ = fresh

    assert person("誠一さん").post("/api/dev/advance").status_code == 400


def test_本人以外は進められない(fresh, claude):
    _, _, person, _ = fresh

    assert person("美穂さん").post("/api/dev/advance").status_code == 404


def test_自分用の起動では10年後へ進むが404(tmp_path):
    client = TestClient(create_app(deployment=config.deployment(mine=True, data_dir=tmp_path)))
    self_id = client.post("/api/self", json={"name": "山田一郎"}).json()["id"]
    client.headers["X-NT-User"] = str(self_id)

    assert client.post("/api/dev/advance").status_code == 404
    assert client.get("/api/dev/story").status_code == 404


def test_進むと2036年になり_10年分が入り_確かめの行は物語の日付になる(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)

    body = me.post("/api/dev/advance").json()

    assert body["today"] == plan.FUTURE_DAY.isoformat()
    assert body["future_loaded"] is True
    confirmed = next(item for item in ledger.current_statements(conn) if item.body == "施設のことも考えていい。")
    assert confirmed.confirm_status is ConfirmStatus.CONFIRMED
    assert confirmed.created_at.year == 2033
    assert confirmed.share_scope is ShareScope.DECISION_SUPPORT


def test_10年後へ進むを2回押しても2回読み込まれない(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)

    me.post("/api/dev/advance")
    before = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
    me.post("/api/dev/advance")

    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == before


def test_進んだあとで起動し直しても2036年のままで招いた人が入れる(fresh, claude):
    app, _, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")
    invited = person("美穂さん").post("/api/people/professional", json={"name": "田中さん", "relation": "ケアマネジャー"})

    restarted = create_app(deployment=app.state.deployment)
    assert demo.restore_day(restarted) is True

    assert clock.now(restarted).date() == plan.FUTURE_DAY
    client = TestClient(restarted, headers={"X-NT-User": str(invited.json()["id"])})
    assert client.get("/api/me").status_code == 200


def test_進む前に起動し直しても日付は動かない(fresh):
    app, _, _, _ = fresh

    restarted = create_app(deployment=app.state.deployment)

    assert demo.restore_day(restarted) is False
    assert clock.now(restarted).date() == date.today()


def _tanaka_exists(connection):
    return any(user.name == "田中さん" for user in db.list_users(connection))


def test_進む直前に控えを取り_控えには進む前の状態が入る(fresh, claude):
    app, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    before = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]

    me.post("/api/dev/advance")

    saved = snapshot.list_snapshots(app.state.deployment)[-1]
    assert saved.name.endswith(snapshot.BEFORE_ADVANCE)
    copied = db.connect(saved.db_path)
    assert copied.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == before
    assert copied.execute("SELECT 1 FROM story_marks WHERE stage = 'future'").fetchone() is None
    copied.close()


def test_2回押しても控えは1つだけ増える(fresh, claude):
    app, _, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)

    me.post("/api/dev/advance")
    me.post("/api/dev/advance")

    assert len(snapshot.list_snapshots(app.state.deployment)) == 1


def test_控えを取れなければ進まない(fresh, claude, monkeypatch):
    app, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)

    def broken(*_):
        raise OSError("書けない")

    monkeypatch.setattr(snapshot, "save", broken)
    assert me.post("/api/dev/advance").status_code == 503

    assert stage.is_loaded(conn, "future") is False
    assert clock.now(app).date() == date.today()


def test_控えへ戻すと2026年に戻り_田中さんは消え_もう一度10年後へ進める(fresh, claude, monkeypatch):
    app, conn, person, users = fresh
    monkeypatch.setattr(snapshot, "server_is_running", lambda port: False)
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")
    person("美穂さん").post("/api/people/professional", json={"name": "田中さん", "relation": "ケアマネジャー"})
    assert _tanaka_exists(conn)
    deployment = app.state.deployment
    # 本物と同じく、サーバーを止めてから戻す。Windows では開いているファイルを移せない
    conn.close()

    snapshot.restore(deployment)

    # 起動し直したのと同じ手順を通す
    restarted = create_app(deployment=deployment)
    assert demo.ensure_prologue(deployment) is False
    assert demo.restore_day(restarted) is False
    assert clock.now(restarted).date() == date.today()
    connection = db.connect(deployment.db_path)
    assert not _tanaka_exists(connection)
    assert stage.is_loaded(connection, "future") is False
    client = TestClient(restarted, headers={"X-NT-User": str(users["誠一さん"].id)})
    assert client.get("/api/dev/story").json()["future_loaded"] is False

    body = client.post("/api/dev/advance").json()

    assert body["future_loaded"] is True
    assert body["today"] == plan.FUTURE_DAY.isoformat()
    bodies = [statement.body for statement in ledger.current_statements(connection)]
    assert bodies.count("施設のことも考えていい。") == 1
    connection.close()


def test_最初の起動のときに控えを取り_2回目の起動では取らない(tmp_path, story):
    deployment = config.deployment(data_dir=tmp_path / "d")
    create_app(deployment=deployment)

    assert demo.ensure_prologue(deployment) is True
    assert demo.ensure_prologue(deployment) is False

    saved = snapshot.list_snapshots(deployment)
    assert [item.name.endswith(snapshot.FIRST_START) for item in saved] == [True]


def test_本人だけの話は託すテーマでも広がらない(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")

    statement = next(item for item in ledger.current_statements(conn) if item.body.startswith("兄とは"))

    assert statement.theme is Theme.HARDSHIP
    assert statement.share_scope is ShareScope.SELF_ONLY


def test_進むとその場で本人が確かめた言葉はそのまま残る(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    guess = next(item for item in ledger.current_statements(conn) if item.body == plan.DEMO_GUESS)
    me.post(f"/api/statements/{guess.id}/correct", json={"text": "妻と過ごした場所だから大切です。"})
    _set_handover(me, users)

    me.post("/api/dev/advance")

    head = ledger.head_of(conn, guess.id)
    assert head.body == "妻と過ごした場所だから大切です。"
    assert head.source.value == "本人の訂正"
    assert head.share_scope is ShareScope.DECISION_SUPPORT


def test_進むと今の様子が入り_田中さんを招ける(fresh, claude):
    _, _, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")

    invited = person("美穂さん").post("/api/people/professional", json={"name": "田中さん", "relation": "ケアマネジャー"})

    assert invited.status_code == 201
    tanaka = TestClient(me.app, headers={"X-NT-User": str(invited.json()["id"])})
    assert tanaka.get("/api/advocacy/overview").json()["now"][0]["body"] == "朝は窓の近くにいます。"


def test_進んだあとライブの言明が年の要素の根拠からたどれる(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    guess = next(item for item in ledger.current_statements(conn) if item.body == plan.DEMO_GUESS)
    me.post(f"/api/statements/{guess.id}/correct", json={"text": "妻と過ごした場所だから大切です。"})
    _set_handover(me, users)
    claude.answers["固める処理"] = {
        "elements": [
            {"kind": "価値", "body": "誠一さんは妻と過ごした場所を大切にしている。", "petals": ["結びつき"], "confidence": 0.8, "evidence_numbers": [1, 2]}
        ]
    }

    me.post("/api/dev/advance")

    years = [item for item in model_store.active_elements(conn) if item.body.startswith("誠一さんは妻と")]
    assert {element.unit.value for element in years} >= {"月", "年"}
    root = ledger.root_of(conn, ledger.head_of(conn, guess.id).id)
    assert any(root in element.evidence_statement_ids for element in years)


def test_起動のときの流し直しで架空ログをAIで分け直さない(fresh, claude):
    from app import jobs

    app, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")
    claude.requests.clear()
    before = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]

    jobs.catch_up(app)

    assert [body for body in claude.bodies() if "記録の整理係" in json.dumps(body, ensure_ascii=False)] == []
    assert conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0] == before


def test_保存した結果は言明を鍵で指す(fresh, claude):
    _, conn, person, users = fresh
    me = person("誠一さん")
    _set_handover(me, users)
    me.post("/api/dev/advance")

    element = next(item for item in model_store.active_elements(conn) if item.body == "誠一さんは施設のことも考えている。")

    assert ledger.head_of(conn, element.evidence_statement_ids[0]).body == "施設のことも考えていい。"
