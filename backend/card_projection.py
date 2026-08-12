from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import paths
from models import Card


BODY_SNIPPET_CHARS = 240
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
_CURSOR_VERSION = 1
_CURSOR_SIGNATURE_BYTES = 32
_CURSOR_SECRET_BYTES = 32

CARD_FIELDS = tuple(Card.model_fields)
DERIVED_FIELDS = ("body_snippet", "has_more_body", "coverage")
ENDPOINT_EXTRAS = {
    "list_cards": (),
    "search_cards": ("relation_roles",),
    "relevant_candidates": ("relevance_score", "column"),
}
FIELD_PROJECTION_DESCRIPTION = (
    "With neither include nor exclude, all endpoint fields are returned. "
    "Include selects exactly those fields; exclude alone selects all fields except "
    "those named; together, exclude removes fields from include. '*' means all "
    "fields, id always remains, and unknown fields are rejected."
)
INCLUDE_FIELDS_DESCRIPTION = (
    f"Fields to select. {FIELD_PROJECTION_DESCRIPTION} An explicit empty selection "
    "returns only id."
)
EXCLUDE_FIELDS_DESCRIPTION = (
    f"Fields to remove. {FIELD_PROJECTION_DESCRIPTION} An explicit empty selection "
    "removes nothing."
)


def coverage_extras(
    cards: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    by_id = {card["id"]: card for card in cards}
    children: dict[str, list[str]] = {}
    for card in cards:
        if card.get("parent_id"):
            children.setdefault(card["parent_id"], []).append(card["id"])

    incoming: dict[str, set[str]] = {}
    for edge in edges:
        incoming.setdefault(edge["to_card_id"], set()).add(edge["type"])

    states: dict[str, str] = {}
    for card in cards:
        semantics = card.get("semantics") or {}
        if semantics.get("kind") != "requirement":
            continue
        relation_types = incoming.get(card["id"], set())
        evidence_states = {
            item.get("state") or (
                "verified" if item.get("kind") in {"test", "validation", "screenshot"}
                else "implemented"
            )
            for item in semantics.get("evidence", [])
            if isinstance(item, Mapping)
        }
        if "verifies" in relation_types or "verified" in evidence_states:
            states[card["id"]] = "verified"
        elif "implements" in relation_types or "implemented" in evidence_states:
            states[card["id"]] = "implemented"
        elif "partial" in evidence_states:
            states[card["id"]] = "partial"
        else:
            states[card["id"]] = "uncovered"

    def rollup(card_id: str) -> str:
        if card_id in states:
            return states[card_id]
        child_states = [
            rollup(child_id)
            for child_id in children.get(card_id, [])
            if (by_id.get(child_id, {}).get("semantics") or {}).get("kind")
            in {"feature", "requirement"}
        ]
        if not child_states or all(state == "uncovered" for state in child_states):
            state = "uncovered"
        elif all(state == "verified" for state in child_states):
            state = "verified"
        elif all(state in {"implemented", "verified"} for state in child_states):
            state = "implemented"
        else:
            state = "partial"
        states[card_id] = state
        return state

    for card in cards:
        kind = (card.get("semantics") or {}).get("kind")
        if kind in {"product_area", "feature"}:
            rollup(card["id"])
    return {card_id: {"coverage": state} for card_id, state in states.items()}


class InvalidCursor(ValueError):
    pass


class StaleCursor(ValueError):
    pass


def resolve_card_fields(
    endpoint: str,
    include: str | Sequence[str] | None = None,
    exclude: str | Sequence[str] | None = None,
) -> tuple[str, ...]:
    available = available_card_fields(endpoint)
    available_set = set(available)
    included = _normalize_field_values(include)
    excluded = _normalize_field_values(exclude)
    _validate_fields(included, available_set)
    _validate_fields(excluded, available_set)

    selected = (
        set(available)
        if include is None or "*" in included
        else set(included)
    )
    if "*" in excluded:
        selected.clear()
    else:
        selected.difference_update(excluded)
    selected.add("id")
    return tuple(field for field in available if field in selected)


def available_card_fields(endpoint: str) -> tuple[str, ...]:
    try:
        return (*CARD_FIELDS, *DERIVED_FIELDS, *ENDPOINT_EXTRAS[endpoint])
    except KeyError as exc:
        raise ValueError(f"unknown_card_endpoint:{endpoint}") from exc


def project_card(
    card: Mapping[str, Any],
    fields: Sequence[str],
    relation_roles: Sequence[str] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = str(card.get("body") or "")
    values = dict(card)
    values["body_snippet"] = body[:BODY_SNIPPET_CHARS]
    values["has_more_body"] = len(body) > BODY_SNIPPET_CHARS
    if relation_roles is not None:
        values["relation_roles"] = list(relation_roles)
    for field, value in (extras or {}).items():
        values.setdefault(field, value)
    return {field: values.get(field) for field in fields}


def page_cards(
    cards: Sequence[dict[str, Any]],
    *,
    board_id: str,
    endpoint: str,
    request: Mapping[str, Any],
    limit: int,
    cursor: str | None,
    sort_key: Callable[[dict[str, Any]], tuple[Any, ...]],
    relation_roles: Mapping[str, Sequence[str]] | None = None,
    item_extras: Mapping[str, Mapping[str, Any]] | None = None,
    envelope_extras: Mapping[str, Any] | None = None,
    selected_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    fields = (
        resolve_card_fields(endpoint)
        if selected_fields is None
        else _canonicalize_selected_fields(endpoint, selected_fields)
    )
    request_fingerprint = _fingerprint({
        "board_id": board_id,
        "endpoint": endpoint,
        "request": request,
        "limit": limit,
        "fields": fields,
    })
    result_fingerprint = _fingerprint([
        {
            "card": card,
            "relation_roles": list((relation_roles or {}).get(card["id"], [])),
            "extras": (item_extras or {}).get(card["id"], {}),
        }
        for card in cards
    ])

    start = 0
    if cursor:
        payload = _decode_cursor(cursor)
        if payload.get("request") != request_fingerprint:
            raise InvalidCursor("invalid_cursor")
        if payload.get("results") != result_fingerprint:
            raise StaleCursor("stale_cursor")
        after_key = tuple(payload.get("after", []))
        if not after_key:
            raise InvalidCursor("invalid_cursor")
        start = _continuation_index(cards, after_key, sort_key)

    page = list(cards[start:start + limit])
    has_more = start + len(page) < len(cards)
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor({
            "version": _CURSOR_VERSION,
            "request": request_fingerprint,
            "results": result_fingerprint,
            "after": list(sort_key(page[-1])),
        })

    items = [
        project_card(
            card,
            fields,
            (relation_roles or {}).get(card["id"]),
            (item_extras or {}).get(card["id"]),
        )
        for card in page
    ]
    envelope = {"items": items, "next_cursor": next_cursor, "has_more": has_more}
    if envelope_extras:
        envelope.update(envelope_extras)
    return envelope


def _normalize_field_values(values: str | Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    raw_values = (values,) if isinstance(values, str) else values
    normalized: list[str] = []
    for value in raw_values:
        if not isinstance(value, str):
            raise ValueError("invalid_card_fields")
        normalized.extend(field.strip() for field in value.split(",") if field.strip())
    return tuple(dict.fromkeys(normalized))


def _validate_fields(fields: Sequence[str], available: set[str]) -> None:
    invalid = [field for field in fields if field != "*" and field not in available]
    if invalid:
        raise ValueError(f"invalid_card_fields:{','.join(invalid)}")


def _canonicalize_selected_fields(
    endpoint: str, fields: Sequence[str]
) -> tuple[str, ...]:
    available = available_card_fields(endpoint)
    normalized = _normalize_field_values(fields)
    _validate_fields(normalized, set(available))
    selected = set(normalized)
    selected.add("id")
    return tuple(field for field in available if field in selected)


def _continuation_index(
    cards: Sequence[dict[str, Any]],
    after_key: tuple[Any, ...],
    sort_key: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> int:
    for index, card in enumerate(cards):
        key = sort_key(card)
        if key == after_key:
            return index + 1
        try:
            passed_cursor = key > after_key
        except TypeError as exc:
            raise InvalidCursor("invalid_cursor") from exc
        if passed_cursor:
            raise InvalidCursor("invalid_cursor")
    raise InvalidCursor("invalid_cursor")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode_cursor(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    signature = hmac.new(_cursor_secret(), encoded, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature + encoded).decode().rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        signature = decoded[:_CURSOR_SIGNATURE_BYTES]
        encoded = decoded[_CURSOR_SIGNATURE_BYTES:]
        expected = hmac.new(_cursor_secret(), encoded, hashlib.sha256).digest()
        if len(signature) != _CURSOR_SIGNATURE_BYTES or not hmac.compare_digest(
            signature, expected
        ):
            raise ValueError
        payload = json.loads(encoded)
        if not isinstance(payload, dict) or payload.get("version") != _CURSOR_VERSION:
            raise ValueError
        return payload
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursor("invalid_cursor") from exc


def _cursor_secret() -> bytes:
    return _load_cursor_secret(str(paths.cursor_secret_path()))


@functools.cache
def _load_cursor_secret(path_value: str) -> bytes:
    path = Path(path_value)
    try:
        secret = _read_cursor_secret(path)
    except FileNotFoundError:
        secret = _create_cursor_secret(path)
    if len(secret) != _CURSOR_SECRET_BYTES:
        raise RuntimeError("Invalid cursor secret")
    return secret


def _create_cursor_secret(path: Path) -> bytes:
    secret = secrets.token_bytes(_CURSOR_SECRET_BYTES)
    fd, temporary_value = tempfile.mkstemp(
        prefix=".cursor-secret-", dir=path.parent
    )
    temporary = Path(temporary_value)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(secret)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _read_cursor_secret(path)
        return secret
    finally:
        temporary.unlink(missing_ok=True)


def _read_cursor_secret(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("Invalid cursor secret owner")
        os.fchmod(handle.fileno(), 0o600)
        return handle.read()
