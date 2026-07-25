"""Card persistence. Cards are stored in per-board JSON files."""

import fcntl
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from models import (
    AddSession,
    Card,
    Event,
    FieldChange,
    MoveCard,
    SessionEntry,
    UpdateCard,
    _now,
)
from paths import board_path, events_path

from board_store import get_board

CLOSED_CARD_TTL = timedelta(days=3)
CLOSED_AT_METADATA_KEY = "closed_at"

# Card authorship history. Every mutator records who changed what, so a card
# carries its own audit trail instead of leaving it to callers to remember.
_HISTORY_LIMIT = 50
_HISTORY_VALUE_CHARS = 400
_TRACKED_FIELDS = (
    "title", "body", "column_id", "parent_id", "priority", "labels", "metadata", "external_id",
)


def acting_session() -> tuple[str, str]:
    """The Better Agent session driving this call, as ``(session_id, system)``.

    Better Agent spawns an MCP server per session with the id in its env, so a
    tool call attributes itself with no caller cooperation. The standalone API
    and the shared extension-host process have no session in scope; those
    changes are recorded with an empty session_id — unknown authorship, never
    guessed authorship."""
    session_id = (
        os.environ.get("BETTER_AGENT_APP_SESSION_ID")
        or os.environ.get("BETTER_CLAUDE_APP_SESSION_ID")
        or ""
    ).strip()
    system = (os.environ.get("BETTER_AGENT_PROVIDER_KIND") or "").strip()
    return session_id, system


def _history_value(value):
    """Values are stored for display; long bodies and big metadata blobs get
    truncated so history stays a fraction of the card."""
    if isinstance(value, str) and len(value) > _HISTORY_VALUE_CHARS:
        return value[:_HISTORY_VALUE_CHARS] + "…"
    return value


def _snapshot(card: Card) -> dict:
    snapshot = {}
    for field in _TRACKED_FIELDS:
        value = getattr(card, field, None)
        if field == "labels":
            value = list(value or [])
        elif field == "metadata":
            # closed_at is stamped by the store itself, not by the caller, so
            # it would report as a user edit on every close.
            value = {k: v for k, v in dict(value or {}).items() if k != CLOSED_AT_METADATA_KEY}
        snapshot[field] = value
    return snapshot


def _diff(before: dict, after: dict) -> list[FieldChange]:
    return [
        FieldChange(field=field, before=_history_value(before.get(field)), after=_history_value(after.get(field)))
        for field in _TRACKED_FIELDS
        if before.get(field) != after.get(field)
    ]


def _record_history(card: Card, action: str, changes: list[FieldChange]) -> None:
    if action != "created" and not changes:
        return
    session_id, system = acting_session()
    card.session_history.append(
        SessionEntry(session_id=session_id, system=system, action=action, changes=changes)
    )
    if len(card.session_history) > _HISTORY_LIMIT:
        del card.session_history[:-_HISTORY_LIMIT]


def _cards_path(board_id: str) -> Path:
    return board_path(board_id).parent / f"{board_id}_cards.json"


def _reindex_column(cards: list[Card], column_id: str) -> None:
    """Sort cards within a column by position, then reassign sequential positions."""
    col_cards = sorted(
        [c for c in cards if c.column_id == column_id],
        key=lambda c: c.position,
    )
    for i, c in enumerate(col_cards):
        c.position = i


def _read_cards(board_id: str) -> list[Card]:
    p = _cards_path(board_id)
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return [Card.model_validate(c) for c in data]


def _write_cards(board_id: str, cards: list[Card]) -> None:
    p = _cards_path(board_id)
    p.write_text(json.dumps([c.model_dump(mode="json") for c in cards], indent=2, default=str))


def _with_lock(board_id: str) -> Path:
    """Return a lock file path for a board. Used by _locked_write."""
    d = board_path(board_id).parent
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{board_id}.lock"


def _locked_write(board_id: str, cards: list[Card], event: Event | None = None) -> None:
    """Write cards (and optionally append an event) under a per-board file lock."""
    lock_path = _with_lock(board_id)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _write_cards(board_id, cards)
            if event:
                ep = events_path(board_id)
                with open(ep, "a") as f:
                    f.write(event.model_dump_json() + "\n")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


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

    cards = _read_cards(board_id)
    now = _now()
    cutoff = now - CLOSED_CARD_TTL
    retained = [
        card for card in cards
        if card.column_id != closed_column_id or _closed_at(card) > cutoff
    ]
    if len(retained) == len(cards):
        return

    _reindex_column(retained, closed_column_id)
    _locked_write(
        board_id,
        retained,
        Event(type="closed_cards_pruned", detail=f"{len(cards) - len(retained)} closed cards expired"),
    )


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


def create_card(board_id: str, data: "CreateCard") -> Card | None:
    from models import CreateCard
    board = get_board(board_id)
    if not board:
        return None
    col_ids = [c.id for c in board.columns]
    if data.column_id not in col_ids:
        return None

    cards = _read_cards(board_id)
    now = _now()
    col_cards = [c for c in cards if c.column_id == data.column_id]
    pos = data.position if data.position is not None else len(col_cards)
    pos = min(pos, len(col_cards))
    labels = [l.lower() for l in data.labels]

    card = Card(
        board_id=board_id,
        external_id=data.external_id,
        title=data.title,
        body=data.body,
        column_id=data.column_id,
        parent_id=data.parent_id,
        position=pos,
        priority=data.priority,
        labels=labels,
        metadata=data.metadata,
    )
    _stamp_closed_state(card, _closed_column_id(board_id), now)
    _record_history(card, "created", [])
    cards.append(card)
    _reindex_column(cards, data.column_id)
    _locked_write(board_id, cards, Event(type="card_created", detail=card.title))
    return card


def update_card(board_id: str, card_id: str, data: "UpdateCard") -> Card | None:
    from models import UpdateCard
    cards = _read_cards(board_id)
    card = next((c for c in cards if c.id == card_id), None)
    if not card:
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
        board = get_board(board_id)
        if board and data.column_id in [c.id for c in board.columns]:
            card.column_id = data.column_id
            col_changed = True
    if data.position is not None:
        card.position = data.position
    if data.priority is not None:
        card.priority = data.priority
    if data.labels is not None:
        card.labels = [l.lower() for l in data.labels]
    if data.metadata is not None:
        card.metadata = dict(data.metadata)
        if existing_closed_at is not None and card.column_id == closed_column_id:
            card.metadata[CLOSED_AT_METADATA_KEY] = existing_closed_at

    if col_changed or data.position is not None:
        _reindex_column(cards, card.column_id)
        if col_changed and old_col != card.column_id:
            _reindex_column(cards, old_col)

    _stamp_closed_state(card, closed_column_id, now)
    _record_history(card, "updated", _diff(before, _snapshot(card)))
    card.updated_at = now
    _locked_write(board_id, cards, Event(type="card_updated", detail=card.title))
    return card


def _collect_descendants(cards: list[Card], parent_id: str) -> list[Card]:
    """Recursively collect all descendants of a card."""
    result: list[Card] = []
    for c in cards:
        if c.parent_id == parent_id:
            result.append(c)
            result.extend(_collect_descendants(cards, c.id))
    return result


def move_card(board_id: str, card_id: str, data: MoveCard) -> Card | None:
    board = get_board(board_id)
    if not board:
        return None
    if data.column_id not in [c.id for c in board.columns]:
        return None

    cards = _read_cards(board_id)
    card = next((c for c in cards if c.id == card_id), None)
    if not card:
        return None

    closed_column_id = _closed_column_id(board_id)
    now = _now()
    before = _snapshot(card)
    old_col = card.column_id
    card.column_id = data.column_id

    col_cards = [c for c in cards if c.column_id == data.column_id and c.id != card_id]
    pos = data.position if data.position is not None else len(col_cards)
    card.position = min(pos, len(col_cards))

    # Cascade column move to all descendants
    if old_col != data.column_id:
        for desc in _collect_descendants(cards, card_id):
            desc_before = _snapshot(desc)
            desc.column_id = data.column_id
            _stamp_closed_state(desc, closed_column_id, now)
            # A sub-task dragged along by its parent still changed lane, so it
            # records the move under its own history.
            _record_history(desc, "moved", _diff(desc_before, _snapshot(desc)))
            desc.updated_at = now

    _stamp_closed_state(card, closed_column_id, now)
    _record_history(card, "moved", _diff(before, _snapshot(card)))
    card.updated_at = now
    _reindex_column(cards, data.column_id)
    if old_col != data.column_id:
        _reindex_column(cards, old_col)
    _locked_write(board_id, cards, Event(
        type="card_moved",
        detail=f"{card.title}: {old_col[:8]}… → {data.column_id[:8]}…",
    ))
    return card


def delete_card(board_id: str, card_id: str) -> bool:
    cards = _read_cards(board_id)
    card = next((c for c in cards if c.id == card_id), None)
    if not card:
        return False
    col_id = card.column_id
    cards = [c for c in cards if c.id != card_id]
    _reindex_column(cards, col_id)
    _locked_write(board_id, cards, Event(type="card_deleted", detail=card_id))
    # Same orphan-cleanup pattern board_store.delete_column already applies
    # to cards -- a deleted card must not leave dangling edges behind.
    from edge_store import delete_edges_for_card
    delete_edges_for_card(board_id, card_id)
    return True


def add_session(board_id: str, card_id: str, data: AddSession) -> Card | None:
    cards = _read_cards(board_id)
    card = next((c for c in cards if c.id == card_id), None)
    if not card:
        return None

    entry = SessionEntry(
        session_id=data.session_id,
        system=data.system,
        action=data.action,
        outcome=data.outcome,
    )
    card.session_history.append(entry)
    card.updated_at = _now()
    _locked_write(board_id, cards, Event(
        type="session_added",
        detail=f"session {data.session_id[:8]}… → {card.title}",
        actor=data.system or data.session_id,
    ))
    return card


def get_events(board_id: str, limit: int = 100) -> list[Event]:
    ep = events_path(board_id)
    if not ep.exists():
        return []
    lines = ep.read_text().strip().split("\n")
    events = []
    for line in reversed(lines[-limit:]):
        if line.strip():
            events.append(Event.model_validate_json(line))
    return events
