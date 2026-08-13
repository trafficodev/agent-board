"""Read side of the card journal.

The ``events`` table is the authoritative history of every card: append-only,
never truncated, and independent of the card row, so history outlives both the
card's own capped ``session_history`` projection and the card's deletion. This
module only reads and replays it; the store owns appending, because an append
has to happen in the same transaction as the card write it describes.
"""

from datetime import datetime, timezone

from . import db
from .models import CardChangeItem, CardVersion, ChangeContext, Event

from .card_diff import CHANGELOG_PROJECTION_VERSION, diff_snapshots, project_change_items


def read_events(board_id: str, tail: int | None = None) -> list[Event]:
    """A board's events, oldest first, or only the last ``tail`` of them.

    ``seq`` is the journal's total order, so a tail is the last N rows by seq
    read back in ascending order -- never a re-sort by timestamp, which would
    reorder events written inside the same transaction.
    """
    if tail is not None and tail <= 0:
        return []
    if tail is None:
        rows = db.connect().execute(
            "SELECT * FROM events WHERE board_id=? ORDER BY seq", (board_id,)
        ).fetchall()
    else:
        rows = db.connect().execute(
            "SELECT * FROM events WHERE board_id=? ORDER BY seq DESC LIMIT ?",
            (board_id, tail),
        ).fetchall()[::-1]
    return [db.event_from_row(row) for row in rows]


def card_change_items(
    board_id: str,
    card_id: str,
    context: ChangeContext | None = None,
    include_votes: bool = True,
) -> list[CardChangeItem]:
    """Voteable leaf diffs projected from the card's immutable event stream."""
    rows = db.connect().execute(
        "SELECT * FROM events WHERE board_id=? AND card_id=?"
        " AND cutover_state IN ('baseline', 'authored') ORDER BY seq",
        (board_id, card_id),
    ).fetchall()
    previous: dict | None = None
    items: list[CardChangeItem] = []
    for row in rows:
        event = db.event_from_row(row)
        if event.cutover_state == "baseline":
            previous = event.document
            continue
        before = previous
        previous = event.document
        if not event.voteable or event.projection_version != CHANGELOG_PROJECTION_VERSION:
            continue
        items.extend(
            item.model_copy(update={"timestamp": event.timestamp})
            for item in project_change_items(
                event.id,
                before,
                event.document,
                commit_sha=event.reviewed_commit_sha,
            )
        )
    if not include_votes:
        return items

    from .vote_store import attach_vote_summaries

    return attach_vote_summaries(items, context)


def card_versions(board_id: str, card_id: str) -> list[CardVersion]:
    """Every recorded version of one card, oldest first.

    ``changes`` is derived here by diffing consecutive snapshots rather than
    stored alongside them, so the two can never disagree.
    """
    previous: dict = {}
    versions: list[CardVersion] = []
    rows = db.connect().execute(
        "SELECT * FROM events WHERE board_id=? AND card_id=? AND snapshot IS NOT NULL"
        " ORDER BY seq",
        (board_id, card_id),
    ).fetchall()
    for event in (db.event_from_row(row) for row in rows):
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
    normally spans cards and boards. One indexed query by session, rather than
    reading every board's journal and filtering.
    """
    if not session_id:
        return []
    where = ["session_id = ?"]
    params: list = [session_id]
    if board_id:
        where.append("board_id = ?")
        params.append(board_id)
    params.append(limit)
    rows = db.connect().execute(
        f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT ?",
        params,
    ).fetchall()
    return [db.event_from_row(row) for row in rows]
