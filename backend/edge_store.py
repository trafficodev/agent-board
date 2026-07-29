"""Edge persistence. Arbitrary typed connections between two cards, stored
per-board alongside cards/columns. `type` is a free-form string (e.g.
"blocked_by", "blocks", "duplicates", "relates_to") — no fixed vocabulary,
same open-ended approach labels already take."""

import db
from models import CreateEdge, Edge, Event

from board_store import get_board


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
    from card_store import reindex_dag_columns

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


def create_edge(board_id: str, data: CreateEdge) -> Edge | None:
    with db.transaction() as conn:
        if not get_board(board_id):
            return None
        # Both endpoints must be real cards, or the graph grows references to
        # nothing and the dependency order walks off the board.
        found = {
            row["id"] for row in conn.execute(
                "SELECT id FROM cards WHERE board_id=? AND id IN (?,?)",
                (board_id, data.from_card_id, data.to_card_id),
            )
        }
        if data.from_card_id not in found or data.to_card_id not in found:
            return None
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
