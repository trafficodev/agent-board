from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# --- Column ---

class Column(BaseModel):
    id: str = Field(default_factory=_uid)
    board_id: str = ""
    name: str
    position: int = 0


# --- Session history entry ---

class SessionEntry(BaseModel):
    session_id: str
    system: str = ""
    timestamp: datetime = Field(default_factory=_now)
    action: str = ""
    outcome: Literal["success", "failed", "partial"] | None = None


# --- Card ---

class Card(BaseModel):
    id: str = Field(default_factory=_uid)
    board_id: str = ""
    title: str
    body: str = ""
    column_id: str
    parent_id: str | None = None  # null = top-level card
    position: int = 0
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    labels: list[str] = Field(default_factory=list)
    session_history: list[SessionEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Board ---

class Board(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    description: str = ""
    columns: list[Column] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Event log ---

class Event(BaseModel):
    timestamp: datetime = Field(default_factory=_now)
    type: str
    actor: str = ""
    detail: str = ""


# --- Request models ---

class CreateBoard(BaseModel):
    name: str
    description: str = ""
    columns: list[str] | None = None  # column names; defaults to Backlog/In Progress/Review/Done


class UpdateBoard(BaseModel):
    name: str | None = None
    description: str | None = None


class CreateColumn(BaseModel):
    name: str
    position: int | None = None  # append to end if None


class UpdateColumn(BaseModel):
    name: str | None = None
    position: int | None = None


class CreateCard(BaseModel):
    title: str
    body: str = ""
    column_id: str
    parent_id: str | None = None  # null = top-level card
    position: int | None = None  # append to end of column if None
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    labels: list[str] = Field(default_factory=list)


class UpdateCard(BaseModel):
    title: str | None = None
    body: str | None = None
    column_id: str | None = None
    parent_id: str | None = None
    position: int | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    labels: list[str] | None = None


class MoveCard(BaseModel):
    column_id: str
    position: int | None = None  # append to end if None


class AddSession(BaseModel):
    session_id: str
    system: str = ""
    action: str = ""
    outcome: Literal["success", "failed", "partial"] | None = None
