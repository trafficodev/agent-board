from __future__ import annotations

import operator
import re
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


FIELD_ALIASES = {
    "labels": "label",
    "type": "label",
    "files": "file",
    "edited_file": "file",
    "edited_files": "file",
    "commits": "commit",
    "git_commit": "commit",
    "git_commits": "commit",
    "worktrees": "worktree",
    "worktree_path": "worktree",
    "worktree_paths": "worktree",
    "content": "contains",
    "external": "external_id",
    "lifecycle": "catalog_lifecycle",
    "acceptance": "acceptance_criteria",
    "owner": "ownership",
    "decision": "decisions",
}

FILE_RE = re.compile(r"(^|\s|`)\/?[\w.-]+\/[\w./-]+")
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)

# Card fields backed by a `metadata.<key>` list (or a `<key>: [...]` line in the
# body) plus an optional freeform-text detector regex. `file`/`commit`/`worktree`
# search terms, `has:<field>`, and the `contains:` fallback all read this table
# instead of hand-rolling a matcher per field.
_TRACKED_LIST_FIELDS: dict[str, tuple[str, "re.Pattern | None"]] = {
    "file": ("edited_files", FILE_RE),
    "commit": ("git_commits", COMMIT_RE),
    "worktree": ("worktrees", None),  # no reliable freeform-text signal; structured metadata only
}

_PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


@dataclass(frozen=True)
class Comparison:
    """One bound of a numeric/date comparison, e.g. `>` against a parsed
    epoch-seconds (date fields) or plain float (numeric fields) threshold."""
    op: str  # one of _COMPARATOR_OPS keys: ">", "<", ">=", "<=", "="
    value: float


@dataclass(frozen=True)
class SearchTerm:
    negative: bool
    field: str | None
    value: str
    regex: "re.Pattern | None" = None
    comparisons: "tuple[Comparison, ...]" = ()


@dataclass(frozen=True)
class OrGroup:
    """A parenthesized group: `(a OR b OR c)`. Matches if ANY branch matches.
    Each branch is an AND'd sequence of SearchNode (term or nested group),
    mirroring the semantics of the top-level query itself."""
    negative: bool
    branches: "tuple[tuple[SearchNode, ...], ...]"


# A query is an AND'd sequence of nodes; a node is either a leaf term or a
# parenthesized OR-group of further AND'd sequences. Recursion bottoms out at
# SearchTerm leaves, which `_matches_term` evaluates unchanged.
SearchNode = SearchTerm | OrGroup


def _parse_date_value(raw: str) -> float:
    """Parses an ISO date/datetime into epoch seconds. A value with no
    timezone (e.g. a bare `2026-01-01` operand typed into a query, as
    opposed to a stored `...Z` card timestamp) is treated as UTC — the two
    must compare consistently regardless of the host's local timezone."""
    normalized = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


_KIND_PARSERS: dict[str, Callable[[str], float]] = {
    "date": _parse_date_value,
    "number": float,
}

# Single-source-of-truth registration for comparable fields: (kind, getter).
# `kind` selects the parser above for both the query operand and the card's
# actual value; adding another comparable field is one new entry here.
_COMPARABLE_FIELDS: dict[str, tuple[str, Callable[[dict[str, Any]], "float | None"]]] = {
    "created": ("date", lambda card: _parse_date_value(str(card["created_at"])) if card.get("created_at") else None),
    "updated": ("date", lambda card: _parse_date_value(str(card["updated_at"])) if card.get("updated_at") else None),
    "position": ("number", lambda card: float(card["position"]) if card.get("position") is not None else None),
    "sessions": ("number", lambda card: float(len(card.get("session_history") or []))),
}

_COMPARATOR_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "=": operator.eq,
}

# Longest-prefix-first: ">=" must be checked before ">" or its operand would
# wrongly start with "=".
_COMPARATOR_PREFIXES = (">=", "<=", ">", "<", "=")
_RANGE_SEP = ".."


def search_cards(
    cards: list[dict[str, Any]], columns: list[dict[str, Any]], query: str = "",
    priority: str | None = None, label: str | None = None, edges: list[dict[str, Any]] | None = None,
    sort: str = "board",
) -> list[dict[str, Any]]:
    results, _roles = search_cards_with_roles(
        cards, columns, query=query, priority=priority, label=label, edges=edges, sort=sort
    )
    return results


def search_cards_with_roles(
    cards: list[dict[str, Any]], columns: list[dict[str, Any]], query: str = "",
    priority: str | None = None, label: str | None = None, edges: list[dict[str, Any]] | None = None,
    sort: str = "board",
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    terms = parse_search_query(query)
    column_names_by_id = {column["id"]: column.get("name", column["id"]) for column in columns}
    cards_by_id = {card["id"]: card for card in cards}
    children_by_parent = _group_children(cards)
    edges_by_card = _group_edges_by_card(edges or [])
    rg_cache: dict[tuple[str, bool, tuple[str, ...]], bool] = {}

    direct_ids: set[str] = set()
    for card in cards:
        if priority and card.get("priority") != priority:
            continue
        if label and label not in card.get("labels", []):
            continue
        if _matches_query(card, terms, column_names_by_id, rg_cache, edges_by_card, cards_by_id):
            direct_ids.add(card["id"])

    visible_ids = set(direct_ids)
    roles_by_id: dict[str, set[str]] = {
        card_id: {"direct_match"} for card_id in direct_ids
    }
    for card_id in direct_ids:
        _add_ancestors(card_id, visible_ids, cards_by_id, roles_by_id)
        _add_descendants(card_id, visible_ids, children_by_parent, roles_by_id)

    ordered = sort_cards([card for card in cards if card["id"] in visible_ids], sort)
    role_order = {"direct_match": 0, "ancestor": 1, "descendant": 2}
    roles = {
        card_id: sorted(card_roles, key=role_order.__getitem__)
        for card_id, card_roles in roles_by_id.items()
    }
    return ordered, roles


def sort_cards(
    cards: list[dict[str, Any]], sort: str = "board", *, tie_breaker: bool = False
) -> list[dict[str, Any]]:
    key = card_sort_key if tie_breaker else _sort_key
    return sorted(cards, key=lambda card: key(card, sort))


def card_sort_key(card: dict[str, Any], sort: str = "board") -> tuple[Any, ...]:
    return (*_sort_key(card, sort), card.get("id", ""))


_SHORTLIST_CAP = 40
_BODY_SNIPPET_CHARS = 240


def relevant_candidates(
    cards: list[dict[str, Any]], columns: list[dict[str, Any]], query: str,
    priority: str | None = None, label: str | None = None, edges: list[dict[str, Any]] | None = None,
    max_candidates: int = _SHORTLIST_CAP,
) -> dict[str, Any]:
    """Keyword-relevance shortlist: the top-N cards for a query, in a
    COMPACT projection (id/title/body-snippet/labels/priority/column),
    capped at `max_candidates`. Uses the same weighted keyword-relevance
    scoring `search_cards` is built on. This is a building block for
    semantic/AI ranking (an embedder sends this shortlist to a model, since
    agent-board itself has no AI provider) -- it is not itself a semantic
    ranker and returns candidates, not full cards."""
    ranked_cards, _scores, column_names_by_id, error = ranked_relevant_cards(
        cards,
        columns,
        query,
        priority=priority,
        label=label,
        edges=edges,
        max_candidates=max_candidates,
    )
    if error:
        return {"candidates": [], "error": "empty_query"}

    candidates = [
        _shortlist_candidate(card, column_names_by_id)
        for card in ranked_cards
    ]
    return {"candidates": candidates, "error": None}


def ranked_relevant_cards(
    cards: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    query: str,
    priority: str | None = None,
    label: str | None = None,
    edges: list[dict[str, Any]] | None = None,
    max_candidates: int = _SHORTLIST_CAP,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, str], str | None]:
    terms = parse_search_query(query)
    column_names_by_id = {
        column["id"]: column.get("name", column["id"]) for column in columns
    }
    if not terms:
        return [], {}, column_names_by_id, "empty_query"

    cards_by_id = {card["id"]: card for card in cards}
    edges_by_card = _group_edges_by_card(edges or [])
    ranked: list[tuple[int, dict[str, Any]]] = []

    for card in cards:
        if priority and card.get("priority") != priority:
            continue
        if label and label not in card.get("labels", []):
            continue
        score = _ai_relevance_score(card, terms, column_names_by_id, edges_by_card, cards_by_id)
        if score > 0:
            ranked.append((score, card))

    ranked.sort(key=lambda item: (-item[0], *_card_sort_key(item[1]), item[1]["id"]))
    capped = ranked[:max_candidates]
    return (
        [card for _score, card in capped],
        {card["id"]: score for score, card in capped},
        column_names_by_id,
        None,
    )


def _shortlist_candidate(card: dict[str, Any], column_names_by_id: dict[str, str]) -> dict[str, Any]:
    body = str(card.get("body") or "")
    snippet = body[:_BODY_SNIPPET_CHARS] + ("…" if len(body) > _BODY_SNIPPET_CHARS else "")
    return {
        "id": card.get("id", ""),
        "title": card.get("title", ""),
        "body_snippet": snippet,
        "labels": list(card.get("labels", []) or []),
        "priority": card.get("priority", ""),
        "column": column_names_by_id.get(card.get("column_id"), card.get("column_id", "")),
    }


_REGEX_VALUE_RE = re.compile(r"^/(.+)/([a-zA-Z]*)$")


def parse_search_query(query: str) -> list[SearchNode]:
    """Grep-style `/pattern/flags` value syntax works on any field, including
    the unscoped (search-everything) case and `contains:` — e.g.
    `title:/^Fix.*bug$/i`, `/TODO|FIXME/`, `contains:/error: \\d+/`. Every
    other value is still a plain case-insensitive substring match, unchanged.

    Grouping: `(a OR b)` — OR is only recognized strictly inside parens;
    implicit AND joins top-level terms/groups and terms within a group.
    A bare `OR` token outside any parens is NOT an operator — it parses as
    an ordinary literal term (documented, deliberate: no precedence guessing
    without explicit grouping). Parens are structural only when unquoted;
    quote a value that needs a literal `(`/`)` (or a regex containing one).

    Date/numeric comparisons on registered fields (`created`, `updated`,
    `position`, `sessions`): `field:>value`, `field:<value`, `field:>=value`,
    `field:<=value`, `field:a..b` (inclusive range), or a bare `field:value`
    for equality. Regex value syntax is rejected on these fields.

    In the simple case (no grouping), the returned list is exactly the flat
    `list[SearchTerm]` this function has always returned.
    """
    tokens = _tokenize(query)
    nodes, _ = _parse_and_sequence(tokens, 0, in_group=False)
    return nodes


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for char in query.strip():
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            flush()
            continue
        if char in "()" and not quoted:
            flush()
            tokens.append(char)
            continue
        current.append(char)

    flush()
    return tokens


def _parse_and_sequence(tokens: list[str], index: int, *, in_group: bool) -> tuple[list[SearchNode], int]:
    """Parses an implicit-AND run of factors. Inside a group, stops at `)` or
    an `OR` token (leaving them for the caller); at the top level neither is
    special, so a stray `)` is dropped and a bare `OR` is parsed as a term."""
    nodes: list[SearchNode] = []
    while index < len(tokens):
        token = tokens[index]
        if in_group and (token == ")" or token.upper() == "OR"):
            break
        node, index = _parse_factor(tokens, index, in_group=in_group)
        if node is not None:
            nodes.append(node)
    return nodes, index


def _parse_or_group(tokens: list[str], index: int) -> tuple[tuple[tuple[SearchNode, ...], ...], int]:
    """Parses OR-separated AND-sequences until `)` or end of tokens."""
    branches: list[tuple[SearchNode, ...]] = []
    nodes, index = _parse_and_sequence(tokens, index, in_group=True)
    branches.append(tuple(nodes))
    while index < len(tokens) and tokens[index].upper() == "OR":
        index += 1
        nodes, index = _parse_and_sequence(tokens, index, in_group=True)
        branches.append(tuple(nodes))
    return tuple(branches), index


def _parse_factor(tokens: list[str], index: int, *, in_group: bool) -> tuple["SearchNode | None", int]:
    token = tokens[index]
    negative = token == "-"
    if negative:
        index += 1
        if index >= len(tokens):
            return None, index
        token = tokens[index]

    if token == "(":
        index += 1
        branches, index = _parse_or_group(tokens, index)
        if index < len(tokens) and tokens[index] == ")":
            index += 1
        return OrGroup(negative=negative, branches=branches), index

    if token == ")":
        # Stray close-paren with no opener (only reachable at the top level,
        # since `_parse_and_sequence` intercepts it inside a group): drop it.
        return None, index + 1

    return _parse_term_token(token, negative), index + 1


def _parse_term_token(token: str, negative: bool) -> "SearchTerm | None":
    if token.startswith("-") and len(token) > 1:
        negative = True
        token = token[1:]

    field = None
    value = token
    colon_index = token.find(":")
    if colon_index > 0:
        raw_field = _normalize(token[:colon_index])
        field = FIELD_ALIASES.get(raw_field, raw_field)
        value = token[colon_index + 1:]

    if field in _COMPARABLE_FIELDS:
        return _parse_comparable_term(field, value, negative)

    regex_match = _REGEX_VALUE_RE.match(value)
    if regex_match:
        pattern, flags = regex_match.groups()
        re_flags = re.IGNORECASE if "i" in flags.lower() else 0
        try:
            compiled = re.compile(pattern, re_flags)
        except re.error as exc:
            raise ValueError(f"invalid regex /{pattern}/: {exc}") from exc
        return SearchTerm(negative=negative, field=field, value=pattern, regex=compiled)

    normalized = _normalize(value)
    if not normalized:
        return None
    return SearchTerm(negative=negative, field=field, value=normalized)


def _parse_comparable_term(field: str, value: str, negative: bool) -> SearchTerm:
    if _REGEX_VALUE_RE.match(value):
        raise ValueError(f"regex value syntax is not supported on comparable field '{field}': {value!r}")

    _kind, _getter = _COMPARABLE_FIELDS[field]
    parse_operand = _KIND_PARSERS[_kind]

    if _RANGE_SEP in value:
        start, _, end = value.partition(_RANGE_SEP)
        comparisons = (
            Comparison(">=", parse_operand(start)),
            Comparison("<=", parse_operand(end)),
        )
        return SearchTerm(negative=negative, field=field, value=value, comparisons=comparisons)

    for prefix in _COMPARATOR_PREFIXES:
        if value.startswith(prefix):
            operand = value[len(prefix):]
            comparisons = (Comparison(prefix, parse_operand(operand)),)
            return SearchTerm(negative=negative, field=field, value=value, comparisons=comparisons)

    comparisons = (Comparison("=", parse_operand(value)),)
    return SearchTerm(negative=negative, field=field, value=value, comparisons=comparisons)


def _matches_query(
    card: dict[str, Any], nodes: "list[SearchNode] | tuple[SearchNode, ...]", column_names_by_id: dict[str, str],
    rg_cache: dict[tuple[str, bool, tuple[str, ...]], bool],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> bool:
    """AND-matches a sequence of nodes. Used both for the top-level query and,
    recursively, for each AND'd branch inside an `OrGroup` (see
    `_evaluate_node`) — the semantics are identical either way."""
    for node in nodes:
        matched = _evaluate_node(card, node, column_names_by_id, rg_cache, edges_by_card, cards_by_id)
        if node.negative and matched:
            return False
        if not node.negative and not matched:
            return False
    return True


def _evaluate_node(
    card: dict[str, Any], node: SearchNode, column_names_by_id: dict[str, str],
    rg_cache: dict[tuple[str, bool, tuple[str, ...]], bool],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> bool:
    if isinstance(node, OrGroup):
        return any(
            _matches_query(card, branch, column_names_by_id, rg_cache, edges_by_card, cards_by_id)
            for branch in node.branches
        )
    return _matches_term(card, node, column_names_by_id, rg_cache, edges_by_card, cards_by_id)


def _group_edges_by_card(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        grouped.setdefault(edge["from_card_id"], []).append(edge)
        grouped.setdefault(edge["to_card_id"], []).append(edge)
    return grouped


def _matches_term(
    card: dict[str, Any], term: SearchTerm, column_names_by_id: dict[str, str],
    rg_cache: dict[tuple[str, bool, tuple[str, ...]], bool],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> bool:
    if term.comparisons:
        return _matches_comparison(card, term)
    if term.field == "has":
        return _matches_has(card, term.value, edges_by_card)
    if term.field in _TRACKED_LIST_FIELDS:
        return _matches_tracked(card, term.field, term.value)
    if term.field == "contains":
        return _matches_contains(card, term.value, term.regex, column_names_by_id, rg_cache, edges_by_card, cards_by_id)
    values = _search_values(card, column_names_by_id, term.field, edges_by_card, cards_by_id)
    if term.regex:
        return any(term.regex.search(str(value)) for value in values)
    return any(term.value in _normalize(value) for value in values)


def _matches_comparison(card: dict[str, Any], term: SearchTerm) -> bool:
    _kind, getter = _COMPARABLE_FIELDS[term.field]
    actual = getter(card)
    if actual is None:
        return False
    return all(_COMPARATOR_OPS[comparison.op](actual, comparison.value) for comparison in term.comparisons)


def _ai_relevance_score(
    card: dict[str, Any], nodes: list[SearchNode], column_names_by_id: dict[str, str],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> int:
    score = 0
    for node in nodes:
        if node.negative:
            if _evaluate_node(card, node, column_names_by_id, {}, edges_by_card, cards_by_id):
                return 0
            continue
        score += _weighted_node_score(card, node, column_names_by_id, edges_by_card, cards_by_id)
    return score


def _weighted_node_score(
    card: dict[str, Any], node: SearchNode, column_names_by_id: dict[str, str],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> int:
    if isinstance(node, OrGroup):
        return 8 if _evaluate_node(card, node, column_names_by_id, {}, edges_by_card, cards_by_id) else 0
    return _weighted_term_score(card, node, column_names_by_id, edges_by_card, cards_by_id)


def _weighted_term_score(
    card: dict[str, Any], term: SearchTerm, column_names_by_id: dict[str, str],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> int:
    if term.field:
        return 8 if _matches_term(card, term, column_names_by_id, {}, edges_by_card, cards_by_id) else 0

    weights = {
        "title": 12,
        "label": 8,
        "body": 5,
        "metadata": 4,
        "semantics": 6,
        "session": 4,
        "edge": 4,
        "priority": 3,
        "column": 3,
        "external_id": 2,
        "id": 1,
    }
    total = 0
    for field, weight in weights.items():
        values = _search_values(card, column_names_by_id, field, edges_by_card, cards_by_id)
        if term.regex:
            matched = any(term.regex.search(str(value)) for value in values)
        else:
            matched = any(term.value in _normalize(value) for value in values)
        if matched:
            total += weight
    return total


def _search_values(
    card: dict[str, Any], column_names_by_id: dict[str, str], field: str | None,
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> list[Any]:
    sessions = []
    for entry in card.get("session_history") or []:
        sessions.extend(["session", entry.get("session_id", ""), entry.get("system", ""), entry.get("action", ""), entry.get("outcome") or "", entry.get("timestamp", "")])

    edge_values = _edge_search_values(card, edges_by_card, cards_by_id)

    metadata_values = _flatten_metadata(card.get("metadata") or {})
    semantics = card.get("semantics") or {}
    semantics_values = _flatten_metadata(semantics)
    scoped = {
        "external_id": [card.get("external_id", "")],
        "title": [card.get("title", "")],
        "body": [card.get("body", "")],
        "label": card.get("labels", []),
        "priority": [card.get("priority", "")],
        "column": [column_names_by_id.get(card.get("column_id"), card.get("column_id", ""))],
        "id": [card.get("id", "")],
        "metadata": metadata_values,
        "semantics": semantics_values,
        "kind": [semantics.get("kind", "")],
        "catalog_lifecycle": [semantics.get("catalog_lifecycle", "")],
        "outcome": [semantics.get("outcome", "")],
        "acceptance_criteria": semantics.get("acceptance_criteria", []),
        "evidence": _flatten_metadata(semantics.get("evidence", [])),
        "ownership": _flatten_metadata(semantics.get("ownership", {})),
        "decisions": _flatten_metadata(semantics.get("decisions", [])),
        "session": sessions,
        "edge": edge_values,
        "edge_type": [e.get("type", "") for e in edges_by_card.get(card.get("id", ""), [])],
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
        *semantics_values,
        *sessions,
        *edge_values,
    ]


def _edge_search_values(
    card: dict[str, Any], edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """Every edge touching this card, as greppable strings: the edge type,
    its label, and the OTHER card's title (in the meaningful direction) —
    so `blocked by install` finds a card whose blocked_by edge points at a
    card titled "...install...", without needing to know its id."""
    values: list[str] = []
    card_id = card.get("id", "")
    for edge in edges_by_card.get(card_id, []):
        values.append(edge.get("type", ""))
        if edge.get("label"):
            values.append(edge["label"])
        other_id = edge["to_card_id"] if edge["from_card_id"] == card_id else edge["from_card_id"]
        other = cards_by_id.get(other_id)
        if other:
            values.append(other.get("title", ""))
    return values


_HAS_ALIASES = {
    "file": "file", "files": "file", "edited_file": "file", "edited_files": "file",
    "commit": "commit", "commits": "commit", "git_commit": "commit", "git_commits": "commit",
    "worktree": "worktree", "worktrees": "worktree", "worktree_path": "worktree", "worktree_paths": "worktree",
}


def _matches_has(card: dict[str, Any], value: str, edges_by_card: dict[str, list[dict[str, Any]]]) -> bool:
    field = _HAS_ALIASES.get(value)
    if field:
        return _has_tracked(card, field)
    if value in {"session", "sessions"}:
        return bool(card.get("session_history"))
    if value in {"edge", "edges"}:
        return bool(edges_by_card.get(card.get("id", "")))
    semantics = card.get("semantics") or {}
    semantic_field = FIELD_ALIASES.get(value, value)
    if semantic_field in {
        "kind", "catalog_lifecycle", "outcome", "acceptance_criteria",
        "exclusions", "owning_surface", "evidence", "ownership", "decisions",
    }:
        return bool(semantics.get(semantic_field))
    return False


def _has_tracked(card: dict[str, Any], field: str) -> bool:
    key, detector = _TRACKED_LIST_FIELDS[field]
    if _metadata_values(card, key):
        return True
    body = card.get("body", "")
    if f"{key}:" in _normalize(body):
        return True
    return bool(detector and detector.search(body))


def _matches_tracked(card: dict[str, Any], field: str, value: str) -> bool:
    if not _has_tracked(card, field):
        return False
    key, _ = _TRACKED_LIST_FIELDS[field]
    body = card.get("body", "")
    return value in _normalize(body) or any(value in _normalize(item) for item in _metadata_values(card, key))


def _matches_contains(
    card: dict[str, Any], value: str, regex: "re.Pattern | None", column_names_by_id: dict[str, str],
    rg_cache: dict[tuple[str, bool, tuple[str, ...]], bool],
    edges_by_card: dict[str, list[dict[str, Any]]], cards_by_id: dict[str, dict[str, Any]],
) -> bool:
    values = _search_values(card, column_names_by_id, None, edges_by_card, cards_by_id)
    if regex:
        if any(regex.search(str(candidate)) for candidate in values):
            return True
    elif any(value in _normalize(candidate) for candidate in values):
        return True
    if not regex and any(_matches_tracked(card, field, value) for field in _TRACKED_LIST_FIELDS):
        return True

    paths = tuple(str(path) for path in _existing_edited_file_paths(card))
    if not paths:
        return False

    pattern = regex.pattern if regex else value
    key = (pattern, bool(regex), paths)
    if key not in rg_cache:
        rg_cache[key] = _rg_contains(pattern, paths, is_regex=bool(regex))
    return rg_cache[key]


def _rg_contains(value: str, paths: tuple[str, ...], *, is_regex: bool = False) -> bool:
    argv = ["rg"] if is_regex else ["rg", "--fixed-strings"]
    argv += ["--ignore-case", "--quiet", "--", value, *paths]
    try:
        result = subprocess.run(
            argv,
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


def _add_ancestors(
    card_id: str,
    visible_ids: set[str],
    cards_by_id: dict[str, dict[str, Any]],
    roles_by_id: dict[str, set[str]],
) -> None:
    current = cards_by_id.get(card_id)
    visited = {card_id}
    while current and current.get("parent_id"):
        parent = cards_by_id.get(current["parent_id"])
        if not parent or parent["id"] in visited:
            return
        visited.add(parent["id"])
        visible_ids.add(parent["id"])
        roles_by_id.setdefault(parent["id"], set()).add("ancestor")
        current = parent


def _add_descendants(
    card_id: str,
    visible_ids: set[str],
    children_by_parent: dict[str, list[dict[str, Any]]],
    roles_by_id: dict[str, set[str]],
    visited: set[str] | None = None,
) -> None:
    visited = set() if visited is None else visited
    if card_id in visited:
        return
    visited.add(card_id)
    for child in children_by_parent.get(card_id, []):
        visible_ids.add(child["id"])
        roles_by_id.setdefault(child["id"], set()).add("descendant")
        _add_descendants(child["id"], visible_ids, children_by_parent, roles_by_id, visited)


def _group_children(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        parent_id = card.get("parent_id")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(card)
    return children_by_parent


def _card_sort_key(card: dict[str, Any]) -> tuple[str, int]:
    return (card.get("column_id", ""), card.get("position", 0))


def _sort_key(card: dict[str, Any], sort: str) -> tuple[Any, ...]:
    board = _card_sort_key(card)
    if sort == "updated_desc":
        return (_reverse_timestamp(card.get("updated_at")), *board)
    if sort == "created_desc":
        return (_reverse_timestamp(card.get("created_at")), *board)
    if sort == "priority_desc":
        return (_PRIORITY_RANK.get(card.get("priority"), 99), *board)
    if sort == "title_asc":
        return (_normalize(card.get("title", "")), *board)
    if sort == "sessions_desc":
        return (-(len(card.get("session_history") or [])), *board)
    return board


def _reverse_timestamp(value: Any) -> float:
    if not value:
        return 0
    try:
        return -_parse_date_value(str(value))
    except ValueError:
        return 0


def _normalize(value: Any) -> str:
    return str(value or "").lower()
