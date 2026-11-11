"""本人の今を代弁する手紙のテスト。

役割：手紙が、本人か託された家族の確かめを経てから専門職に届くこと、札が確かめた人で変わること、
     直しが直した人の言葉として台帳に入ることを確かめる。
4つの極：4. 10年後に代弁する。
守る原則：1（返すか出さないかは本人と家族が決める）、3（ツインの文と記録を分ける）、9（引く言葉は記録の本文のまま）、
          12（紙に必ず「ツイン（AI）」と出す）。

通信だけを差し替える。本物の Claude API は呼ばない。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import advocacy_letter, ai, ledger, model_store
from app.schemas import ConfirmStatus, ConsolidationUnit, ElementKind, ElementStatus, ShareScope, Source, Theme
from tests.fake_claude import RoleClaude

RICE = "冷や飯なんざ食えるか。飯は炊きたてが一番たい。"
BODY = "田中さん、元気にしとります。飯は今も自分で炊いとる。ボタンを忘れる日もあるが、まあよか。"


def _letter(**changes):
    return {
        "body": BODY,
        "requests": ["来るときは、先に電話ばくれんね。"],
        "quote_number": None,
        "piece_numbers": [],
        "illustration_number": None,
        **changes,
    }


@pytest.fixture
def claude(monkeypatch):
    fake = RoleClaude()
    fake.answers["ツインの答え"] = {"direct_numbers": [1], "related_numbers": [], "request_kind": "通常"}
    fake.answers["手紙"] = _letter()
    monkeypatch.setattr(ai, "_make_client", fake.client)
    return fake


@pytest.fixture
def words(conn, add_statement):
    rice = add_statement(RICE, scope=ShareScope.DECISION_SUPPORT, theme=Theme.LIKES, effective_date=date(2026, 10, 11))
    model_store.add_element(
        conn,
        kind=ElementKind.VALUE,
        body="暮らしのこだわりです。",
        condition=None,
        petals=[],
        confidence=0.7,
        status=ElementStatus.AI_VIEW,
        valid_from=date(2026, 1, 1),
        evidence_statement_ids=[rice.id],
        unit=ConsolidationUnit.YEAR,
    )
    conn.commit()
    return rice


def _make(client, key="l1", purpose="今月の様子"):
    return client.post("/api/advocacy/letters", json={"client_key": key, "purpose": purpose})


def _current(conn, body: str):
    return [item for item in ledger.current_statements(conn) if item.body == body]


def test_札は確かめた人で書き分ける():
    head = "ツイン（AI）が誠一さんに代わって書きました。"
    assert advocacy_letter.label_for("誠一さん", ["本人"]) == f"{head}誠一さんも確認されました。"
    assert advocacy_letter.label_for("誠一", ["家族"]) == f"{head}ご家族も確認されました。"
    assert advocacy_letter.label_for("誠一さん", ["家族", "本人"]) == f"{head}誠一さんとご家族も確認されました。"


def test_手紙は確かめるまで専門職に中身が見えない(login, words, claude):
    tanaka = login("田中さん")
    letter = _make(tanaka).json()["letter"]
    assert letter["status"] == "確かめ待ち"
    assert "paper" not in letter
    assert all("paper" not in item for item in tanaka.get("/api/advocacy/letters").json()["items"])
    assert "paper" not in tanaka.get(f"/api/advocacy/letters/{letter['id']}").json()["letter"]
    assert claude.bodies("手紙")[0]["model"] == "claude-opus-5-5"


def test_本人が確かめて返すと専門職に届く(login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    seiichi = login("誠一さん")
    pending = seiichi.get("/api/letters/pending").json()["items"]
    assert [item["id"] for item in pending] == [letter_id]
    assert pending[0]["recipient"] == "田中さん"
    # 知らせの行の頭に、頼んだ人の顔を出すための情報
    assert pending[0]["requester_id"] == login("田中さん").get("/api/me").json()["id"]
    check = seiichi.get(f"/api/letters/{letter_id}/check").json()["letter"]
    assert check["body"] == BODY
    assert check["label"] == "ツイン（AI）が誠一さんに代わって書きました。誠一さんも確認されました。"

    seiichi.post(f"/api/letters/{letter_id}/return")

    letter = login("田中さん").get(f"/api/advocacy/letters/{letter_id}").json()["letter"]
    assert letter["status"] == "返した"
    assert letter["paper"]["body"] == BODY
    assert letter["paper"]["requests"] == ["来るときは、先に電話ばくれんね。"]
    assert letter["paper"]["recipient"] == "田中さん"
    assert letter["paper"]["label"] == "ツイン（AI）が誠一さんに代わって書きました。誠一さんも確認されました。"
    assert seiichi.get("/api/letters/pending").json()["items"] == []


def test_家族が確かめると札がご家族になる(login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    login("美穂さん").post(f"/api/letters/{letter_id}/return")
    paper = login("田中さん").get(f"/api/advocacy/letters/{letter_id}").json()["letter"]["paper"]
    assert paper["label"] == "ツイン（AI）が誠一さんに代わって書きました。ご家族も確認されました。"


def test_家族が直して本人が返すと両方の札になる(login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    login("美穂さん").post(f"/api/letters/{letter_id}/edit", json={"body": BODY + "美穂も来てくれる。", "requests": []})
    login("誠一さん").post(f"/api/letters/{letter_id}/return")
    paper = login("田中さん").get(f"/api/advocacy/letters/{letter_id}").json()["letter"]["paper"]
    assert paper["label"] == "ツイン（AI）が誠一さんに代わって書きました。誠一さんとご家族も確認されました。"


def test_本人の直しは本人の言葉として台帳に入り返すと確認済みになる(conn, login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    seiichi = login("誠一さん")
    added = "庭の草取りは、自分でしとる。"
    edited = seiichi.post(f"/api/letters/{letter_id}/edit", json={"body": BODY + added, "requests": ["来るときは、先に電話ばくれんね。"]}).json()["letter"]
    assert edited["body"] == BODY + added
    assert edited["edited"] is True
    # 返すまでは台帳に入らない
    assert _current(conn, added) == []

    seiichi.post(f"/api/letters/{letter_id}/return")

    [statement] = _current(conn, added)
    assert statement.source is Source.SELF_WORDS
    assert statement.confirm_status is ConfirmStatus.CONFIRMED
    assert statement.share_scope is ShareScope.DECISION_SUPPORT
    # AI が書いた最初の版の文は、本人の言葉として足さない
    assert _current(conn, "飯は今も自分で炊いとる。") == []
    # ツインにだけ話した言葉と見なさない場所で残す
    from app import consent

    assert statement.id not in consent.only_to_twin_ids(conn)


def test_家族の直しは家族の証言になる(conn, login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    added = "夜はよく眠れとるようです。"
    login("美穂さん").post(f"/api/letters/{letter_id}/edit", json={"body": BODY + added, "requests": []})
    login("美穂さん").post(f"/api/letters/{letter_id}/return")
    [statement] = _current(conn, added)
    assert statement.source is Source.FAMILY_TESTIMONY
    assert statement.confirm_status is ConfirmStatus.UNCONFIRMED


def test_出さないと押した手紙は届かず返せない(login, words, claude):
    tanaka = login("田中さん")
    letter_id = _make(tanaka).json()["letter"]["id"]
    seiichi = login("誠一さん")
    assert seiichi.post(f"/api/letters/{letter_id}/decline").json()["letter"]["status"] == "出さない"
    assert seiichi.post(f"/api/letters/{letter_id}/return").status_code == 400
    assert login("美穂さん").post(f"/api/letters/{letter_id}/return").status_code == 400
    letter = tanaka.get(f"/api/advocacy/letters/{letter_id}").json()["letter"]
    assert letter["status"] == "出さない"
    assert "paper" not in letter


def test_手紙を確かめられるかを_meで返す(login, words):
    # 画面は、これが真の人だけ確かめ待ちを問い合わせる。確かめられない人の 404 をブラウザの記録に残さないため
    for name, expected in (("誠一さん", True), ("美穂さん", True), ("田中さん", False), ("正雄さん", False)):
        assert login(name).get("/api/me").json()["can_check_letters"] is expected


def test_確かめられるのは本人と託された家族だけ(login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    for name in ("田中さん", "正雄さん"):
        client = login(name)
        assert client.get("/api/letters/pending").status_code == 404
        assert client.get(f"/api/letters/{letter_id}/check").status_code == 404
        assert client.post(f"/api/letters/{letter_id}/return").status_code == 404


def test_手紙の道具のない人は頼めない(login, words, claude):
    assert _make(login("美穂さん")).status_code == 404


def test_引く言葉は番号から記録の本文で入る(login, words, claude):
    claude.answers["手紙"] = _letter(quote_number=1)
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    login("誠一さん").post(f"/api/letters/{letter_id}/return")
    quote = login("田中さん").get(f"/api/advocacy/letters/{letter_id}").json()["letter"]["paper"]["quote"]
    assert quote["body"] == RICE
    assert quote["source"] == "本人の言葉"


def test_鉤括弧は本文に入れられない(login, words, claude):
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    response = login("誠一さん").post(f"/api/letters/{letter_id}/edit", json={"body": "「飯は炊く」", "requests": []})
    assert response.status_code == 400


def test_挿絵のファイルがなければ挿絵なしで出しAIにも渡さない(login, words, claude, monkeypatch, tmp_path):
    monkeypatch.setattr(advocacy_letter, "ART_DIR", tmp_path / "絵はまだない")
    claude.answers["手紙"] = _letter(illustration_number=1)
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    assert "## 挿絵の一覧\nありません。" in claude.bodies("手紙")[0]["messages"][0]["content"]
    check = login("誠一さん").get(f"/api/letters/{letter_id}/check").json()["letter"]
    assert check["illustration"] is None


def test_挿絵は番号で選んだ絵を出す(login, words, claude, monkeypatch, tmp_path):
    art_dir = tmp_path / "letter_art"
    art_dir.mkdir()
    (art_dir / "01.webp").write_bytes(b"webp")
    monkeypatch.setattr(advocacy_letter, "ART_DIR", art_dir)
    claude.answers["手紙"] = _letter(illustration_number=1)
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    assert "[1] 縁側と座布団二枚（通年）" in claude.bodies("手紙")[0]["messages"][0]["content"]
    check = login("誠一さん").get(f"/api/letters/{letter_id}/check").json()["letter"]
    assert check["illustration"] == {"src": "letter_art/01.webp", "title": "縁側と座布団二枚"}


def test_チャットの手紙の語は手紙を頼む(login, words, claude):
    reply = login("田中さん").post("/api/advocacy/chat", json={"question": "誠一さんに手紙を書いてもらえますか", "client_key": "c1"}).json()
    assert reply["kind"] == "手紙"
    assert reply["letter"]["status"] == "確かめ待ち"


def test_同じ送り直しでは手紙を増やさない(login, words, claude):
    tanaka = login("田中さん")
    assert _make(tanaka).json()["letter"]["id"] == _make(tanaka).json()["letter"]["id"]
    assert len(claude.bodies("手紙")) == 1


def test_手紙の文に服薬の指示があればやり直す(login, words, claude):
    claude.answers["手紙"] = [_letter(body="薬は飲まなくても大丈夫です。"), _letter()]
    letter_id = _make(login("田中さん")).json()["letter"]["id"]
    assert len(claude.bodies("手紙")) == 2
    assert login("誠一さん").get(f"/api/letters/{letter_id}/check").json()["letter"]["body"] == BODY


def test_本文の宛名と署名は外す():
    body = "田中さんへ。\n五月になった。\n飯は炊いた。\n誠一"
    assert advocacy_letter.strip_address_and_name(body, "田中さん", "誠一さん") == "五月になった。\n飯は炊いた。"
    # 本文の中の名前は外さない
    assert advocacy_letter.strip_address_and_name("田中さんに会うた。\n誠一は元気たい。", "田中さん", "誠一さん") == "田中さんに会うた。\n誠一は元気たい。"


def test_手紙の材料に専門職だけの記録を入れない(conn, login, users, words, claude):
    from datetime import datetime

    from app import observations

    tanaka, seiichi = users["田中さん"], users["誠一さん"]
    at = datetime(2036, 5, 6, 10, 0).astimezone()
    observations.save_observation(conn, tanaka, seiichi, "台所はよく片付いていた。", Theme.HOME, at)
    observations.save_observation(
        conn, tanaka, seiichi, "アセスメントの見立てです。", Theme.HOME, at, share_scope=ShareScope.PROFESSIONAL_ONLY
    )
    conn.commit()
    _make(login("田中さん"))
    text = claude.bodies("手紙")[0]["messages"][0]["content"]
    assert "台所はよく片付いていた。" in text
    assert "アセスメントの見立てです。" not in text


def test_頼んだ専門職は手紙を削除でき_確かめ待ちからも消えるが_直した言葉は台帳に残る(conn, login, words, claude):
    tanaka, seiichi = login("田中さん"), login("誠一さん")
    letter_id = _make(tanaka).json()["letter"]["id"]
    seiichi.post(f"/api/letters/{letter_id}/edit", json={"body": "米は自分で研ぐ。", "requests": []})
    before = len(_current(conn, "米は自分で研ぐ。"))

    # ほかの人は消せない（あることも見せない）
    assert login("美穂さん").delete(f"/api/advocacy/made/letter/{letter_id}").status_code == 404
    assert tanaka.delete(f"/api/advocacy/made/letter/{letter_id}").status_code == 204

    assert tanaka.get("/api/advocacy/letters").json()["items"] == []
    assert tanaka.get(f"/api/advocacy/letters/{letter_id}").status_code == 404
    assert seiichi.get("/api/letters/pending").json()["items"] == []
    assert seiichi.get(f"/api/letters/{letter_id}/check").status_code == 404
    # 書類から台帳に入った言葉は消さない（原則5）
    assert len(_current(conn, "米は自分で研ぐ。")) == before
    # 2回目は、もうないものとして扱う
    assert tanaka.delete(f"/api/advocacy/made/letter/{letter_id}").status_code == 404
