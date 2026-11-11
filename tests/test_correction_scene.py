"""訂正の場面のテスト。

役割：訂正の相手の推測が庭の話かを確かめ、違えば止まり、決めた順番で流し直すことを確かめる。
4つの極：3. 読み解き、積み上げる（架空の記録を作る道具）。
守る原則：4（AI の推測は推測として本人が答える）、8（人物と記録はすべて架空）。

本物の Claude API は呼ばない。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from app import ai
from fiction import correction_scene, run_script
from fiction.convert_chat import GUESS_DAY
from tests.fake_claude import _user_text, message, role_of
from tests.test_run_script_jobs import EchoClaude, _echo, close, query, say

CORRECTION = "庭が好きなのではなく、妻と過ごした場所だから大切"
GARDEN = "本当は庭と離れたくないのかもしれません。"
OTHER = "本当は誰かに気にかけてほしいのかもしれません。"
SCRIPT_GUESS = "庭のある暮らしそのものを大切にされているのかもしれません。"


class GuessClaude(EchoClaude):
    """推測の日の分割でだけ、決めた順に AI の推測を足す。流し直すたびに違う推測が出る形を作る。"""

    def __init__(self, guesses: list[str]) -> None:
        super().__init__()
        self.guesses = list(guesses)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if role_of(body) == "言明への分割" and "庭の世話" in _user_text(body).split("## 発言", 1)[1]:
            self.requests.append(request)
            answer = _echo(body)
            if self.guesses:
                first = answer["statements"][0]
                answer["statements"].append({**first, "source": "AIの推測", "speaker": "ツイン（AI）", "body": self.guesses.pop(0)})
            return httpx2.Response(200, json=message(json.dumps(answer, ensure_ascii=False), model=body["model"]))
        return super().__call__(request)


@pytest.fixture
def use_guesses(monkeypatch):
    def _use(*guesses: str) -> GuessClaude:
        fake = GuessClaude(list(guesses))
        monkeypatch.setattr(ai, "_make_client", fake.client)
        return fake

    return _use


def rows(*, with_script_guess: bool = False) -> list[dict]:
    lines = [
        say("2026-10-20", 10, "前の日の話です。"),
        close("2026-10-20"),
        say(GUESS_DAY, 10, "庭の世話があるけん、ここは離れられん。"),
        close(GUESS_DAY),
    ]
    if with_script_guess:
        lines.append({"at": f"{GUESS_DAY}T08:31", "kind": "推測", "by": "誠一さん", "text": SCRIPT_GUESS, "match": "庭の世話があるけん"})
    return [
        *lines,
        say("2026-10-28", 10, "後の日の話です。"),
        close("2026-10-28"),
        {"at": "2026-11-03T08:20", "kind": "訂正", "by": "誠一さん", "match": "庭", "guess": True, "text": CORRECTION},
    ]


@pytest.fixture
def scripts(tmp_path) -> tuple[Path, Path, Path]:
    def write(path: Path, lines: list[dict]) -> Path:
        path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")
        return path

    return (
        write(tmp_path / "台本.jsonl", rows()),
        write(tmp_path / "台本の文の版.jsonl", rows(with_script_guess=True)),
        tmp_path / "流し込み",
    )


def run_once(script: Path, folder: Path, *more: str) -> int:
    return run_script.main([str(script), "--folder", str(folder), "--budget", "50", *more])


def statements(folder: Path, source: str) -> list[str]:
    return [row["body"] for row in query(folder / "narrative_twin.db", "SELECT body FROM statements WHERE source = ?", source)]


def test_推測が庭の話なら訂正し_場面を残す(scripts, use_guesses, capsys):
    script, _, folder = scripts
    use_guesses(GARDEN)

    assert run_once(script, folder) == 0

    assert statements(folder, "本人の訂正") == [CORRECTION]
    scene = run_script.read_scene(folder)
    assert scene["guess"] == GARDEN
    assert scene["correction"] == CORRECTION
    assert scene["method"] == run_script.METHOD_AI
    assert scene["guess_day"] == GUESS_DAY
    assert GARDEN in capsys.readouterr().out


def test_推測が庭の話でなければ_訂正の前で止まる(scripts, use_guesses, capsys):
    # 本人の言葉には「庭」があるが、推測でなければ相手にしない
    script, _, folder = scripts
    use_guesses(OTHER)

    assert run_once(script, folder) == run_script.GUESS_EXIT

    assert statements(folder, "本人の訂正") == []
    assert run_script.read_scene(folder) is None
    out = capsys.readouterr().out
    assert "「庭」の話の AI の推測がありません" in out
    assert OTHER in out


def test_推測がないときも_訂正の前で止まる(scripts, use_guesses):
    script, _, folder = scripts
    use_guesses()

    assert run_once(script, folder) == run_script.GUESS_EXIT
    assert statements(folder, "本人の訂正") == []


def test_keep_goingでも_庭の話でなければ止まる(scripts, use_guesses):
    script, _, folder = scripts
    use_guesses(OTHER)

    assert run_once(script, folder, "--keep-going") == run_script.GUESS_EXIT


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("本当は庭と離れたくないのかもしれません。", True),
        ("この家と庭で暮らしたいのかもしれません。", True),
        ("家庭を大切にされているのかもしれません。", False),
        ("校庭の桜を覚えておられるのかもしれません。", False),
        ("本当は誰かに気にかけてほしいのかもしれません。", False),
    ],
)
def test_庭の話かどうかの見分け(body, expected):
    assert run_script.speaks_of(body, "庭") is expected


def test_庭の話でなければ_推測の日の前へ戻して流し直し_出たらそこで終わる(scripts, use_guesses):
    script, fallback, folder = scripts
    fake = use_guesses(OTHER, GARDEN)

    scene = correction_scene.run_to_scene(script, fallback, folder, options=["--budget", "50"])

    assert scene["guess"] == GARDEN
    assert scene["method"] == run_script.METHOD_AI
    assert scene["reruns"] == 1
    assert scene["script"] == str(script)
    # 戻す前の分は消さない
    assert len(list(folder.glob("replaced-*"))) == 1
    # 推測の日より前の行は流し直さない。言葉も重ならない
    assert statements(folder, "本人の言葉").count("前の日の話です。") == 1
    said = [_user_text(json.loads(request.content)) for request in fake.requests if role_of(json.loads(request.content)) == "言明への分割"]
    assert sum("前の日の話です。" in text.split("## 発言", 1)[1] for text in said) == 1
    assert statements(folder, "本人の訂正") == [CORRECTION]


def test_3回流し直しても出なければ_推測01を台本の文として入れる(scripts, use_guesses):
    script, fallback, folder = scripts
    use_guesses(OTHER, OTHER, OTHER, OTHER)

    scene = correction_scene.run_to_scene(script, fallback, folder, options=["--budget", "50"])

    assert scene["guess"] == SCRIPT_GUESS
    assert scene["method"] == run_script.METHOD_SCRIPT
    assert scene["reruns"] == correction_scene.MAX_RERUNS == 3
    assert scene["script"] == str(fallback)
    assert len(list(folder.glob("replaced-*"))) == 4
    assert statements(folder, "本人の訂正") == [CORRECTION]


def test_ほかの理由で止まったら_流し直さない(scripts):
    script, fallback, folder = scripts
    calls = []

    def stopped(args: list[str]) -> int:
        calls.append(args)
        return 2

    with pytest.raises(run_script.StopRun, match="流せませんでした"):
        correction_scene.run_to_scene(script, fallback, folder, run=stopped)
    assert len(calls) == 1


def test_場面のあとは_同じ台本を続きから流せる(scripts, use_guesses, tmp_path):
    script, fallback, folder = scripts
    use_guesses(GARDEN)
    correction_scene.run_to_scene(script, fallback, folder, options=["--budget", "50"])
    more = [*rows(), say("2026-11-05", 10, "そのあとの話です。"), close("2026-11-05")]
    script.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in more), encoding="utf-8")

    assert run_once(script, folder) == 0

    assert "そのあとの話です。" in statements(folder, "本人の言葉")
    assert statements(folder, "本人の訂正") == [CORRECTION]
