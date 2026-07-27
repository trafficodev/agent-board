from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, computed_field


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
    remote_aliases: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Event log ---

class Event(BaseModel):
    """One durable, append-only fact about a board.

    For card mutations this log -- not the card -- is the authoritative
    history: the card carries a capped, truncated projection of it, while the
    journal keeps every version in full so a card can be reconstructed as of
    any point in time and survives the card's own deletion.
    """
    id: str = Field(default_factory=_uid)
    timestamp: datetime = Field(default_factory=_now)
    type: str
    detail: str = ""
    # Structured attribution. Queryable fields rather than a display string,
    # so "what did session X do" is answerable without parsing prose.
    board_id: str = ""
    card_id: str = ""
    session_id: str = ""
    system: str = ""
    action: str = ""
    # Whole tracked-field state of the card right after this mutation.
    snapshot: dict[str, Any] | None = None
    # Journals written before attribution was structured stored a bare actor
    # string. Reading it back keeps those events attributed; nothing writes it.
    legacy_actor: str = Field(default="", validation_alias="actor", exclude=True)

    @computed_field
    @property
    def actor(self) -> str:
        """Display name for whoever caused this. Derived, never stored, so it
        cannot drift from the identity fields it summarizes."""
        return self.system or self.session_id or self.legacy_actor


class CardVersion(BaseModel):
    """One point in a card's life, rebuilt from the journal."""
    version: int
    event_id: str
    timestamp: datetime
    action: str
    session_id: str = ""
    system: str = ""
    changes: list[FieldChange] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)


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


class LinkProjectRemote(BaseModel):
    remote_url: str


class ConsolidateProjectBoard(BaseModel):
    source_board_id: str
    target_board_id: str


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


class RevertCard(BaseModel):
    version: int  # as reported by the card's history, 1-based


# --- Bulk card operations ---
#
# One ordered batch, applied in a single board transaction, so an agent can
# rewrite a whole slice of a board without paying a round-trip per card. A
# create names itself with `ref`; any later op targets it as "@<ref>", which
# is what lets a parent and its sub-tasks be built in one call.

MAX_BULK_OPERATIONS = 500


class BulkCreate(BaseModel):
    op: Literal["create"]
    ref: str = ""  # names this new card for later ops in the same batch
    external_id: str = ""
    title: str
    body: str = ""
    column_id: str
    parent_id: str | None = None
    position: int | None = None
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BulkUpdate(BaseModel):
    op: Literal["update"]
    card_id: str
    external_id: str | None = None
    title: str | None = None
    body: str | None = None
    column_id: str | None = None
    parent_id: str | None = None
    position: int | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    labels: list[str] | None = None
    metadata: dict[str, Any] | None = None


class BulkMove(BaseModel):
    op: Literal["move"]
    card_id: str
    column_id: str
    position: int | None = None


class BulkDelete(BaseModel):
    op: Literal["delete"]
    card_id: str


class BulkNote(BaseModel):
    op: Literal["note"]
    card_id: str
    text: str
    kind: Literal["note", "question"] = "note"


BulkOperation = Annotated[
    BulkCreate | BulkUpdate | BulkMove | BulkDelete | BulkNote,
    Field(discriminator="op"),
]


class BulkCards(BaseModel):
    operations: list[BulkOperation] = Field(min_length=1, max_length=MAX_BULK_OPERATIONS)


class BulkOperationResult(BaseModel):
    index: int
    op: str
    card_id: str = ""
    ref: str = ""


class BulkCardsResult(BaseModel):
    """Nothing is applied unless every operation succeeds, so ``applied``
    describes the whole batch rather than any single operation."""
    applied: bool
    results: list[BulkOperationResult] = Field(default_factory=list)
    failed_index: int | None = None
    error: str = ""


class CreateEdge(BaseModel):
    from_card_id: str
    to_card_id: str
    type: str = "relates_to"
    label: str = ""
