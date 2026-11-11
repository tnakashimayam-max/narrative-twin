"""ツインに聞くチャットから、アドボカシーの3つの処理を呼ぶ。

役割：チャットの言葉を見分けて、ふだんの問い、ツインの考え、1枚の紙、ケアプラン案へ渡す。処理そのものはボタンと同じものを使う。
4つの極：4. 10年後に代弁する。
守る原則：1（判断は人。ここでは呼び分けるだけ）、9（本人の言葉の扱いは呼び先のまま）。

見分けるのはプログラムの語の一覧で、AI を呼ばない。動画でも毎回同じ処理へ渡すため。当てはまらない言葉はふだんの問いになる。
ふだんの問いには、託された人（家族と専門職）のだれにでも、ツインの考え（代弁）で答える（原則16、tyumajinさんの決定）。
集めた言葉の一覧は材料にとどめ、応答に入れない。託された家族の答えには、理由ごとに本人の元の発言を添える（app/advocacy_voice.py）。
ケアプラン案と1枚の紙は、これまでどおりツールを使える人だけが作れる。
「伝えます」などの語は、専門職がツインに伝える処理へ渡す（app/professional_records.py）。分けた項目を返し、残すかは人が決める。
「手紙」の語は、本人の今を代弁する手紙を頼む処理へ渡す（app/advocacy_letter.py）。手紙の道具を使える人だけ。

関連するテスト：tests/test_advocacy_chat.py
API の取り決め：docs/api.md
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from app import advocacy, advocacy_letter, advocacy_plan, advocacy_sheet, advocacy_voice, professional_records, professions
from app.auth import current_user, get_conn
from app import made_documents
from app.schemas import MadeDocumentKind, User

router = APIRouter(prefix="/api/advocacy")

ASK = "問い"
VOICE = "ツインの考え"
ASK_AND_VOICE = "問いとツインの考え"
SHEET = "1枚の紙"
PLAN = "ケアプラン案"
REVISE = "第2版"
CHOOSE = "版の選択"
TELL = "伝える"
LETTER = "手紙"

_TOOL_OF_KIND = {
    VOICE: professions.VOICE,
    ASK_AND_VOICE: professions.VOICE,
    SHEET: professions.SHEET,
    PLAN: professions.PLAN,
    REVISE: professions.PLAN,
    CHOOSE: professions.PLAN,
    LETTER: professions.LETTER,
}

_VOICE_WORDS = ("ツインはどう", "ツインの考え", "ツインの意見", "ツインは何", "どう思いますか", "どう思う", "どう考えますか")
_SHEET_WORDS = ("1枚の紙", "１枚の紙", "一枚の紙", "紙を作", "手帳を作")
_AGAIN_WORDS = ("作り直", "もう一度")
_REVISE_WORDS = ("第2版", "第２版", "第二版", "案を書き直")
# 版を選ぶ言葉。「第2版」を含むので、書き直しより先に見る
_CHOICE_WORDS = {
    "第2版にします": advocacy_plan.SECOND,
    "第2版を使": advocacy_plan.SECOND,
    "第1版にします": advocacy_plan.FIRST,
    "第1版を使": advocacy_plan.FIRST,
    "どちらも使わない": advocacy_plan.NEITHER,
}
_PLAN_WORDS = ("という案", "の案を", "この案", "ケアプラン案", "ケアプランの案")
# 伝える語。問いの語より先に見る。「伝えます。どう思いますか」は、伝えたあとで聞く形にせず、伝えるだけにする
_TELL_WORDS = ("伝えます", "伝えておきます", "お伝えします", "報告します", "ご報告", "共有します", "お知らせします")
_LETTER_WORDS = ("手紙を書", "手紙を作", "手紙をお願い", "お手紙")
# この字数までの「どう思いますか」は、前の問いへの続きとして読む。長ければ、問いも一緒に書かれている
_SHORT_FOLLOW_UP = 20


def chat_intent(text: str) -> str:
    """チャットの言葉を、どの処理へ渡すかに分ける。

    「案」の言葉を先に見る。「週2回のデイサービスという案をどう思いますか」は、ツインの考えではなく案への異議にするため。
    """
    if any(word in text for word in _TELL_WORDS):
        return TELL
    if any(word in text for word in _LETTER_WORDS):
        return LETTER
    if any(word in text for word in _CHOICE_WORDS):
        return CHOOSE
    if any(word in text for word in _REVISE_WORDS):
        return REVISE
    if any(word in text for word in _SHEET_WORDS):
        return SHEET
    if any(word in text for word in _PLAN_WORDS):
        return PLAN
    if any(word in text for word in _VOICE_WORDS):
        return VOICE if len(text) <= _SHORT_FOLLOW_UP else ASK_AND_VOICE
    return ASK


def _latest_turn_id(conn: sqlite3.Connection, viewer: User) -> int | None:
    row = conn.execute(
        "SELECT id FROM advocacy_turns WHERE asker_user_id = ? ORDER BY rowid DESC LIMIT 1", (viewer.id,)
    ).fetchone()
    return row["id"] if row else None


def _latest_plan_id(conn: sqlite3.Connection, viewer: User) -> int | None:
    row = conn.execute(
        f"SELECT id FROM advocacy_plans WHERE asker_user_id = ? AND {made_documents.not_deleted(MadeDocumentKind.PLAN)} ORDER BY rowid DESC LIMIT 1", (viewer.id,)
    ).fetchone()
    return row["id"] if row else None


def _ask(body: advocacy.AskInput, request: Request, viewer: User, conn: sqlite3.Connection) -> dict:
    return advocacy.ask_turn(body, request, viewer, conn)["turn"]


def _ask_and_voice(body: advocacy.AskInput, request: Request, viewer: User, conn: sqlite3.Connection) -> dict:
    """問いにツインの考えで答える。推定の枠は別に作らない。材料がまったくないときは考えを作らない。

    健康や病気の問いでも考えを作る。診断と服薬の指示をしない決まりは、考えを作る側が添える（原則17、advocacy_voice）。
    """
    turn = advocacy.ask_turn(body, request, viewer, conn, with_estimate=False)["turn"]
    brief = advocacy.brief_turn(turn)
    if not advocacy_voice.has_material(conn, viewer):
        return {"kind": ASK, "turn": {**brief, "not_yet": advocacy.NOT_YET, "can_voice": False}}
    voice = advocacy_voice.advocate_on_turn(advocacy_voice.VoiceInput(), request, turn["id"], viewer, conn)["voice"]
    return {"kind": ASK_AND_VOICE, "turn": brief, "voice": voice}


@router.get("/turns")
def list_turns(viewer: User = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """この人の問いと答えを、古い順に返す。ほかの人のやり取りは返さない。

    考えを添えて、集めた言葉の一覧を外して返す。読み直しても一覧が出ないようにするため。
    """
    advocacy._require_supporter(conn, viewer)
    rows = conn.execute(
        "SELECT * FROM advocacy_turns WHERE asker_user_id = ? ORDER BY rowid", (viewer.id,)
    ).fetchall()
    turns = [advocacy._turn_view(conn, row) for row in rows]
    return {
        "items": [
            {**advocacy.brief_turn(turn), "voice": advocacy_voice._latest_voice(conn, turn["id"])}
            for turn in turns
        ]
    }


@router.post("/chat")
def chat(
    body: advocacy.AskInput,
    request: Request,
    viewer: User = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """チャットの1回に答える。呼び先はボタンと同じなので、作ってあるものは AI を呼ばずに返る。"""
    advocacy._require_supporter(conn, viewer)
    text = body.question.strip()
    if not text:
        raise HTTPException(status_code=400, detail="聞きたいことを入れてください。")
    kind = chat_intent(text)
    # ツインの考えは託された人のだれでも使える。ほかのツール（案と紙）を使えない人の言葉は、ふつうの問いとして答える
    tool = _TOOL_OF_KIND.get(kind)
    if tool is not None and tool != professions.VOICE and tool not in professions.tools_of(viewer):
        kind = ASK
    # ふだんの問いにも、ツインの考えで答える。言葉を並べるだけなら検索と同じになるため（原則16）
    if kind == ASK:
        kind = ASK_AND_VOICE
    if kind == VOICE:
        turn_id = _latest_turn_id(conn, viewer)
        if turn_id is None:
            # 続ける問いがないので、言葉そのものを問いにして、考えも添える
            kind = ASK_AND_VOICE
        else:
            voice = advocacy_voice.advocate_on_turn(advocacy_voice.VoiceInput(), request, turn_id, viewer, conn)["voice"]
            return {"kind": VOICE, "voice": voice}
    if kind == ASK_AND_VOICE:
        return _ask_and_voice(body, request, viewer, conn)
    if kind == TELL:
        return {"kind": TELL, "tell": professional_records.tell(conn, request, viewer, text, body.client_key)}
    if kind == LETTER:
        made = advocacy_letter.make_letter(
            advocacy_letter.LetterInput(client_key=body.client_key), request, viewer, conn
        )["letter"]
        return {"kind": LETTER, "letter": made}
    if kind == SHEET:
        again = any(word in text for word in _AGAIN_WORDS)
        sheet = advocacy_sheet.make_sheet(advocacy_sheet.SheetInput(again=again), request, viewer, conn)["sheet"]
        return {"kind": SHEET, "sheet": sheet}
    if kind == CHOOSE:
        plan_id = _latest_plan_id(conn, viewer)
        if plan_id is None:
            raise HTTPException(status_code=400, detail="先に、案をツインに見せてください。")
        version = next(value for word, value in _CHOICE_WORDS.items() if word in text)
        plan = advocacy_plan.choose_version(advocacy_plan.ChoiceInput(version=version), request, plan_id, viewer, conn)["plan"]
        return {"kind": CHOOSE, "plan": plan}
    if kind == REVISE:
        plan_id = _latest_plan_id(conn, viewer)
        if plan_id is None:
            raise HTTPException(status_code=400, detail="先に、案をツインに見せてください。")
        plan = advocacy_plan.revise_plan(advocacy_plan.StepInput(), request, plan_id, viewer, conn)["plan"]
        return {"kind": REVISE, "plan": plan}
    if kind == PLAN:
        if len(text) > advocacy_plan.MAX_PLAN_CHARS:
            raise HTTPException(status_code=400, detail=f"{advocacy_plan.MAX_PLAN_CHARS}字までで入れてください。")
        made = advocacy_plan.make_first_plan(
            advocacy_plan.PlanInput(client_key=body.client_key, text=text), request, viewer, conn
        )["plan"]
        plan = advocacy_plan.object_to_plan(advocacy_plan.StepInput(), request, made["id"], viewer, conn)["plan"]
        return {"kind": PLAN, "plan": plan}
    return {"kind": ASK, "turn": _ask(body, request, viewer, conn)}
