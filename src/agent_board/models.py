from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, Field, computed_field, model_validator

from .card_semantics import CardSemantics, RelationshipType


CHANGE_PROVIDER_HEADER = "X-Agent-Board-Provider"
CHANGE_NATIVE_SESSION_HEADER = "X-Agent-Board-Session"
CHANGE_REVIEWED_COMMIT_HEADER = "X-Agent-Board-Commit"
CHANGE_PROVIDER_ENV = "AGENT_BOARD_PROVIDER"
CHANGE_NATIVE_SESSION_ENV = "AGENT_BOARD_NATIVE_SESSION_ID"
CHANGE_REVIEWED_COMMIT_ENV = "AGENT_BOARD_REVIEWED_COMMIT_SHA"
_PROVIDER_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_COMMIT_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_MAX_NATIVE_SESSION_ID = 512


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- Column ---

class Column(BaseModel):
    id: str = Field(default_factory=new_id)
    board_id: str = ""
    name: str
    position: int = 0


# --- Edge (arbitrary typed connection between two cards, same or different board) ---

class Edge(BaseModel):
    id: str = Field(default_factory=new_id)
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
    id: str = Field(default_factory=new_id)
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
    claim_id: str = ""
    session_id: str
    system: str = ""
    timestamp: datetime = Field(default_factory=_now)
    action: str = ""
    outcome: Literal["success", "failed", "partial"] | None = None
    changes: list[FieldChange] = Field(default_factory=list)


# --- Card ---

class Card(BaseModel):
    id: str = Field(default_factory=new_id)
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
    semantics: CardSemantics = Field(default_factory=CardSemantics)
    session_history: list[SessionEntry] = Field(default_factory=list)
    notes: list[CardNote] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Board ---

class Board(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    remote_url: str = ""  # normalized git remote URL identifying the project this board tracks
    remote_aliases: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Event log ---

class ChangeContext(BaseModel):
    """Caller-reviewed revision and native identity for one authored change."""
    provider: str
    native_session_id: str
    reviewed_commit_sha: str = ""
    voteable: bool = True

    @model_validator(mode="after")
    def _complete_voteable_identity(self):
        self.provider = self.provider.strip()
        self.native_session_id = self.native_session_id.strip()
        self.reviewed_commit_sha = self.reviewed_commit_sha.strip()
        if not _PROVIDER_PATTERN.fullmatch(self.provider):
            raise ValueError("provider must be a lowercase provider namespace")
        if len(self.native_session_id) > _MAX_NATIVE_SESSION_ID:
            raise ValueError("native_session_id is too long")
        if self.native_session_id and not self.native_session_id.isprintable():
            raise ValueError("native_session_id must be printable")
        if self.voteable and not self.native_session_id:
            raise ValueError("native_session_id is required for authored changes")
        if self.voteable and not _COMMIT_SHA_PATTERN.fullmatch(self.reviewed_commit_sha):
            raise ValueError("reviewed_commit_sha must be a full Git commit SHA")
        if self.reviewed_commit_sha and not _COMMIT_SHA_PATTERN.fullmatch(
            self.reviewed_commit_sha
        ):
            raise ValueError("reviewed_commit_sha must be a full Git commit SHA")
        return self

    @classmethod
    def authored(
        cls,
        provider: str,
        native_session_id: str,
        reviewed_commit_sha: str,
    ) -> ChangeContext:
        return cls(
            provider=provider,
            native_session_id=native_session_id,
            reviewed_commit_sha=reviewed_commit_sha,
        )

    @classmethod
    def system(cls, _purpose: str) -> ChangeContext:
        return cls(provider="system", native_session_id="", voteable=False)

    @classmethod
    def legacy_session(cls, provider: str, native_session_id: str) -> ChangeContext:
        return cls(
            provider=provider or "legacy",
            native_session_id=native_session_id,
            voteable=False,
        )

    @classmethod
    def reader(cls, provider: str, native_session_id: str) -> ChangeContext:
        return cls(
            provider=provider,
            native_session_id=native_session_id,
            voteable=False,
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> ChangeContext:
        normalized = {key.lower(): value for key, value in headers.items()}
        return cls.authored(
            normalized.get(CHANGE_PROVIDER_HEADER.lower(), ""),
            normalized.get(CHANGE_NATIVE_SESSION_HEADER.lower(), ""),
            normalized.get(CHANGE_REVIEWED_COMMIT_HEADER.lower(), ""),
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> ChangeContext:
        provider = environ.get(CHANGE_PROVIDER_ENV, "").strip()
        native_session_id = environ.get(CHANGE_NATIVE_SESSION_ENV, "").strip()
        if not native_session_id:
            native_session_id = (
                environ.get("BETTER_AGENT_APP_SESSION_ID", "")
                or environ.get("BETTER_CLAUDE_APP_SESSION_ID", "")
            ).strip()
        if not provider:
            provider = environ.get("BETTER_AGENT_PROVIDER_KIND", "").strip()
        return cls.authored(
            provider,
            native_session_id,
            environ.get(CHANGE_REVIEWED_COMMIT_ENV, ""),
        )

    def headers(self) -> dict[str, str]:
        return {
            CHANGE_PROVIDER_HEADER: self.provider,
            CHANGE_NATIVE_SESSION_HEADER: self.native_session_id,
            CHANGE_REVIEWED_COMMIT_HEADER: self.reviewed_commit_sha,
        }

class Event(BaseModel):
    """One durable, append-only fact about a board.

    For card mutations this log -- not the card -- is the authoritative
    history: the card carries a capped, truncated projection of it, while the
    journal keeps every version in full so a card can be reconstructed as of
    any point in time and survives the card's own deletion.
    """
    id: str = Field(default_factory=new_id)
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
    projection_version: int = 0
    document: dict[str, Any] | None = None
    provider: str = ""
    native_session_id: str = ""
    reviewed_commit_sha: str = ""
    voteable: bool = False
    cutover_state: Literal["legacy", "baseline", "authored"] = "legacy"
    # Journals written before attribution was structured stored a bare actor
    # string. Reading it back keeps those events attributed; nothing writes it.
    legacy_actor: str = Field(default="", validation_alias="actor", exclude=True)

    @computed_field
    @property
    def actor(self) -> str:
        """Display name for whoever caused this. Derived, never stored, so it
        cannot drift from the identity fields it summarizes."""
        return (
            self.provider
            or self.system
            or self.native_session_id
            or self.session_id
            or self.legacy_actor
        )


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


class ChangeVoteSummary(BaseModel):
    up: int = 0
    down: int = 0
    current: Literal[-1, 1] | None = None


class ChangeVoteEvent(BaseModel):
    """One immutable vote decision; effective totals use each voter's latest."""
    id: str = Field(default_factory=new_id)
    target_id: str
    event_id: str
    provider: str
    native_session_id: str
    reviewed_commit_sha: str
    direction: Literal[-1, 1]
    timestamp: datetime = Field(default_factory=_now)


class CardChangeItem(BaseModel):
    """One immutable, voteable operation projected from a card event."""
    id: str
    event_id: str
    projection_version: int
    ordinal: int
    path: str
    operation: Literal["add", "remove", "replace"]
    diff: str
    commit_sha: str = ""
    timestamp: datetime | None = None
    votes: ChangeVoteSummary = Field(default_factory=ChangeVoteSummary)


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
    semantics: CardSemantics = Field(default_factory=CardSemantics)


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
    semantics: CardSemantics | None = None


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
    semantics: CardSemantics = Field(default_factory=CardSemantics)


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
    semantics: CardSemantics | None = None


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
    type: RelationshipType
    label: str = ""
