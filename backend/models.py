from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

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


# --- Edge (arbitrary typed connection between two cards, same or different board) ---

class Edge(BaseModel):
    id: str = Field(default_factory=_uid)
    board_id: str = ""
    from_card_id: str
    to_card_id: str
    type: str = "relates_to"  # arbitrary — "blocked_by", "blocks", "duplicates", etc.
    label: str = ""
    created_at: datetime = Field(default_factory=_now)


# --- Session history entry ---

class CardNote(BaseModel):
    """A work note or a question left on a card. A question with no answer is
    open, and open questions are what the UI surfaces for attention."""
    id: str = Field(default_factory=_uid)
    kind: Literal["note", "question"] = "note"
    text: str
    session_id: str = ""
    created_at: datetime = Field(default_factory=_now)
    answer: str = ""
    answered_by: str = ""
    answered_at: datetime | None = None


class FieldChange(BaseModel):
    """One field's before/after for a card edit. Long values are truncated by
    the writer so a card's history cannot outgrow the card itself."""
    field: str
    before: Any = None
    after: Any = None


class SessionEntry(BaseModel):
    session_id: str
    system: str = ""
    timestamp: datetime = Field(default_factory=_now)
    action: str = ""
    outcome: Literal["success", "failed", "partial"] | None = None
    changes: list[FieldChange] = Field(default_factory=list)


# --- Card ---

class Card(BaseModel):
    id: str = Field(default_factory=_uid)
    board_id: str = ""
    external_id: str = ""
    title: str
    body: str = ""
    column_id: str
    parent_id: str | None = None  # null = top-level card
    position: int = 0
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_history: list[SessionEntry] = Field(default_factory=list)
    notes: list[CardNote] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Board ---

class Board(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    description: str = ""
    remote_url: str = ""  # normalized git remote URL identifying the project this board tracks
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
    remote_url: str | None = None


class EnsureProjectBoard(BaseModel):
    remote_url: str
    name: str | None = None  # defaults to a name derived from the remote URL
    description: str = ""
    columns: list[str] | None = None  # defaults to Open Items/In Progress/In Testing/Done


class CreateColumn(BaseModel):
    name: str
    position: int | None = None  # append to end if None


class UpdateColumn(BaseModel):
    name: str | None = None
    position: int | None = None


class CreateCard(BaseModel):
    external_id: str = ""
    title: str
    body: str = ""
    column_id: str
    parent_id: str | None = None  # null = top-level card
    position: int | None = None  # append to end of column if None
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateCard(BaseModel):
    external_id: str | None = None
    title: str | None = None
    body: str | None = None
    column_id: str | None = None
    parent_id: str | None = None
    position: int | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    labels: list[str] | None = None
    metadata: dict[str, Any] | None = None


class MoveCard(BaseModel):
    column_id: str
    position: int | None = None  # append to end if None


class AddSession(BaseModel):
    session_id: str
    system: str = ""
    action: str = ""
    outcome: Literal["success", "failed", "partial"] | None = None


class AddNote(BaseModel):
    kind: Literal["note", "question"] = "note"
    text: str


class AnswerNote(BaseModel):
    answer: str
    answered_by: str = ""


class CreateEdge(BaseModel):
    from_card_id: str
    to_card_id: str
    type: str = "relates_to"
    label: str = ""
