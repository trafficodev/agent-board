"""Dependency ordering for the cards of one column.

Blocking edges are what make a backlog's order meaningful: whatever blocks
work has to sit above the work waiting on it, or the top of the column is not
the thing to pick up next. The sort is stable — cards the graph says nothing
about keep the order a human or an agent gave them."""

import heapq

from models import Card, Edge

# The only two edge types that express "must come first". Every other type
# (duplicates, relates_to, …) carries no ordering meaning.
_BLOCKS = "blocks"  # from blocks to      -> from first
_BLOCKED_BY = "blocked_by"  # from blocked by to -> to first


def _normalized_type(edge_type: str) -> str:
    return (edge_type or "").strip().lower().replace("-", "_")


def dependency_pairs(edges: list[Edge], card_ids) -> set[tuple[str, str]]:
    """`(before, after)` pairs for blocking edges with both ends inside
    `card_ids`. Edges reaching out of the column constrain nothing within it."""
    known = set(card_ids)
    pairs: set[tuple[str, str]] = set()
    for edge in edges:
        kind = _normalized_type(edge.type)
        if kind == _BLOCKS:
            before, after = edge.from_card_id, edge.to_card_id
        elif kind == _BLOCKED_BY:
            before, after = edge.to_card_id, edge.from_card_id
        else:
            continue
        if before == after or before not in known or after not in known:
            continue
        pairs.add((before, after))
    return pairs


def dag_sort(cards: list[Card], edges: list[Edge]) -> list[Card]:
    """Kahn's algorithm over the blocking edges, tie-broken by the incoming
    order so nothing moves except what a dependency forces. Cards caught in a
    cycle keep their incoming order and land after everything resolvable —
    an unsatisfiable graph reorders the column, it never drops a card."""
    rank = {card.id: i for i, card in enumerate(cards)}
    pairs = dependency_pairs(edges, rank)
    if not pairs:
        return list(cards)

    successors: dict[str, list[str]] = {card.id: [] for card in cards}
    indegree: dict[str, int] = {card.id: 0 for card in cards}
    for before, after in pairs:
        successors[before].append(after)
        indegree[after] += 1

    ready = [rank[card_id] for card_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[Card] = []
    while ready:
        card = cards[heapq.heappop(ready)]
        ordered.append(card)
        for successor in successors[card.id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(ready, rank[successor])

    if len(ordered) < len(cards):
        placed = {card.id for card in ordered}
        ordered.extend(card for card in cards if card.id not in placed)
    return ordered
