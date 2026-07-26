"""Read side of the card journal.

``events.jsonl`` is the authoritative history of every card: append-only, never
truncated, and independent of the card document, so history outlives both the
card's own capped ``session_history`` projection and the card's deletion. This
module only reads and replays it; the store owns appending, because an append
has to happen under the same board lock as the card write it describes.
"""

from datetime import datetime, timezone

from models import CardVersion, Event
from paths import events_path

from card_diff import diff_snapshots

def read_events(board_id: str, tail: int | None = None) -> list[Event]:
    """A board's events, oldest first, or only the last ``tail`` of them.

    A crash can interrupt an append mid-line, so a torn *final* line is
    tolerated. An unparseable line anywhere else means the journal is corrupt
    and is raised rather than skipped: silently dropping an interior record
    renumbers every version after it, which would make a revert restore the
    wrong content.
    """
    path = events_path(board_id)
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    if tail is not None:
        lines = lines[-tail:] if tail > 0 else []
    events: list[Event] = []
    for index, line in enumerate(lines):
        try:
            events.append(Event.model_validate_json(line))
        except ValueError as exc:
            if index == len(lines) - 1:
                break
            raise ValueError(f"{path}: corrupt journal record at line {index + 1}") from exc
    return events


def card_versions(board_id: str, card_id: str) -> list[CardVersion]:
    """Every recorded version of one card, oldest first.

    ``changes`` is derived here by diffing consecutive snapshots rather than
    stored alongside them, so the two can never disagree.
    """
    previous: dict = {}
    versions: list[CardVersion] = []
    for event in read_events(board_id):
        if event.card_id != card_id or event.snapshot is None:
            continue
        snapshot = event.snapshot
        versions.append(CardVersion(
            version=len(versions) + 1,
            event_id=event.id,
            timestamp=event.timestamp,
            action=event.action or event.type,
            session_id=event.session_id,
            system=event.system,
            changes=diff_snapshots(previous, snapshot),
            snapshot=snapshot,
        ))
        previous = snapshot
    return versions


def card_as_of(board_id: str, card_id: str, when: datetime) -> CardVersion | None:
    """The card as it stood at a moment in time, or None if it did not exist
    yet. Reads the last snapshot at or before ``when`` instead of replaying
    diffs forward, so a missing record cannot silently skew the result."""
    # Journal timestamps are UTC-aware; a caller-supplied naive one is read as
    # UTC rather than allowed to blow up the comparison.
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    matching = [v for v in card_versions(board_id, card_id) if v.timestamp <= when]
    return matching[-1] if matching else None


def session_activity(session_id: str, board_id: str | None = None, limit: int = 200) -> list[Event]:
    """What one session did, newest first, across every board or just one.

    This is the question the per-card view cannot answer: a session's work
    normally spans cards and boards.
    """
    from board_store import list_boards

    if not session_id:
        return []
    board_ids = [board_id] if board_id else [b.id for b in list_boards()]
    found = [
        event
        for bid in board_ids
        for event in read_events(bid)
        if event.session_id == session_id
    ]
    found.sort(key=lambda event: event.timestamp, reverse=True)
    return found[:limit]
