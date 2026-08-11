"""Snapshot and diff semantics for cards.

One home for "which fields count as card content", how a card is snapshotted,
and how two snapshots differ. The store and the history reader both need these
and must agree, so neither owns them.
"""

import hashlib
import json
from difflib import unified_diff
from typing import Any

from models import CardChangeItem, FieldChange

# The fields that make up a card's content. Position is excluded on purpose:
# it is a view concern owned by column reindexing, not something a session
# authored.
TRACKED_CARD_FIELDS = (
    "title", "body", "column_id", "parent_id", "priority", "labels", "metadata",
    "semantics", "external_id",
)

# Cap for values copied onto the card's own history projection. The journal is
# never truncated -- faithful reconstruction depends on full values.
HISTORY_VALUE_CHARS = 400
CHANGELOG_PROJECTION_VERSION = 1
_MISSING = object()


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
        elif field == "semantics":
            value = value.sparse_dump() if value else {}
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


def canonical_document(card, exclude_metadata_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """Authored card state normalized for stable changelog projection."""
    document = snapshot_of(card, exclude_metadata_keys)
    document["labels"] = {
        label: True
        for label in sorted(set(document["labels"] or []))
    }
    document.update(_canonical_entities(card))
    return _canonical_value(document)


def _canonical_entities(card) -> dict[str, dict[str, dict[str, Any]]]:
    notes: dict[str, dict[str, Any]] = {}
    questions: dict[str, dict[str, Any]] = {}
    answers: dict[str, dict[str, Any]] = {}
    for note in card.notes:
        collection = questions if note.kind == "question" else notes
        collection[note.id] = {"text": note.text}
        if note.kind == "question" and note.answer:
            answers[note.id] = {"text": note.answer}

    claims = {
        entry.claim_id: {
            key: value
            for key, value in {
                "session_id": entry.session_id,
                "system": entry.system,
                "action": entry.action,
                "outcome": entry.outcome,
            }.items()
            if value not in ("", None)
        }
        for entry in card.session_history
        if entry.claim_id
    }
    return {
        "notes": notes,
        "questions": questions,
        "answers": answers,
        "claims": claims,
    }


def project_change_items(
    event_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    commit_sha: str = "",
) -> list[CardChangeItem]:
    """Project adjacent canonical documents into deterministic leaf changes."""
    operations: list[tuple[str, str, Any, Any]] = []
    _collect_operations(
        "",
        _MISSING if before is None else before,
        _MISSING if after is None else after,
        operations,
    )

    return [
        CardChangeItem(
            id=_change_item_id(event_id, ordinal, path, operation, old, new),
            event_id=event_id,
            projection_version=CHANGELOG_PROJECTION_VERSION,
            ordinal=ordinal,
            path=path,
            operation=operation,
            diff=_render_diff(old, new),
            commit_sha=commit_sha,
        )
        for ordinal, (path, operation, old, new) in enumerate(operations)
    ]


def _collect_operations(path, before, after, operations):
    if before == after:
        return
    if before is _MISSING:
        if isinstance(after, dict) and after:
            for key in sorted(after):
                _collect_operations(
                    f"{path}/{_escape_pointer(str(key))}",
                    _MISSING,
                    after[key],
                    operations,
                )
            return
    if after is _MISSING:
        if isinstance(before, dict) and before:
            for key in sorted(before):
                _collect_operations(
                    f"{path}/{_escape_pointer(str(key))}",
                    before[key],
                    _MISSING,
                    operations,
                )
            return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}/{_escape_pointer(str(key))}"
            old = before.get(key, _MISSING)
            new = after.get(key, _MISSING)
            _collect_operations(child_path, old, new, operations)
        return
    operation = "add" if before is _MISSING else "remove" if after is _MISSING else "replace"
    operations.append((path, operation, before, after))


def _change_item_id(event_id, ordinal, path, operation, before, after):
    identity = {
        "projection_version": CHANGELOG_PROJECTION_VERSION,
        "event_id": event_id,
        "ordinal": ordinal,
        "path": path,
        "operation": operation,
        "before": _identity_value(before),
        "after": _identity_value(after),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _render_diff(before, after):
    return "\n".join(
        unified_diff(
            _render_lines(before),
            _render_lines(after),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )


def _render_lines(value):
    if value is _MISSING:
        return []
    rendered = _render_value(value)
    return rendered.split("\n")


def _render_value(value):
    if value is _MISSING:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _identity_value(value):
    return {"missing": True} if value is _MISSING else value


def _canonical_value(value):
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def _escape_pointer(value):
    return value.replace("~", "~0").replace("/", "~1")


def _truncated(value):
    if isinstance(value, str) and len(value) > HISTORY_VALUE_CHARS:
        return value[:HISTORY_VALUE_CHARS] + "…"
    return value
