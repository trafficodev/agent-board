"""Edge persistence. Arbitrary typed connections between two cards, stored
per-board alongside cards/columns. `type` is a free-form string (e.g.
"blocked_by", "blocks", "duplicates", "relates_to") — no fixed vocabulary,
same open-ended approach labels already take."""

import fcntl
import json
from pathlib import Path

from models import CreateEdge, Edge, Event
from paths import edges_path, events_path

from board_store import get_board


def _edges_path(board_id: str) -> Path:
    return edges_path(board_id)


def read_edges(board_id: str) -> list[Edge]:
    p = _edges_path(board_id)
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return [Edge.model_validate(e) for e in data]


def write_edges(board_id: str, edges: list[Edge]) -> None:
    p = _edges_path(board_id)
    p.write_text(json.dumps([e.model_dump(mode="json") for e in edges], indent=2, default=str))


def _locked_write(board_id: str, edges: list[Edge], event: Event | None = None) -> None:
    """Mirrors card_store._locked_write — same per-board lock file, so edge
    writes and card writes (e.g. delete_card's edge cleanup) never race."""
    from card_store import _with_lock

    lock_path = _with_lock(board_id)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            write_edges(board_id, edges)
            if event:
                ep = events_path(board_id)
                with open(ep, "a") as f:
                    f.write(event.model_dump_json() + "\n")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _reorder_dependents(board_id: str) -> None:
    """A dependency-ordered column reads its order off this graph, so every
    edge write has to let the card store re-derive it. Called after the edge
    file is written and its lock released — the card store takes the same
    per-board lock."""
    from card_store import reindex_dag_columns

    reindex_dag_columns(board_id)


def list_edges(board_id: str, card_id: str | None = None, type: str | None = None) -> list[Edge]:
    edges = read_edges(board_id)
    if card_id:
        edges = [e for e in edges if e.from_card_id == card_id or e.to_card_id == card_id]
    if type:
        edges = [e for e in edges if e.type == type]
    return edges


def get_edge(board_id: str, edge_id: str) -> Edge | None:
    return next((e for e in read_edges(board_id) if e.id == edge_id), None)


def create_edge(board_id: str, data: CreateEdge) -> Edge | None:
    from card_store import get_card

    if not get_board(board_id):
        return None
    if not get_card(board_id, data.from_card_id) or not get_card(board_id, data.to_card_id):
        return None
    edges = read_edges(board_id)
    edge = Edge(
        board_id=board_id, from_card_id=data.from_card_id, to_card_id=data.to_card_id,
        type=data.type, label=data.label,
    )
    edges.append(edge)
    _locked_write(board_id, edges, Event(
        type="edge_created", detail=f"{data.type}: {data.from_card_id[:8]}… → {data.to_card_id[:8]}…",
    ))
    _reorder_dependents(board_id)
    return edge


def delete_edge(board_id: str, edge_id: str) -> bool:
    edges = read_edges(board_id)
    edge = next((e for e in edges if e.id == edge_id), None)
    if not edge:
        return False
    edges = [e for e in edges if e.id != edge_id]
    _locked_write(board_id, edges, Event(type="edge_deleted", detail=edge_id))
    _reorder_dependents(board_id)
    return True
