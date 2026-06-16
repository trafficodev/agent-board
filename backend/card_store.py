"""Card persistence. Cards are stored in per-board JSON files."""

import fcntl
import json
from pathlib import Path

from models import (
    AddSession,
    Card,
    Event,
    MoveCard,
    SessionEntry,
    UpdateCard,
    _now,
)
from paths import board_path, events_path

from board_store import get_board


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


# --- Cards ---

def list_cards(board_id: str, priority: str | None = None, label: str | None = None, column_id: str | None = None, parent_id: str | None = None) -> list[Card]:
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
    cards = _read_cards(board_id)
    return next((c for c in cards if c.id == card_id), None)


def create_card(board_id: str, data: "UpdateCard") -> Card | None:
    from models import CreateCard
    board = get_board(board_id)
    if not board:
        return None
    col_ids = [c.id for c in board.columns]
    if data.column_id not in col_ids:
        return None

    cards = _read_cards(board_id)
    col_cards = [c for c in cards if c.column_id == data.column_id]
    pos = data.position if data.position is not None else len(col_cards)
    pos = min(pos, len(col_cards))
    labels = [l.lower() for l in data.labels]

    card = Card(
        board_id=board_id,
        title=data.title,
        body=data.body,
        column_id=data.column_id,
        parent_id=data.parent_id,
        position=pos,
        priority=data.priority,
        labels=labels,
    )
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

    if col_changed or data.position is not None:
        _reindex_column(cards, card.column_id)
        if col_changed and old_col != card.column_id:
            _reindex_column(cards, old_col)

    card.updated_at = _now()
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

    old_col = card.column_id
    card.column_id = data.column_id

    col_cards = [c for c in cards if c.column_id == data.column_id and c.id != card_id]
    pos = data.position if data.position is not None else len(col_cards)
    card.position = min(pos, len(col_cards))

    # Cascade column move to all descendants
    if old_col != data.column_id:
        for desc in _collect_descendants(cards, card_id):
            desc.column_id = data.column_id
            desc.updated_at = _now()

    card.updated_at = _now()
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
