"""Controlled directional relationships between cards."""

from . import db
from .models import CreateEdge, Edge, Event

from .board_store import get_board


class EdgeError(Exception):
    """A ``create_edge`` precondition failed.

    ``str(self)`` is a caller-facing reason. Each subclass names one distinct
    way the request is refused so callers can map it to an accurate status
    instead of collapsing every rejection into "invalid id".
    """


class UnknownEndpoint(EdgeError):
    """The board, or one of the two cards, does not exist."""


class SelfLoop(EdgeError):
    """The from- and to-card are the same card."""


class DuplicateEdge(EdgeError):
    """An edge of this type already connects these two cards."""


class MissingSemanticKind(EdgeError):
    """One or both endpoints lack the semantic kind a link needs."""


class InvalidDirection(EdgeError):
    """This edge type is not allowed between these two kinds/direction."""


class ParentRelationshipRequired(EdgeError):
    """A ``defines``/``parent_of`` edge must follow the card parentage."""


class DependencyCycle(EdgeError):
    """A ``depends_on`` edge would close a cycle in the dependency graph."""


def direction_is_valid(edge_type: str, source, target) -> bool:
    source_kind = source.semantics.kind
    target_kind = target.semantics.kind
    if edge_type == "depends_on":
        return bool(source_kind and target_kind)
    if not source_kind or not target_kind:
        return False
    if edge_type == "defines":
        return (source_kind, target_kind) in {
            ("product_area", "feature"),
            ("feature", "requirement"),
        }
    if edge_type == "implements":
        return source_kind in {"task", "bug"} and target_kind == "requirement"
    if edge_type == "verifies":
        return source_kind == "test" and target_kind == "requirement"
    if edge_type == "fixes":
        return source_kind == "task" and target_kind == "bug"
    if edge_type == "supersedes":
        return source_kind == target_kind
    if edge_type == "validates":
        return source_kind == "finding" and target_kind == "requirement"
    if edge_type == "supports":
        return source_kind == "task" and target_kind == "requirement"
    if edge_type == "documents":
        return source_kind == "decision" and target_kind == "requirement"
    if edge_type == "tests":
        return source_kind == "test" and target_kind in {"task", "bug", "test", "decision", "finding"}
    if edge_type == "relates_to":
        return bool(source_kind and target_kind)
    if edge_type in {"follows_up", "duplicates"}:
        return bool(source_kind and target_kind)
    if edge_type == "parent_of":
        return bool(source_kind and source_kind == target_kind)
    return False


def _would_cycle_dependency(conn, board_id: str, source_id: str, target_id: str) -> bool:
    adjacency: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT from_card_id, to_card_id FROM edges"
        " WHERE board_id=? AND type='depends_on'",
        (board_id,),
    ):
        adjacency.setdefault(row["from_card_id"], set()).add(row["to_card_id"])
    pending = [target_id]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == source_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def read_edges(board_id: str) -> list[Edge]:
    return [
        db.edge_from_row(row)
        for row in db.connect().execute(
            "SELECT * FROM edges WHERE board_id=? ORDER BY created_at, rowid", (board_id,)
        )
    ]


def write_edges(board_id: str, edges: list[Edge]) -> None:
    """Persist a board's edge set as given.

    Callers hand over the whole set because they have just filtered or extended
    it, so the stored set is reconciled to match: rows no longer present go, the
    rest are written. A board holds few enough edges that this stays one small
    statement plus one write per edge.
    """
    with db.transaction() as conn:
        keep = [edge.id for edge in edges]
        if keep:
            placeholders = ",".join("?" * len(keep))
            conn.execute(
                f"DELETE FROM edges WHERE board_id=? AND id NOT IN ({placeholders})",
                [board_id, *keep],
            )
        else:
            conn.execute("DELETE FROM edges WHERE board_id=?", (board_id,))
        for edge in edges:
            db.write_edge(conn, edge)


def _reorder_dependents(board_id: str) -> None:
    """A dependency-ordered column reads its order off this graph, so every
    edge write has to let the card store re-derive it."""
    from .card_store import reindex_dag_columns

    reindex_dag_columns(board_id)


def list_edges(board_id: str, card_id: str | None = None, type: str | None = None) -> list[Edge]:
    where = ["board_id = ?"]
    params: list = [board_id]
    if card_id:
        # Both directions, each served by its own index.
        where.append("(from_card_id = ? OR to_card_id = ?)")
        params.extend([card_id, card_id])
    if type:
        where.append("type = ?")
        params.append(type)
    return [
        db.edge_from_row(row)
        for row in db.connect().execute(
            f"SELECT * FROM edges WHERE {' AND '.join(where)} ORDER BY created_at, rowid",
            params,
        )
    ]


def get_edge(board_id: str, edge_id: str) -> Edge | None:
    row = db.connect().execute(
        "SELECT * FROM edges WHERE board_id=? AND id=?", (board_id, edge_id)
    ).fetchone()
    return db.edge_from_row(row) if row else None


def create_edge(board_id: str, data: CreateEdge) -> Edge:
    """Create one edge, or raise an :class:`EdgeError` naming why it can't.

    Every refusal is a distinct subclass so a caller can report an accurate
    status and message rather than the historical catch-all that told callers
    with perfectly valid ids that their ids were invalid.
    """
    with db.transaction() as conn:
        if not get_board(board_id):
            raise UnknownEndpoint(f"unknown board '{board_id}'")
        # Both endpoints must be real cards, or the graph grows references to
        # nothing and the dependency order walks off the board.
        found = {
            row["id"] for row in conn.execute(
                "SELECT id FROM cards WHERE board_id=? AND id IN (?,?)",
                (board_id, data.from_card_id, data.to_card_id),
            )
        }
        missing = [
            cid for cid in (data.from_card_id, data.to_card_id) if cid not in found
        ]
        if missing:
            raise UnknownEndpoint(
                "unknown card id" + ("s" if len(missing) > 1 else "")
                + ": " + ", ".join(missing)
            )
        if data.from_card_id == data.to_card_id:
            raise SelfLoop("a card cannot link to itself")
        if conn.execute(
            "SELECT 1 FROM edges WHERE board_id=? AND from_card_id=?"
            " AND to_card_id=? AND type=?",
            (board_id, data.from_card_id, data.to_card_id, data.type),
        ).fetchone():
            raise DuplicateEdge(
                f"a '{data.type}' edge between these cards already exists"
            )
        cards = {
            card.id: card
            for card in db.hydrate_cards(
                conn,
                conn.execute(
                    "SELECT * FROM cards WHERE board_id=? AND id IN (?,?)",
                    (board_id, data.from_card_id, data.to_card_id),
                ).fetchall(),
            )
        }
        source = cards[data.from_card_id]
        target = cards[data.to_card_id]
        if not (source.semantics.kind and target.semantics.kind):
            raise MissingSemanticKind(
                "both cards must have a semantic kind before linking"
            )
        if not direction_is_valid(data.type, source, target):
            raise InvalidDirection(
                f"edge type '{data.type}' is not valid from kind "
                f"'{source.semantics.kind}' to kind '{target.semantics.kind}'"
            )
        if data.type in {"defines", "parent_of"} and target.parent_id != data.from_card_id:
            raise ParentRelationshipRequired(
                f"edge type '{data.type}' requires the target card's parent "
                "to be the source card"
            )
        if data.type == "depends_on" and _would_cycle_dependency(
            conn,
            board_id,
            data.from_card_id,
            data.to_card_id,
        ):
            raise DependencyCycle("this edge would create a dependency cycle")
        edge = Edge(
            board_id=board_id, from_card_id=data.from_card_id, to_card_id=data.to_card_id,
            type=data.type, label=data.label,
        )
        db.write_edge(conn, edge)
        db.write_event(conn, Event(
            type="edge_created",
            board_id=board_id,
            detail=f"{data.type}: {data.from_card_id[:8]}… → {data.to_card_id[:8]}…",
        ))
    _reorder_dependents(board_id)
    return edge


def delete_edge(board_id: str, edge_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM edges WHERE board_id=? AND id=?", (board_id, edge_id)
        )
        if not cursor.rowcount:
            return False
        db.write_event(conn, Event(type="edge_deleted", board_id=board_id, detail=edge_id))
    _reorder_dependents(board_id)
    return True
