"""Card persistence. Cards are stored in per-board JSON files."""

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path

from models import (
    AddNote,
    AddSession,
    AnswerNote,
    Card,
    CardNote,
    CreateCard,
    Event,
    MoveCard,
    RevertCard,
    SessionEntry,
    UpdateCard,
    _now,
)
from paths import board_path, events_path

import card_history
from board_store import get_board
from card_diff import diff_snapshots, snapshot_of
from dag_order import dag_sort
from edge_store import read_edges, write_edges

CLOSED_CARD_TTL = timedelta(days=3)
CLOSED_AT_METADATA_KEY = "closed_at"
DAG_ORDERED_COLUMN = "open items"

# How much authorship history the card document itself carries. This is a
# convenience projection for anyone holding a card; the journal behind
# card_history keeps every version in full, so trimming here loses nothing.
_HISTORY_LIMIT = 50

_open_question_cache_lock = threading.Lock()
_open_question_cache: dict[
    Path,
    tuple[tuple[int, int, int, int, int], tuple[dict, ...]],
] = {}
_open_question_cache_home: Path | None = None
_OPEN_QUESTION_CACHE_RETRIES = 3


# Set for the duration of one API request from the caller's session header.
request_session: ContextVar[str] = ContextVar("agent_board_request_session", default="")


def acting_session() -> tuple[str, str]:
    """The Better Agent session driving this call, as ``(session_id, system)``.

    Two ways in. In-process callers (an MCP server importing the store, a test)
    are identified by the session id Better Agent puts in their environment.
    Callers that reach the shared API server over HTTP carry it in a request
    header instead, because that server process belongs to no session — it is
    set per request into ``request_session``.

    Anything with neither is recorded with an empty session_id: unknown
    authorship, never guessed authorship."""
    session_id = (
        request_session.get()
        or os.environ.get("BETTER_AGENT_APP_SESSION_ID")
        or os.environ.get("BETTER_CLAUDE_APP_SESSION_ID")
        or ""
    ).strip()
    system = (os.environ.get("BETTER_AGENT_PROVIDER_KIND") or "").strip()
    return session_id, system


def _snapshot(card: Card) -> dict:
    # closed_at is stamped by the store itself, not by the caller, so it would
    # otherwise report as a user edit on every close.
    return snapshot_of(card, exclude_metadata_keys=(CLOSED_AT_METADATA_KEY,))


def _record(
    card: Card,
    action: str,
    before: dict,
    event_type: str,
    detail: str = "",
    force: bool = False,
) -> Event:
    """Record one card mutation in both places it belongs.

    The card gets a truncated, capped entry for whoever is holding it; the
    returned journal event carries the whole post-mutation state and is what
    makes the card's history reconstructable. A mutation that changed nothing
    still shows up in the activity feed but does not mint a version, so
    no-op writes cannot pad a card's history.
    """
    after = _snapshot(card)
    is_version = force or action == "created" or bool(diff_snapshots(before, after))
    session_id, system = acting_session()
    if is_version:
        card.session_history.append(SessionEntry(
            session_id=session_id,
            system=system,
            action=action,
            changes=diff_snapshots(before, after, truncate=True),
        ))
        if len(card.session_history) > _HISTORY_LIMIT:
            del card.session_history[:-_HISTORY_LIMIT]
    return Event(
        type=event_type,
        detail=detail or card.title,
        board_id=card.board_id,
        card_id=card.id,
        session_id=session_id,
        system=system,
        action=action,
        snapshot=after if is_version else None,
    )


def _cards_path(board_id: str) -> Path:
    return board_path(board_id).parent / f"{board_id}_cards.json"


def _dag_ordered_column_ids(board_id: str) -> set[str]:
    """The columns whose order is owned by the dependency graph. The backlog is
    the one place order answers "what can I pick up next", so it is the one
    place blocking edges outrank the order cards were put in."""
    board = get_board(board_id)
    if not board:
        return set()
    return {c.id for c in board.columns if c.name.strip().lower() == DAG_ORDERED_COLUMN}


def _place_at(cards: list[Card], card: Card, position: int) -> None:
    """Put a card at an explicit slot in its column. Everything from that slot
    down shifts, because a bare assignment only ties the card with whoever
    already holds the slot and the stable reindex then leaves it where it was."""
    for other in cards:
        if other is not card and other.column_id == card.column_id and other.position >= position:
            other.position += 1
    card.position = position


def _reindex_column(board_id: str, cards: list[Card], column_id: str, edges: list) -> None:
    """Sort cards within a column by position, then reassign sequential
    positions. A dependency-ordered column is additionally run through the
    blocking graph, so a blocker always sits above what waits on it.

    Edges come from the caller's transaction rather than off disk, because a
    transaction that just removed some would otherwise order the column by a
    graph that no longer exists.
    """
    col_cards = sorted(
        [c for c in cards if c.column_id == column_id],
        key=lambda c: c.position,
    )
    if column_id in _dag_ordered_column_ids(board_id):
        col_cards = dag_sort(col_cards, edges)
    for i, c in enumerate(col_cards):
        c.position = i


def _read_cards(board_id: str) -> list[Card]:
    p = _cards_path(board_id)
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return [Card.model_validate(c) for c in data]


def _write_cards(board_id: str, cards: list[Card]) -> None:
    """Replace the cards file atomically, so a concurrent reader sees either
    the whole previous file or the whole new one, never a half-written one."""
    p = _cards_path(board_id)
    payload = json.dumps([c.model_dump(mode="json") for c in cards], indent=2, default=str)
    # Unique per call, not per process: two writers sharing a temp name would
    # have one rename away the file the other is still writing.
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(payload)
    os.replace(tmp, p)


def _cards_fingerprint(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _with_lock(board_id: str) -> Path:
    """Return a lock file path for a board. Shared with edge_store and
    board_store so every write to a board's files serializes on one lock."""
    d = board_path(board_id).parent
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{board_id}.lock"


class _Transaction:
    """The cards and edges a mutator is working on, plus the journal records it
    produced.

    Nothing is written until the mutator sets ``commit``, so an early return or
    a raised exception leaves the board exactly as it was. Edges live here too
    because they share the board's lock: cleaning them up after the commit
    would leave a window where a card is gone but the edges pointing at it are
    not, and would let a cleanup failure report a batch as failed after it had
    already landed.
    """

    def __init__(self, cards: list[Card], edges: list):
        self.cards = cards
        self.edges = edges
        self.events: list[Event] = []
        self.edges_dirty = False
        self.commit = False

    def record(self, *events: Event) -> None:
        self.events.extend(events)
        self.commit = True

    def drop_edges_for(self, card_id: str) -> None:
        """A deleted card must not leave edges pointing at it."""
        remaining = [
            e for e in self.edges
            if e.from_card_id != card_id and e.to_card_id != card_id
        ]
        if len(remaining) != len(self.edges):
            self.edges = remaining
            self.edges_dirty = True


@contextmanager
def board_transaction(board_id: str):
    """Read, mutate and write a board's cards under a single lock.

    The read has to happen inside the lock: a mutator rewrites the whole cards
    list, so reading it before acquiring the lock lets two concurrent writers
    each save a list that is missing the other's card. That would also strand
    the journal, which would still hold a `created` record for a card no longer
    on the board.
    """
    lock_path = _with_lock(board_id)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            tx = _Transaction(_read_cards(board_id), read_edges(board_id))
            yield tx
            if tx.commit:
                _write_cards(board_id, tx.cards)
                if tx.edges_dirty:
                    write_edges(board_id, tx.edges)
                if tx.events:
                    with open(events_path(board_id), "a") as f:
                        f.writelines(event.model_dump_json() + "\n" for event in tx.events)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def reindex_dag_columns(board_id: str) -> None:
    """Re-apply dependency ordering after the graph itself changed. Edge writes
    come through here because a new or removed blocker reorders the backlog
    without touching any card."""
    column_ids = _dag_ordered_column_ids(board_id)
    if not column_ids:
        return
    with board_transaction(board_id) as tx:
        before = {c.id: c.position for c in tx.cards}
        for column_id in column_ids:
            _reindex_column(board_id, tx.cards, column_id, tx.edges)
        if any(before[c.id] != c.position for c in tx.cards):
            tx.record(Event(type="cards_reordered", board_id=board_id, detail="dependency order"))


def _closed_column_id(board_id: str) -> str | None:
    board = get_board(board_id)
    if not board:
        return None
    return next((column.id for column in board.columns if column.name.lower() == "closed"), None)


def _stamp_closed_state(card: Card, closed_column_id: str | None, now: datetime) -> None:
    if not closed_column_id:
        return
    if card.column_id == closed_column_id:
        card.metadata.setdefault(CLOSED_AT_METADATA_KEY, now.isoformat())
        return
    card.metadata.pop(CLOSED_AT_METADATA_KEY, None)


def _closed_at(card: Card) -> datetime:
    value = card.metadata.get(CLOSED_AT_METADATA_KEY)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return card.updated_at if card.updated_at.tzinfo else card.updated_at.replace(tzinfo=timezone.utc)


def _prune_expired_closed_cards(board_id: str) -> None:
    closed_column_id = _closed_column_id(board_id)
    if not closed_column_id:
        return

    with board_transaction(board_id) as tx:
        cutoff = _now() - CLOSED_CARD_TTL
        retained = [
            card for card in tx.cards
            if card.column_id != closed_column_id or _closed_at(card) > cutoff
        ]
        expired = len(tx.cards) - len(retained)
        if not expired:
            return

        tx.cards = retained
        _reindex_column(board_id, tx.cards, closed_column_id, tx.edges)
        tx.record(Event(
            type="closed_cards_pruned",
            board_id=board_id,
            detail=f"{expired} closed cards expired",
        ))


# --- Cards ---

def list_cards(board_id: str, priority: str | None = None, label: str | None = None, column_id: str | None = None, parent_id: str | None = None) -> list[Card]:
    _prune_expired_closed_cards(board_id)
    cards = _read_cards(board_id)
    if priority:
        cards = [c for c in cards if c.priority == priority]
    if label:
        cards = [c for c in cards if label.lower() in [l.lower() for l in c.labels]]
    if column_id:
        cards = [c for c in cards if c.column_id == column_id]
    if parent_id is not None:
        cards = [c for c in cards if c.parent_id == parent_id]
    return sorted(cards, key=lambda c: c.position)


def get_card(board_id: str, card_id: str) -> Card | None:
    _prune_expired_closed_cards(board_id)
    cards = _read_cards(board_id)
    return next((c for c in cards if c.id == card_id), None)


def apply_create(tx: _Transaction, board_id: str, data: CreateCard) -> Card | None:
    """Create one card inside an already-open transaction.

    The public single-card entry points and the bulk executor both route
    through the apply_* helpers, so batching a change can never drift from
    doing it one at a time.
    """
    board = get_board(board_id)
    if not board or data.column_id not in [c.id for c in board.columns]:
        return None

    now = _now()
    col_cards = [c for c in tx.cards if c.column_id == data.column_id]
    pos = data.position if data.position is not None else len(col_cards)
    pos = min(pos, len(col_cards))

    card = Card(
        board_id=board_id,
        external_id=data.external_id,
        title=data.title,
        body=data.body,
        column_id=data.column_id,
        parent_id=data.parent_id,
        position=pos,
        priority=data.priority,
        labels=[l.lower() for l in data.labels],
        metadata=data.metadata,
    )
    _stamp_closed_state(card, _closed_column_id(board_id), now)
    tx.record(_record(card, "created", {}, "card_created"))
    tx.cards.append(card)
    _place_at(tx.cards, card, pos)
    _reindex_column(board_id, tx.cards, data.column_id, tx.edges)
    return card


def create_card(board_id: str, data: CreateCard) -> Card | None:
    with board_transaction(board_id) as tx:
        return apply_create(tx, board_id, data)


def _matches_upsert(card: Card, data: CreateCard) -> bool:
    metadata = {
        key: value
        for key, value in card.metadata.items()
        if key != CLOSED_AT_METADATA_KEY
    }
    return (
        card.external_id == data.external_id
        and card.title == data.title
        and card.body == data.body
        and card.column_id == data.column_id
        and card.parent_id == data.parent_id
        and card.priority == data.priority
        and card.labels == [label.lower() for label in data.labels]
        and metadata == data.metadata
    )


def upsert_card(board_id: str, data: CreateCard) -> Card | None:
    if not data.external_id:
        raise ValueError("external_id is required")
    with board_transaction(board_id) as tx:
        existing = next(
            (card for card in tx.cards if card.external_id == data.external_id),
            None,
        )
        if existing is None:
            return apply_create(tx, board_id, data)
        if _matches_upsert(existing, data):
            return existing
        return apply_update(
            tx,
            board_id,
            existing.id,
            UpdateCard(**data.model_dump(exclude={"position"})),
        )


def apply_update(tx: _Transaction, board_id: str, card_id: str, data: UpdateCard) -> Card | None:
    """Update one card inside an already-open transaction."""
    card = next((c for c in tx.cards if c.id == card_id), None)
    if not card:
        return None

    # A column that does not exist is refused rather than quietly ignored:
    # silently keeping the old lane would report a move that never happened.
    if data.column_id is not None:
        board = get_board(board_id)
        if not board or data.column_id not in [c.id for c in board.columns]:
            return None
    if "parent_id" in data.model_fields_set and data.parent_id:
        # Re-parenting a card under its own descendant makes a cycle that
        # wedges every later walk of the tree, so it is refused outright.
        if data.parent_id == card_id:
            return None
        if not any(c.id == data.parent_id for c in tx.cards):
            return None
        if any(c.id == data.parent_id for c in _collect_descendants(tx.cards, card_id)):
            return None

    closed_column_id = _closed_column_id(board_id)
    now = _now()
    before = _snapshot(card)
    existing_closed_at = card.metadata.get(CLOSED_AT_METADATA_KEY)
    if data.external_id is not None:
        card.external_id = data.external_id
    if data.title is not None:
        card.title = data.title
    if data.body is not None:
        card.body = data.body
    if "parent_id" in data.model_fields_set:
        card.parent_id = data.parent_id
    old_col = card.column_id
    col_changed = False
    if data.column_id is not None:
        card.column_id = data.column_id
        col_changed = True
    if data.position is not None:
        _place_at(tx.cards, card, data.position)
    if data.priority is not None:
        card.priority = data.priority
    if data.labels is not None:
        card.labels = [l.lower() for l in data.labels]
    if data.metadata is not None:
        card.metadata = dict(data.metadata)
        if existing_closed_at is not None and card.column_id == closed_column_id:
            card.metadata[CLOSED_AT_METADATA_KEY] = existing_closed_at

    if col_changed or data.position is not None:
        _reindex_column(board_id, tx.cards, card.column_id, tx.edges)
        if col_changed and old_col != card.column_id:
            _reindex_column(board_id, tx.cards, old_col, tx.edges)

    _stamp_closed_state(card, closed_column_id, now)
    tx.record(_record(card, "updated", before, "card_updated"))
    card.updated_at = now
    return card


def update_card(board_id: str, card_id: str, data: UpdateCard) -> Card | None:
    with board_transaction(board_id) as tx:
        return apply_update(tx, board_id, card_id, data)


def _collect_descendants(cards: list[Card], parent_id: str) -> list[Card]:
    """Recursively collect all descendants of a card."""
    result: list[Card] = []
    for c in cards:
        if c.parent_id == parent_id:
            result.append(c)
            result.extend(_collect_descendants(cards, c.id))
    return result


def apply_move(tx: _Transaction, board_id: str, card_id: str, data: MoveCard) -> Card | None:
    """Move one card, and its sub-tree, inside an already-open transaction."""
    board = get_board(board_id)
    if not board or data.column_id not in [c.id for c in board.columns]:
        return None
    card = next((c for c in tx.cards if c.id == card_id), None)
    if not card:
        return None

    closed_column_id = _closed_column_id(board_id)
    now = _now()
    before = _snapshot(card)
    old_col = card.column_id
    card.column_id = data.column_id

    col_cards = [c for c in tx.cards if c.column_id == data.column_id and c.id != card_id]
    pos = data.position if data.position is not None else len(col_cards)
    _place_at(tx.cards, card, min(pos, len(col_cards)))

    # Cascade column move to all descendants
    events: list[Event] = []
    if old_col != data.column_id:
        for desc in _collect_descendants(tx.cards, card_id):
            desc_before = _snapshot(desc)
            desc.column_id = data.column_id
            _stamp_closed_state(desc, closed_column_id, now)
            # A sub-task dragged along by its parent still changed lane, so
            # it records the move under its own history.
            events.append(_record(desc, "moved", desc_before, "card_moved"))
            desc.updated_at = now

    _stamp_closed_state(card, closed_column_id, now)
    events.append(_record(
        card, "moved", before, "card_moved",
        detail=f"{card.title}: {old_col[:8]}… → {data.column_id[:8]}…",
    ))
    card.updated_at = now
    _reindex_column(board_id, tx.cards, data.column_id, tx.edges)
    if old_col != data.column_id:
        _reindex_column(board_id, tx.cards, old_col, tx.edges)
    tx.record(*events)
    return card


def move_card(board_id: str, card_id: str, data: MoveCard) -> Card | None:
    with board_transaction(board_id) as tx:
        return apply_move(tx, board_id, card_id, data)


def apply_delete(tx: _Transaction, board_id: str, card_id: str) -> bool:
    """Remove one card inside an already-open transaction.

    Everything that pointed at the card goes with it: its edges are dropped and
    its direct children are promoted to top level, so a delete never leaves a
    reference to a card that is no longer there.
    """
    card = next((c for c in tx.cards if c.id == card_id), None)
    if not card:
        return False
    col_id = card.column_id
    tx.cards = [c for c in tx.cards if c.id != card_id]
    tx.drop_edges_for(card_id)

    now = _now()
    for child in [c for c in tx.cards if c.parent_id == card_id]:
        child_before = _snapshot(child)
        child.parent_id = None
        # Losing a parent is a change to the child, so it says so in its own
        # history rather than silently acquiring a dangling reference.
        tx.record(_record(child, "orphaned", child_before, "card_updated"))
        child.updated_at = now

    _reindex_column(board_id, tx.cards, col_id, tx.edges)
    session_id, system = acting_session()
    # The card is going away but its history is not: the journal keeps its
    # final state, so a deleted card can still be inspected afterwards.
    tx.record(Event(
        type="card_deleted",
        detail=card.title,
        board_id=board_id,
        card_id=card_id,
        session_id=session_id,
        system=system,
        action="deleted",
        snapshot=_snapshot(card),
    ))
    return True


def delete_card(board_id: str, card_id: str) -> bool:
    with board_transaction(board_id) as tx:
        return apply_delete(tx, board_id, card_id)


def add_session(board_id: str, card_id: str, data: AddSession) -> Card | None:
    with board_transaction(board_id) as tx:
        card = next((c for c in tx.cards if c.id == card_id), None)
        if not card:
            return None

        # The reported session is the subject here, not the caller, so this is
        # the one path attributed from the payload rather than acting_session.
        card.session_history.append(SessionEntry(
            session_id=data.session_id,
            system=data.system,
            action=data.action,
            outcome=data.outcome,
        ))
        if len(card.session_history) > _HISTORY_LIMIT:
            del card.session_history[:-_HISTORY_LIMIT]
        card.updated_at = _now()
        tx.record(Event(
            type="session_added",
            detail=f"session {data.session_id[:8]}… → {card.title}",
            board_id=board_id,
            card_id=card.id,
            session_id=data.session_id,
            system=data.system,
            action=data.action or "worked",
            snapshot=_snapshot(card),
        ))
        return card


_MAX_NOTES = 200
_MAX_NOTE_CHARS = 8000


def is_open_question(note: CardNote) -> bool:
    """A question nobody has answered yet. This is the single definition of
    'needs attention' — the badge, the card marker and the panel all use it."""
    return note.kind == "question" and not (note.answer or "").strip()


def apply_note(tx: _Transaction, board_id: str, card_id: str, data: AddNote) -> Card | None:
    """Leave one note or question inside an already-open transaction."""
    text = (data.text or "").strip()
    if not text:
        return None
    card = next((c for c in tx.cards if c.id == card_id), None)
    if not card:
        return None

    session_id, _ = acting_session()
    note = CardNote(kind=data.kind, text=text[:_MAX_NOTE_CHARS], session_id=session_id)
    card.notes.append(note)
    if len(card.notes) > _MAX_NOTES:
        del card.notes[:-_MAX_NOTES]
    card.updated_at = _now()
    tx.record(_record(
        card,
        "asked" if data.kind == "question" else "noted",
        _snapshot(card),
        "card_note_added",
        detail=f"{data.kind} on {card.title}",
        force=True,
    ))
    return card


def add_note(board_id: str, card_id: str, data: AddNote) -> Card | None:
    with board_transaction(board_id) as tx:
        return apply_note(tx, board_id, card_id, data)


def answer_note(board_id: str, card_id: str, note_id: str, data: AnswerNote) -> Card | None:
    answer = (data.answer or "").strip()
    if not answer:
        return None
    with board_transaction(board_id) as tx:
        card = next((c for c in tx.cards if c.id == card_id), None)
        if not card:
            return None
        note = next((n for n in card.notes if n.id == note_id), None)
        if note is None or note.kind != "question":
            return None

        session_id, _ = acting_session()
        note.answer = answer[:_MAX_NOTE_CHARS]
        # An answer from the UI has no session; the caller says who replied.
        note.answered_by = (data.answered_by or session_id or "user").strip()
        note.answered_at = _now()
        card.updated_at = note.answered_at
        tx.record(_record(
            card,
            "answered",
            _snapshot(card),
            "card_question_answered",
            detail=f"{card.title} — answered by {note.answered_by}",
            force=True,
        ))
        return card


def _question_items(board_id: str, cards: list[Card]) -> tuple[dict, ...]:
    return tuple({
        "board_id": board_id,
        "card_id": card.id,
        "card_title": card.title,
        "note_id": note.id,
        "text": note.text,
        "session_id": note.session_id,
        "created_at": note.created_at.isoformat(),
    } for card in cards for note in card.notes if is_open_question(note))


def _open_questions_for_board(board_id: str) -> list[dict]:
    path = _cards_path(board_id)
    for _ in range(_OPEN_QUESTION_CACHE_RETRIES):
        fingerprint = _cards_fingerprint(path)
        if fingerprint is None:
            with _open_question_cache_lock:
                _open_question_cache.pop(path, None)
            return []

        with _open_question_cache_lock:
            cached = _open_question_cache.get(path)
            if cached and cached[0] == fingerprint:
                return [dict(item) for item in cached[1]]

        try:
            cards = _read_cards(board_id)
        except FileNotFoundError:
            continue
        if _cards_fingerprint(path) != fingerprint:
            continue

        found = _question_items(board_id, cards)
        with _open_question_cache_lock:
            _open_question_cache[path] = (fingerprint, found)
        return [dict(item) for item in found]

    try:
        return [dict(item) for item in _question_items(board_id, _read_cards(board_id))]
    except FileNotFoundError:
        return []


def open_questions(board_id: str | None = None) -> list[dict]:
    """Every unanswered question, newest first, across one board or all of them."""
    from board_store import list_boards

    global _open_question_cache_home
    board_dir = _cards_path("").parent
    with _open_question_cache_lock:
        if _open_question_cache_home != board_dir:
            _open_question_cache.clear()
            _open_question_cache_home = board_dir

    board_ids = [board_id] if board_id else [board.id for board in list_boards()]
    if board_id is None:
        live_paths = {_cards_path(current_board_id) for current_board_id in board_ids}
        with _open_question_cache_lock:
            stale_paths = {
                cached_path
                for cached_path in _open_question_cache
                if cached_path.parent == board_dir and cached_path not in live_paths
            }
            for stale_path in stale_paths:
                _open_question_cache.pop(stale_path, None)

    found = [
        item
        for current_board_id in board_ids
        for item in _open_questions_for_board(current_board_id)
    ]
    found.sort(key=lambda item: item["created_at"], reverse=True)
    return found


def revert_card(board_id: str, card_id: str, data: RevertCard) -> Card | None:
    """Restore a card's content to one of its earlier versions.

    Reverting is itself a mutation recorded forward, never a rewrite of the
    journal: the card's history keeps both the versions that were undone and
    the act of undoing them.
    """
    with board_transaction(board_id) as tx:
        versions = card_history.card_versions(board_id, card_id)
        if data.version < 1 or data.version > len(versions):
            return None
        target = versions[data.version - 1].snapshot

        card = next((c for c in tx.cards if c.id == card_id), None)
        if not card:
            return None

        board = get_board(board_id)
        target_column = target.get("column_id")
        # The column the card used to live in may since have been deleted; a
        # revert that would strand it in a nonexistent lane is refused outright.
        if not board or target_column not in [c.id for c in board.columns]:
            return None
        target_parent = target.get("parent_id")
        if target_parent:
            # The old parent may be gone, or may since have become this card's
            # own descendant -- restoring either one would corrupt the tree.
            descendants = {c.id for c in _collect_descendants(tx.cards, card_id)}
            if not any(c.id == target_parent for c in tx.cards) or target_parent in descendants:
                return None

        before = _snapshot(card)
        old_col = card.column_id
        closed_at = card.metadata.get(CLOSED_AT_METADATA_KEY)
        card.title = target["title"]
        card.body = target["body"]
        card.column_id = target_column
        card.parent_id = target_parent
        card.priority = target["priority"]
        card.labels = list(target["labels"])
        card.external_id = target["external_id"]
        card.metadata = dict(target["metadata"])
        if closed_at is not None and card.column_id == _closed_column_id(board_id):
            card.metadata[CLOSED_AT_METADATA_KEY] = closed_at

        now = _now()
        _stamp_closed_state(card, _closed_column_id(board_id), now)
        tx.record(_record(
            card, "reverted", before, "card_reverted",
            detail=f"{card.title} → version {data.version}",
            force=True,
        ))
        card.updated_at = now
        _reindex_column(board_id, tx.cards, card.column_id, tx.edges)
        if old_col != card.column_id:
            _reindex_column(board_id, tx.cards, old_col, tx.edges)
        return card


def get_events(board_id: str, limit: int = 100) -> list[Event]:
    """Recent board activity, newest first.

    Reads only the tail it needs and drops the per-event card snapshots: the
    feed answers "what happened", and carrying every version's full body would
    make a routine feed request scale with the whole journal.
    """
    return [
        event.model_copy(update={"snapshot": None})
        for event in card_history.read_events(board_id, tail=limit)[::-1]
    ]
