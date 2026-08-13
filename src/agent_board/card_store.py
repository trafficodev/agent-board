"""Card persistence. One row per card in SQLite."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta

from . import card_history
from . import db
from .card_semantics import CardSemantics, valid_semantic_parent
from .models import (
    AddNote,
    AddSession,
    AnswerNote,
    Card,
    CardNote,
    ChangeContext,
    CreateCard,
    Event,
    MoveCard,
    RevertCard,
    SessionEntry,
    UpdateCard,
    new_id,
    _now,
)
from .board_store import get_board
from .card_diff import (
    CHANGELOG_PROJECTION_VERSION,
    canonical_document,
    diff_snapshots,
    snapshot_of,
)
from .dag_order import dag_sort
from .edge_store import direction_is_valid, read_edges, write_edges

CLOSED_CARD_TTL = timedelta(days=3)
CLOSED_AT_METADATA_KEY = "closed_at"
DAG_ORDERED_COLUMN = "open items"

# How much authorship history the card document itself carries. This is a
# convenience projection for anyone holding a card; the journal behind
# card_history keeps every version in full, so trimming here loses nothing.
_HISTORY_LIMIT = 50


# Set only at an authored request boundary. Direct store calls are internal
# maintenance and intentionally create non-voteable records.
request_change_context: ContextVar[ChangeContext | None] = ContextVar(
    "agent_board_request_change_context",
    default=None,
)


def current_change_context() -> ChangeContext:
    context = request_change_context.get()
    if context is not None:
        return context
    session_id = (
        os.environ.get("BETTER_AGENT_APP_SESSION_ID")
        or os.environ.get("BETTER_CLAUDE_APP_SESSION_ID")
        or ""
    ).strip()
    if session_id:
        return ChangeContext.legacy_session(
            (os.environ.get("BETTER_AGENT_PROVIDER_KIND") or "legacy").strip(),
            session_id,
        )
    return ChangeContext.system("direct-store")


def acting_session() -> tuple[str, str]:
    """Compatibility projection for the card's capped session history."""
    context = current_change_context()
    return context.native_session_id, context.provider


def _snapshot(card: Card) -> dict:
    # closed_at is stamped by the store itself, not by the caller, so it would
    # otherwise report as a user edit on every close.
    return snapshot_of(card, exclude_metadata_keys=(CLOSED_AT_METADATA_KEY,))


def _append_session_entry(card: Card, entry: SessionEntry) -> None:
    card.session_history.append(entry)
    overflow = sum(not item.claim_id for item in card.session_history) - _HISTORY_LIMIT
    if overflow <= 0:
        return
    retained: list[SessionEntry] = []
    for item in card.session_history:
        if overflow and not item.claim_id:
            overflow -= 1
            continue
        retained.append(item)
    card.session_history = retained


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
    context = current_change_context()
    session_id, system = context.native_session_id, context.provider
    if is_version:
        _append_session_entry(card, SessionEntry(
            session_id=session_id,
            system=system,
            action=action,
            changes=diff_snapshots(before, after, truncate=True),
        ))
    return Event(
        type=event_type,
        detail=detail or card.title,
        board_id=card.board_id,
        card_id=card.id,
        session_id=session_id,
        system=system,
        action=action,
        snapshot=after if is_version else None,
        projection_version=CHANGELOG_PROJECTION_VERSION if is_version else 0,
        document=canonical_document(card) if is_version else None,
        provider=context.provider,
        native_session_id=context.native_session_id,
        reviewed_commit_sha=context.reviewed_commit_sha,
        voteable=context.voteable and is_version,
        cutover_state="authored",
    )


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


def _card_rows(conn, board_id: str, **filters) -> list[Card]:
    """Cards matching an indexed filter, in column order.

    Filters are pushed into SQL rather than applied after loading the board:
    that is the difference between a query and a full deserialization, and it
    is why the store moved off one document per board.
    """
    where = ["board_id = ?"]
    params: list = [board_id]
    if filters.get("column_id"):
        where.append("column_id = ?")
        params.append(filters["column_id"])
    if filters.get("priority"):
        where.append("priority = ?")
        params.append(filters["priority"])
    if filters.get("parent_id") is not None:
        parent_id = filters["parent_id"]
        # An explicit "no parent" is a real filter, not a missing one.
        if parent_id in ("", "null", None):
            where.append("parent_id IS NULL")
        else:
            where.append("parent_id = ?")
            params.append(parent_id)
    if filters.get("label"):
        where.append(
            "id IN (SELECT card_id FROM card_labels WHERE board_id = ? AND label = ?)"
        )
        params.extend([board_id, filters["label"].lower()])
    rows = conn.execute(
        f"SELECT * FROM cards WHERE {' AND '.join(where)} ORDER BY position, rowid", params
    ).fetchall()
    return db.hydrate_cards(conn, rows)


def _read_cards(board_id: str) -> list[Card]:
    return _card_rows(db.connect(), board_id)


def _write_cards(
    board_id: str, cards: list[Card], original: dict[str, dict] | None = None
) -> None:
    """Persist a card list as the board's whole set, writing only what changed.

    ``original`` is what the caller's transaction read. Rewriting every card on
    every mutation is what the JSON document did and what made a large board
    slow; here an untouched card costs nothing. A caller that has no baseline
    -- anything replacing a board's cards outright -- gets it read here, so the
    saving is an optimization rather than a contract the caller has to honour.
    """
    with db.transaction() as conn:
        if original is None:
            original = {
                card.id: card.model_dump(mode="json")
                for card in _card_rows(conn, board_id)
            }
        surviving = set()
        for card in cards:
            surviving.add(card.id)
            snapshot = card.model_dump(mode="json")
            if original.get(card.id) != snapshot:
                db.write_card(conn, card)
        removed = set(original) - surviving
        if removed:
            conn.executemany(
                "DELETE FROM cards WHERE id=?", [(card_id,) for card_id in removed]
            )


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
        # What was on disk when this transaction opened, so the write can tell
        # which cards it actually has to touch.
        self.original = {card.id: card.model_dump(mode="json") for card in cards}

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
    """Read, mutate and write a board's cards in one write transaction.

    The read has to happen inside the transaction: a mutator works on a list it
    read, so reading before taking the write lock lets two concurrent writers
    each save a list that is missing the other's card. That would also strand
    the journal, which would still hold a `created` record for a card no longer
    on the board. BEGIN IMMEDIATE is what makes that impossible, and it is why
    the old per-board lock file is gone.
    """
    with db.transaction():
        tx = _Transaction(_read_cards(board_id), read_edges(board_id))
        yield tx
        if tx.commit:
            _write_cards(board_id, tx.cards, tx.original)
            if tx.edges_dirty:
                write_edges(board_id, tx.edges)
            for event in tx.events:
                event.board_id = event.board_id or board_id
                db.write_event(db.connect(), event)


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
    return _card_rows(
        db.connect(), board_id,
        priority=priority, label=label, column_id=column_id, parent_id=parent_id,
    )


def get_card(board_id: str, card_id: str) -> Card | None:
    _prune_expired_closed_cards(board_id)
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM cards WHERE board_id=? AND id=?", (board_id, card_id)
    ).fetchall()
    cards = db.hydrate_cards(conn, rows)
    return cards[0] if cards else None


def apply_create(tx: _Transaction, board_id: str, data: CreateCard) -> Card | None:
    """Create one card inside an already-open transaction.

    The public single-card entry points and the bulk executor both route
    through the apply_* helpers, so batching a change can never drift from
    doing it one at a time.
    """
    board = get_board(board_id)
    if not board or data.column_id not in [c.id for c in board.columns]:
        return None
    parent = next((card for card in tx.cards if card.id == data.parent_id), None)
    if data.parent_id and parent is None:
        return None
    if data.semantics.kind and not valid_semantic_parent(
        data.semantics,
        parent.semantics if parent else None,
    ):
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
        semantics=data.semantics,
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
        and card.semantics == data.semantics
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

    final_parent_id = data.parent_id if "parent_id" in data.model_fields_set else card.parent_id
    final_parent = next((c for c in tx.cards if c.id == final_parent_id), None)
    final_semantics = data.semantics if data.semantics is not None else card.semantics
    if final_semantics.kind and not valid_semantic_parent(
        final_semantics,
        final_parent.semantics if final_parent else None,
    ):
        return None
    for child in (candidate for candidate in tx.cards if candidate.parent_id == card_id):
        if child.semantics.kind and not valid_semantic_parent(child.semantics, final_semantics):
            return None
    proposed = card.model_copy(update={
        "parent_id": final_parent_id,
        "semantics": final_semantics,
    })
    cards_by_id = {candidate.id: candidate for candidate in tx.cards}
    cards_by_id[card_id] = proposed
    for edge in (
        candidate
        for candidate in tx.edges
        if card_id in {candidate.from_card_id, candidate.to_card_id}
    ):
        source = cards_by_id.get(edge.from_card_id)
        target = cards_by_id.get(edge.to_card_id)
        if not source or not target or not direction_is_valid(edge.type, source, target):
            return None
        if edge.type in {"defines", "parent_of"} and target.parent_id != source.id:
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
    if data.semantics is not None:
        card.semantics = data.semantics

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
    context = current_change_context()
    # The card is going away but its history is not: the journal keeps its
    # final state, so a deleted card can still be inspected afterwards.
    tx.record(Event(
        type="card_deleted",
        detail=card.title,
        board_id=board_id,
        card_id=card_id,
        session_id=context.native_session_id,
        system=context.provider,
        action="deleted",
        snapshot=_snapshot(card),
        projection_version=CHANGELOG_PROJECTION_VERSION,
        document=None,
        provider=context.provider,
        native_session_id=context.native_session_id,
        reviewed_commit_sha=context.reviewed_commit_sha,
        voteable=context.voteable,
        cutover_state="authored",
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

        # The reported session is the subject in the card projection; the
        # request context still identifies who authored the event.
        _append_session_entry(card, SessionEntry(
            claim_id=new_id(),
            session_id=data.session_id,
            system=data.system,
            action=data.action,
            outcome=data.outcome,
        ))
        card.updated_at = _now()
        context = current_change_context()
        tx.record(Event(
            type="session_added",
            detail=f"session {data.session_id[:8]}… → {card.title}",
            board_id=board_id,
            card_id=card.id,
            session_id=data.session_id,
            system=data.system,
            action=data.action or "worked",
            snapshot=_snapshot(card),
            projection_version=CHANGELOG_PROJECTION_VERSION,
            document=canonical_document(card),
            provider=context.provider,
            native_session_id=context.native_session_id,
            reviewed_commit_sha=context.reviewed_commit_sha,
            voteable=context.voteable,
            cutover_state="authored",
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


def open_questions(board_id: str | None = None) -> list[dict]:
    """Every unanswered question, newest first, across one board or all of them.

    One indexed query against the partial index over unanswered questions. The
    old cached projection file and its fingerprint invalidation existed only
    because answering this meant reading every card of every board; nothing has
    to be cached now, so nothing can go stale.
    """
    where = ["n.kind = 'question'", "n.answer = ''"]
    params: list = []
    if board_id:
        where.append("n.board_id = ?")
        params.append(board_id)
    rows = db.connect().execute(
        f"""SELECT n.board_id, n.card_id, c.title AS card_title, n.id AS note_id,
                   n.text, n.session_id, n.created_at
              FROM card_notes n JOIN cards c ON c.id = n.card_id
             WHERE {' AND '.join(where)}
             ORDER BY n.created_at DESC""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


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
        parent_card = None
        if target_parent:
            # The old parent may be gone, or may since have become this card's
            # own descendant -- restoring either one would corrupt the tree.
            descendants = {c.id for c in _collect_descendants(tx.cards, card_id)}
            if not any(c.id == target_parent for c in tx.cards) or target_parent in descendants:
                return None
            parent_card = next(c for c in tx.cards if c.id == target_parent)
        target_semantics = CardSemantics(**target.get("semantics", {}))
        if target_semantics.kind and not valid_semantic_parent(
            target_semantics,
            parent_card.semantics if parent_card else None,
        ):
            return None
        for child in (candidate for candidate in tx.cards if candidate.parent_id == card_id):
            if child.semantics.kind and not valid_semantic_parent(
                child.semantics,
                target_semantics,
            ):
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
        card.semantics = target_semantics
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
