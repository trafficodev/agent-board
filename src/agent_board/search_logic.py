from __future__ import annotations

import re
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FIELD_ALIASES = {
    "labels": "label",
    "type": "label",
    "files": "file",
    "edited_file": "file",
    "edited_files": "file",
    "commits": "commit",
    "git_commit": "commit",
    "git_commits": "commit",
    "content": "contains",
    "external": "external_id",
}

FILE_RE = re.compile(r"(^|\s|`)\/?[\w.-]+\/[\w./-]+")
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


@dataclass(frozen=True)
class SearchTerm:
    negative: bool
    field: str | None
    value: str


def search_cards(cards: list[dict[str, Any]], columns: list[dict[str, Any]], query: str = "", priority: str | None = None, label: str | None = None) -> list[dict[str, Any]]:
    terms = parse_search_query(query)
    column_names_by_id = {column["id"]: column.get("name", column["id"]) for column in columns}
    cards_by_id = {card["id"]: card for card in cards}
    children_by_parent = _group_children(cards)
    rg_cache: dict[tuple[str, tuple[str, ...]], bool] = {}

    direct_ids: set[str] = set()
    for card in cards:
        if priority and card.get("priority") != priority:
            continue
        if label and label not in card.get("labels", []):
            continue
        if _matches_query(card, terms, column_names_by_id, rg_cache):
            direct_ids.add(card["id"])

    if not terms and not priority and not label:
        return sorted(cards, key=_card_sort_key)

    visible_ids = set(direct_ids)
    for card_id in direct_ids:
        _add_ancestors(card_id, visible_ids, cards_by_id)
        _add_descendants(card_id, visible_ids, children_by_parent)

    return sorted([card for card in cards if card["id"] in visible_ids], key=_card_sort_key)


def parse_search_query(query: str) -> list[SearchTerm]:
    terms: list[SearchTerm] = []
    for raw_token in _tokenize(query):
        token = raw_token.strip()
        negative = token.startswith("-") and len(token) > 1
        if negative:
            token = token[1:]

        field = None
        value = token
        colon_index = token.find(":")
        if colon_index > 0:
            raw_field = _normalize(token[:colon_index])
            field = FIELD_ALIASES.get(raw_field, raw_field)
            value = token[colon_index + 1:]

        normalized = _normalize(value)
        if normalized:
            terms.append(SearchTerm(negative=negative, field=field, value=normalized))
    return terms


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    current = []
    quoted = False

    for char in query.strip():
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if current:
        tokens.append("".join(current))
    return tokens


def _matches_query(card: dict[str, Any], terms: list[SearchTerm], column_names_by_id: dict[str, str], rg_cache: dict[tuple[str, tuple[str, ...]], bool]) -> bool:
    for term in terms:
        matched = _matches_term(card, term, column_names_by_id, rg_cache)
        if term.negative and matched:
            return False
        if not term.negative and not matched:
            return False
    return True


def _matches_term(card: dict[str, Any], term: SearchTerm, column_names_by_id: dict[str, str], rg_cache: dict[tuple[str, tuple[str, ...]], bool]) -> bool:
    if term.field == "has":
        return _matches_has(card, term.value)
    if term.field == "file":
        return _matches_file(card, term.value)
    if term.field == "commit":
        return _matches_commit(card, term.value)
    if term.field == "contains":
        return _matches_contains(card, term.value, column_names_by_id, rg_cache)
    return any(term.value in _normalize(value) for value in _search_values(card, column_names_by_id, term.field))


def _search_values(card: dict[str, Any], column_names_by_id: dict[str, str], field: str | None) -> list[Any]:
    sessions = []
    for entry in card.get("session_history") or []:
        sessions.extend(["session", entry.get("session_id", ""), entry.get("system", ""), entry.get("action", ""), entry.get("outcome") or "", entry.get("timestamp", "")])

    metadata_values = _flatten_metadata(card.get("metadata") or {})
    scoped = {
        "external_id": [card.get("external_id", "")],
        "title": [card.get("title", "")],
        "body": [card.get("body", "")],
        "label": card.get("labels", []),
        "priority": [card.get("priority", "")],
        "column": [column_names_by_id.get(card.get("column_id"), card.get("column_id", ""))],
        "id": [card.get("id", "")],
        "metadata": metadata_values,
        "session": sessions,
    }
    if field in scoped:
        return scoped[field]
    return [
        card.get("id", ""),
        card.get("external_id", ""),
        card.get("title", ""),
        card.get("body", ""),
        card.get("priority", ""),
        column_names_by_id.get(card.get("column_id"), card.get("column_id", "")),
        *card.get("labels", []),
        *metadata_values,
        *sessions,
    ]


def _matches_has(card: dict[str, Any], value: str) -> bool:
    if value in {"file", "files", "edited_file", "edited_files"}:
        return _has_file(card)
    if value in {"commit", "commits", "git_commit", "git_commits"}:
        return _has_commit(card)
    if value in {"session", "sessions"}:
        return bool(card.get("session_history"))
    return False


def _matches_file(card: dict[str, Any], value: str) -> bool:
    body = card.get("body", "")
    if not _has_file(card):
        return False
    return value in _normalize(body) or any(value in _normalize(item) for item in _metadata_values(card, "edited_files"))


def _matches_commit(card: dict[str, Any], value: str) -> bool:
    body = card.get("body", "")
    if not _has_commit(card):
        return False
    return value in _normalize(body) or any(value in _normalize(item) for item in _metadata_values(card, "git_commits"))


def _matches_contains(card: dict[str, Any], value: str, column_names_by_id: dict[str, str], rg_cache: dict[tuple[str, tuple[str, ...]], bool]) -> bool:
    if any(value in _normalize(candidate) for candidate in _search_values(card, column_names_by_id, None)):
        return True
    if _matches_file(card, value) or _matches_commit(card, value):
        return True

    paths = tuple(str(path) for path in _existing_edited_file_paths(card))
    if not paths:
        return False

    key = (value, paths)
    if key not in rg_cache:
        rg_cache[key] = _rg_contains(value, paths)
    return rg_cache[key]


def _rg_contains(value: str, paths: tuple[str, ...]) -> bool:
    try:
        result = subprocess.run(
            ["rg", "--fixed-strings", "--ignore-case", "--quiet", "--", value, *paths],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _existing_edited_file_paths(card: dict[str, Any]) -> list[Path]:
    projects = [Path(value).expanduser() for value in _metadata_values(card, "projects")]
    files = _metadata_values(card, "edited_files")
    paths: list[Path] = []
    seen: set[str] = set()

    for file_value in files:
        file_path = Path(file_value).expanduser()
        candidates = [file_path] if file_path.is_absolute() else [project / file_path for project in projects]
        if not candidates and not file_path.is_absolute():
            candidates = [file_path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if not resolved.is_file():
                continue
            as_string = str(resolved)
            if as_string in seen:
                continue
            seen.add(as_string)
            paths.append(resolved)
    return paths


def _metadata_values(card: dict[str, Any], key: str) -> list[str]:
    structured = card.get("metadata") or {}
    if isinstance(structured, dict):
        direct = structured.get(key)
        if isinstance(direct, list):
            return [str(value) for value in direct if value not in (None, "")]
        if direct not in (None, ""):
            return [str(direct)]

    prefix = f"{key}:"
    values: list[str] = []
    for line in str(card.get("body", "")).splitlines():
        if not line.lower().startswith(prefix):
            continue
        raw = line[len(prefix):].strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [raw]
        if isinstance(parsed, list):
            values.extend(value for value in parsed if isinstance(value, str) and value)
        elif isinstance(parsed, str) and parsed:
            values.append(parsed)
    return values


def _flatten_metadata(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            values.append(str(key))
            values.extend(_flatten_metadata(nested))
    elif isinstance(value, list):
        for item in value:
            values.extend(_flatten_metadata(item))
    elif value not in (None, ""):
        values.append(str(value))
    return values


def _has_file(card: dict[str, Any]) -> bool:
    body = card.get("body", "")
    normalized = _normalize(body)
    return bool(_metadata_values(card, "edited_files")) or "edited_files:" in normalized or bool(FILE_RE.search(body))


def _has_commit(card: dict[str, Any]) -> bool:
    body = card.get("body", "")
    normalized = _normalize(body)
    return bool(_metadata_values(card, "git_commits")) or "git_commits:" in normalized or bool(COMMIT_RE.search(body))


def _add_ancestors(card_id: str, visible_ids: set[str], cards_by_id: dict[str, dict[str, Any]]) -> None:
    current = cards_by_id.get(card_id)
    while current and current.get("parent_id"):
        parent = cards_by_id.get(current["parent_id"])
        if not parent or parent["id"] in visible_ids:
            return
        visible_ids.add(parent["id"])
        current = parent


def _add_descendants(card_id: str, visible_ids: set[str], children_by_parent: dict[str, list[dict[str, Any]]]) -> None:
    for child in children_by_parent.get(card_id, []):
        if child["id"] in visible_ids:
            continue
        visible_ids.add(child["id"])
        _add_descendants(child["id"], visible_ids, children_by_parent)


def _group_children(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        parent_id = card.get("parent_id")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(card)
    return children_by_parent


def _card_sort_key(card: dict[str, Any]) -> tuple[str, int]:
    return (card.get("column_id", ""), card.get("position", 0))


def _normalize(value: Any) -> str:
    return str(value or "").lower()
