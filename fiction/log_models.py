"""架空の会話の型。

役割：架空の会話1回分の形を決める。台本を流す道具（fiction/run_script.py）と、比べの土台の前の物語の両方が使う。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：8（提出物とデモは架空）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LogTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["ツイン", "誠一さん"]
    text: str


class LogComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["美穂さん", "和子さん", "正雄さん", "節子さん"]
    text: str


class LogConversation(BaseModel):
    """架空の会話1回分。"""

    model_config = ConfigDict(extra="forbid")

    trigger: str
    turns: list[LogTurn]
    confirmation: Literal["合っています", "直した"]
    letter: str | None = None
    comments: list[LogComment] = []
