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


BODY_SNIPPET_CHARS = 240
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
_CURSOR_VERSION = 1
_CURSOR_SIGNATURE_BYTES = 32
_CURSOR_SECRET_BYTES = 32


class InvalidCursor(ValueError):
    pass


class StaleCursor(ValueError):
    pass


def compact_card(
    card: Mapping[str, Any],
    relation_roles: Sequence[str] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = str(card.get("body") or "")
    projected = {
        "id": card.get("id", ""),
        "title": card.get("title", ""),
        "column_id": card.get("column_id", ""),
        "parent_id": card.get("parent_id"),
        "priority": card.get("priority", "medium"),
        "labels": list(card.get("labels") or []),
        "body_snippet": body[:BODY_SNIPPET_CHARS],
        "has_more_body": len(body) > BODY_SNIPPET_CHARS,
    }
    if relation_roles is not None:
        projected["relation_roles"] = list(relation_roles)
    if extras:
        projected.update(extras)
    return projected


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
) -> dict[str, Any]:
    request_fingerprint = _fingerprint({
        "board_id": board_id,
        "endpoint": endpoint,
        "request": request,
        "limit": limit,
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
        compact_card(
            card,
            (relation_roles or {}).get(card["id"]),
            (item_extras or {}).get(card["id"]),
        )
        for card in page
    ]
    envelope = {"items": items, "next_cursor": next_cursor, "has_more": has_more}
    if envelope_extras:
        envelope.update(envelope_extras)
    return envelope


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
