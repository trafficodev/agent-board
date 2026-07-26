"""Snapshot and diff semantics for cards.

One home for "which fields count as card content", how a card is snapshotted,
and how two snapshots differ. The store and the history reader both need these
and must agree, so neither owns them.
"""

from typing import Any

from models import FieldChange

# The fields that make up a card's content. Position is excluded on purpose:
# it is a view concern owned by column reindexing, not something a session
# authored.
TRACKED_CARD_FIELDS = (
    "title", "body", "column_id", "parent_id", "priority", "labels", "metadata", "external_id",
)

# Cap for values copied onto the card's own history projection. The journal is
# never truncated -- faithful reconstruction depends on full values.
HISTORY_VALUE_CHARS = 400


def snapshot_of(card, exclude_metadata_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """Full, untruncated state of a card's tracked fields."""
    snapshot: dict[str, Any] = {}
    for field in TRACKED_CARD_FIELDS:
        value = getattr(card, field, None)
        if field == "labels":
            value = list(value or [])
        elif field == "metadata":
            value = {
                k: v for k, v in dict(value or {}).items()
                if k not in exclude_metadata_keys
            }
        snapshot[field] = value
    return snapshot


def diff_snapshots(before: dict, after: dict, truncate: bool = False) -> list[FieldChange]:
    """Fields that differ between two snapshots.

    ``truncate`` is for the on-card projection only; the journal stores whole
    values so a reconstruction is faithful.
    """
    render = _truncated if truncate else (lambda value: value)
    return [
        FieldChange(field=field, before=render(before.get(field)), after=render(after.get(field)))
        for field in TRACKED_CARD_FIELDS
        if before.get(field) != after.get(field)
    ]


def _truncated(value):
    if isinstance(value, str) and len(value) > HISTORY_VALUE_CHARS:
        return value[:HISTORY_VALUE_CHARS] + "…"
    return value
