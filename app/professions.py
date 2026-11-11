"""専門職の職種と、職種ごとに使えるツール（決まった手順で動く層）。

役割：専門職を職種で分け、職種ごとに使えるツールを決める。
4つの極：4. 10年後に代弁する。
守る原則：1（判断するのは人）、10（見える範囲は本人が決める）。

ツールは「何を作れるか」だけを決め、見える範囲を広げない。見える範囲は app/consent.py が決める。
職種は users.relation に持つ。列を足すと、すべての起動でデータベースの変更が要るため。
職種を選ばずに招いた専門職は、これまでどおりツールを使える。職種を選ぶ前に招いた人の動きを変えないため。

理由：docs/decisions.md の「専門職の枠と、職種ごとのツール」
関連するテスト：tests/test_professions.py
"""

from __future__ import annotations

from app.schemas import Role, User

VOICE = "voice"
PLAN = "plan"
SHEET = "sheet"
LETTER = "letter"
# 画面に出す道具は、ツインにたずねる、ケア資料をチェックする、書類を作るの3つ（tyumajinさんの決定、2026-11-06。見出しの名前は 11/7 に変えた）。
# 書類を作るには、手紙と生活まとめプランが入る（tyumajinさんの決定で「手紙を作る」から名前を変えた）。
# 1枚の紙は手紙に置き換えたので画面には出さない（static/tools.js）。作る処理と API は残している。
TOOL_NAMES = {VOICE: "ツインにたずねる", PLAN: "ケア資料をチェックする", LETTER: "書類を作る", SHEET: "1枚の紙"}
ALL_TOOLS = (VOICE, PLAN, LETTER, SHEET)

CARE_MANAGER = "ケアマネジャー"
DOCTOR = "主治医"
# 招く職種を増やす（tyumajinさんの依頼、2026-11-08）。主治医と同じく、ツールはなく、本人から託されたらケア会議を使える。
# 見える範囲は職種では変えない（app/consent.py は役割と託す設定だけで決め、職種を見ない）。並びは招く窓の選択肢の順
MORE_PROFESSIONS = ("歯科医", "ヘルパー", "訪問看護", "包括支援センター", "商店")
TOOLS_BY_PROFESSION: dict[str, tuple[str, ...]] = {CARE_MANAGER: ALL_TOOLS, DOCTOR: (), **{name: () for name in MORE_PROFESSIONS}}


def profession_of(user: User) -> str | None:
    """職種を返す。専門職でない人と、職種を選ばずに招いた人は None。"""
    if user.role is Role.PROFESSIONAL and user.relation in TOOLS_BY_PROFESSION:
        return user.relation
    return None


def tools_of(user: User) -> tuple[str, ...]:
    """この人の立場と職種で使えるツールを返す。託されているかは呼ぶ側が確かめる。"""
    if user.role is not Role.PROFESSIONAL:
        return ()
    return TOOLS_BY_PROFESSION.get(user.relation or "", ALL_TOOLS)


def tool_views(tools: tuple[str, ...]) -> list[dict]:
    """画面に返す形。"""
    return [{"id": tool, "name": TOOL_NAMES[tool]} for tool in tools]


def profession_views() -> list[dict]:
    """招く画面の選択肢。職種ごとに使えるツールを添える。"""
    return [{"name": name, "tools": tool_views(tools)} for name, tools in TOOLS_BY_PROFESSION.items()]
