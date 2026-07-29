"""SQLite storage for Agent Board.

The database is the only authority. Boards used to be whole-file JSON
documents, which meant every read deserialized every card and every write
rewrote the file; a board that grows past a few thousand cards makes both
costs unacceptable. Here a card is a row, so a filtered list is an indexed
query and a mutation writes the rows it changed.

Nested card structure that is only ever read with its card -- metadata and a
session entry's field changes -- stays as JSON in its row. Structure that is
queried across cards -- labels, notes, session attribution -- is a table with
an index, because that is what makes the tool surface cheap.

Every index here exists for a specific caller; see INDEX_NOTES.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from models import Board, Card, CardNote, Column, Edge, Event, FieldChange, SessionEntry
from paths import home

SCHEMA_VERSION = 1

# Which caller each index serves. Kept next to the DDL so an index cannot
# quietly outlive the query that justified it.
INDEX_NOTES = {
    "idx_columns_board": "board hydration, column order",
    "idx_cards_board_column": "list_cards column filter, board render, column reindex",
    "idx_cards_board_parent": "parent_id filter, child lookup, delete cascade",
    "idx_cards_board_priority": "priority filter",
    "idx_cards_external": "upsert_card identity lookup (unique)",
    "idx_cards_updated": "sort=updated_desc",
    "idx_cards_created": "sort=created_desc",
    "idx_card_labels": "label filter, label facet",
    "idx_card_notes_open": "list_open_questions (partial: unanswered questions only)",
    "idx_card_notes_card": "card hydration",
    "idx_card_sessions_session": "get_session_activity, sort=sessions_desc",
    "idx_card_sessions_card": "card hydration",
    "idx_edges_from": "list_edges(card_id), DAG ordering",
    "idx_edges_to": "list_edges(card_id) reverse direction",
    "idx_events_board_seq": "get_events tail",
    "idx_events_card": "get_card_history, revert_card",
    "idx_events_session": "get_session_activity",
    "idx_board_remotes_url": "ensure_project_board / get_board_by_remote_url (unique)",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boards (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    remote_url  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- remote_url plus every alias, in one place, so "which board tracks this
-- repository" is a unique-index hit instead of a scan over boards.
CREATE TABLE IF NOT EXISTS board_remotes (
    board_id   TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    remote_url TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, remote_url)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_board_remotes_url ON board_remotes(remote_url);

CREATE TABLE IF NOT EXISTS columns (
    id       TEXT PRIMARY KEY,
    board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    name     TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_columns_board ON columns(board_id, position);

CREATE TABLE IF NOT EXISTS cards (
    id          TEXT PRIMARY KEY,
    board_id    TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    column_id   TEXT NOT NULL,
    parent_id   TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    priority    TEXT NOT NULL DEFAULT 'medium',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_board_column   ON cards(board_id, column_id, position);
CREATE INDEX IF NOT EXISTS idx_cards_board_parent   ON cards(board_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_cards_board_priority ON cards(board_id, priority);
CREATE INDEX IF NOT EXISTS idx_cards_updated        ON cards(board_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_cards_created        ON cards(board_id, created_at DESC);
-- Partial, because the empty external_id is the common case and is not an identity.
CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_external
    ON cards(board_id, external_id) WHERE external_id <> '';

CREATE TABLE IF NOT EXISTS card_labels (
    board_id TEXT NOT NULL,
    card_id  TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    label    TEXT NOT NULL,
    PRIMARY KEY (card_id, label)
);
CREATE INDEX IF NOT EXISTS idx_card_labels ON card_labels(board_id, label);

CREATE TABLE IF NOT EXISTS card_notes (
    id          TEXT PRIMARY KEY,
    board_id    TEXT NOT NULL,
    card_id     TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'note',
    text        TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    answered_by TEXT NOT NULL DEFAULT '',
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_card_notes_card ON card_notes(card_id, seq);
-- The attention query: unanswered questions, newest first, across every board.
CREATE INDEX IF NOT EXISTS idx_card_notes_open
    ON card_notes(board_id, created_at DESC)
    WHERE kind = 'question' AND answer = '';

CREATE TABLE IF NOT EXISTS card_sessions (
    board_id   TEXT NOT NULL,
    card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    system     TEXT NOT NULL DEFAULT '',
    timestamp  TEXT NOT NULL,
    action     TEXT NOT NULL DEFAULT '',
    outcome    TEXT,
    changes    TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (card_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_card_sessions_session ON card_sessions(session_id, board_id);
CREATE INDEX IF NOT EXISTS idx_card_sessions_card    ON card_sessions(card_id, seq);

CREATE TABLE IF NOT EXISTS edges (
    id           TEXT PRIMARY KEY,
    board_id     TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
    from_card_id TEXT NOT NULL,
    to_card_id   TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'relates_to',
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(board_id, from_card_id);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(board_id, to_card_id);

-- The journal. seq is the total order the JSONL file used to get from line
-- order, and is what history replay walks.
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL,
    board_id   TEXT NOT NULL DEFAULT '',
    card_id    TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    system     TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL DEFAULT '',
    type       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    timestamp  TEXT NOT NULL,
    snapshot   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_board_seq ON events(board_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_events_card      ON events(board_id, card_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id, seq DESC);

-- Full-text over what search actually matches. Contentless-external so the
-- card row stays the only copy of title/body; triggers keep it in step.
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    title, body, content='cards', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS cards_fts_insert AFTER INSERT ON cards BEGIN
    INSERT INTO cards_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS cards_fts_delete AFTER DELETE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS cards_fts_update AFTER UPDATE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, body)
        VALUES ('delete', old.rowid, old.title, old.body);
    INSERT INTO cards_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
"""


def db_path() -> Path:
    return home() / "agent-board.sqlite3"


_local = threading.local()


def connect() -> sqlite3.Connection:
    """This thread's connection, opened and initialized on first use.

    One connection per thread rather than one per process: sqlite3 objects are
    not shareable across threads, and the server answers requests on many.
    """
    existing = getattr(_local, "conn", None)
    path = db_path()
    if existing is not None and getattr(_local, "path", None) == path:
        return existing
    if existing is not None:
        existing.close()  # AGENT_BOARD_HOME moved (tests do this)

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets readers work while a writer holds the board, which is the whole
    # point of moving off a global file lock.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    _local.conn = conn
    _local.path = path
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


class transaction:
    """One exclusive write transaction.

    BEGIN IMMEDIATE takes the write lock up front, which is what makes a
    read-modify-write safe across processes: two writers cannot both read, then
    both write, and lose one of the two changes.
    """

    def __init__(self):
        self.conn = connect()

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


# --- Row <-> model ---

def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def board_from_row(row: sqlite3.Row, columns: list[Column], aliases: list[str]) -> Board:
    return Board(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        remote_url=row["remote_url"],
        remote_aliases=aliases,
        columns=columns,
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def card_from_rows(
    row: sqlite3.Row,
    labels: list[str],
    notes: list[CardNote],
    sessions: list[SessionEntry],
) -> Card:
    return Card(
        id=row["id"],
        board_id=row["board_id"],
        external_id=row["external_id"],
        title=row["title"],
        body=row["body"],
        column_id=row["column_id"],
        parent_id=row["parent_id"],
        position=row["position"],
        priority=row["priority"],
        labels=labels,
        metadata=json.loads(row["metadata"]),
        session_history=sessions,
        notes=notes,
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def note_from_row(row: sqlite3.Row) -> CardNote:
    return CardNote(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        session_id=row["session_id"],
        created_at=_dt(row["created_at"]),
        answer=row["answer"],
        answered_by=row["answered_by"],
        answered_at=_dt(row["answered_at"]) if row["answered_at"] else None,
    )


def session_from_row(row: sqlite3.Row) -> SessionEntry:
    return SessionEntry(
        session_id=row["session_id"],
        system=row["system"],
        timestamp=_dt(row["timestamp"]),
        action=row["action"],
        outcome=row["outcome"],
        changes=[FieldChange(**c) for c in json.loads(row["changes"])],
    )


def edge_from_row(row: sqlite3.Row) -> Edge:
    return Edge(
        id=row["id"],
        board_id=row["board_id"],
        from_card_id=row["from_card_id"],
        to_card_id=row["to_card_id"],
        type=row["type"],
        label=row["label"],
        created_at=_dt(row["created_at"]),
    )


def event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        timestamp=_dt(row["timestamp"]),
        type=row["type"],
        detail=row["detail"],
        board_id=row["board_id"],
        card_id=row["card_id"],
        session_id=row["session_id"],
        system=row["system"],
        action=row["action"],
        snapshot=json.loads(row["snapshot"]) if row["snapshot"] else None,
    )


def write_card(conn: sqlite3.Connection, card: Card) -> None:
    """Insert or replace one card and its indexed side tables.

    The side tables are rewritten per card rather than diffed: a card holds a
    few labels and at most a couple of hundred notes, so replacing them is
    cheaper than working out which changed, and cannot drift.
    """
    conn.execute(
        """INSERT INTO cards
               (id, board_id, external_id, title, body, column_id, parent_id,
                position, priority, metadata, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               external_id=excluded.external_id, title=excluded.title,
               body=excluded.body, column_id=excluded.column_id,
               parent_id=excluded.parent_id, position=excluded.position,
               priority=excluded.priority, metadata=excluded.metadata,
               updated_at=excluded.updated_at""",
        (
            card.id, card.board_id, card.external_id, card.title, card.body,
            card.column_id, card.parent_id, card.position, card.priority,
            json.dumps(card.metadata, default=str),
            card.created_at.isoformat(), card.updated_at.isoformat(),
        ),
    )
    conn.execute("DELETE FROM card_labels WHERE card_id=?", (card.id,))
    conn.executemany(
        "INSERT OR IGNORE INTO card_labels(board_id, card_id, label) VALUES (?,?,?)",
        [(card.board_id, card.id, label) for label in card.labels],
    )
    conn.execute("DELETE FROM card_notes WHERE card_id=?", (card.id,))
    conn.executemany(
        """INSERT INTO card_notes
               (id, board_id, card_id, seq, kind, text, session_id,
                created_at, answer, answered_by, answered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                note.id, card.board_id, card.id, seq, note.kind, note.text,
                note.session_id, note.created_at.isoformat(), note.answer,
                note.answered_by, _iso(note.answered_at),
            )
            for seq, note in enumerate(card.notes)
        ],
    )
    conn.execute("DELETE FROM card_sessions WHERE card_id=?", (card.id,))
    conn.executemany(
        """INSERT INTO card_sessions
               (board_id, card_id, seq, session_id, system, timestamp,
                action, outcome, changes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (
                card.board_id, card.id, seq, entry.session_id, entry.system,
                entry.timestamp.isoformat(), entry.action, entry.outcome,
                json.dumps([c.model_dump(mode="json") for c in entry.changes], default=str),
            )
            for seq, entry in enumerate(card.session_history)
        ],
    )


def write_event(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        """INSERT INTO events
               (id, board_id, card_id, session_id, system, action, type,
                detail, timestamp, snapshot)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            event.id, event.board_id, event.card_id, event.session_id,
            event.system, event.action, event.type, event.detail,
            event.timestamp.isoformat(),
            json.dumps(event.snapshot, default=str) if event.snapshot is not None else None,
        ),
    )


def write_edge(conn: sqlite3.Connection, edge: Edge) -> None:
    conn.execute(
        """INSERT INTO edges
               (id, board_id, from_card_id, to_card_id, type, label, created_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               from_card_id=excluded.from_card_id, to_card_id=excluded.to_card_id,
               type=excluded.type, label=excluded.label""",
        (
            edge.id, edge.board_id, edge.from_card_id, edge.to_card_id,
            edge.type, edge.label, edge.created_at.isoformat(),
        ),
    )


def hydrate_cards(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Card]:
    """Turn card rows into cards, fetching their side tables in one query each
    rather than one per card."""
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(ids))

    labels: dict[str, list[str]] = {}
    for row in conn.execute(
        f"SELECT card_id, label FROM card_labels WHERE card_id IN ({placeholders})", ids
    ):
        labels.setdefault(row["card_id"], []).append(row["label"])

    notes: dict[str, list[CardNote]] = {}
    for row in conn.execute(
        f"SELECT * FROM card_notes WHERE card_id IN ({placeholders}) ORDER BY card_id, seq", ids
    ):
        notes.setdefault(row["card_id"], []).append(note_from_row(row))

    sessions: dict[str, list[SessionEntry]] = {}
    for row in conn.execute(
        f"SELECT * FROM card_sessions WHERE card_id IN ({placeholders}) ORDER BY card_id, seq", ids
    ):
        sessions.setdefault(row["card_id"], []).append(session_from_row(row))

    return [
        card_from_rows(
            row,
            labels.get(row["id"], []),
            notes.get(row["id"], []),
            sessions.get(row["id"], []),
        )
        for row in rows
    ]


# --- Migration from the JSON boards this replaced ---

def _json_boards() -> list[Path]:
    directory = home() / "boards"
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.glob("*.json")
        if not p.stem.endswith("_cards")
        and not p.stem.endswith("_edges")
        and not p.stem.endswith("_open_questions")
    )


def migrate_json_if_needed() -> dict:
    """Import legacy JSON boards once, then leave them alone.

    The JSON files are not deleted: this is a one-way cutover and keeping the
    old documents costs nothing, but nothing reads them afterwards.
    """
    conn = connect()
    done = conn.execute(
        "SELECT value FROM schema_meta WHERE key='json_migrated'"
    ).fetchone()
    if done:
        return {"migrated": False, "reason": "already migrated"}

    boards, cards, edges, events = 0, 0, 0, 0
    for board_file in _json_boards():
        try:
            payload = json.loads(board_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "id" not in payload:
            continue
        board = Board.model_validate(payload)
        with transaction() as tx:
            _insert_board(tx, board)
            boards += 1
            cards += _migrate_cards(tx, board_file, board.id)
            edges += _migrate_edges(tx, board_file, board.id)
            events += _migrate_events(tx, board.id)

    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('json_migrated', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    return {"migrated": True, "boards": boards, "cards": cards, "edges": edges, "events": events}


def _insert_board(conn: sqlite3.Connection, board: Board) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO boards
               (id, name, description, remote_url, created_at, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (
            board.id, board.name, board.description, board.remote_url,
            board.created_at.isoformat(), board.updated_at.isoformat(),
        ),
    )
    for column in board.columns:
        conn.execute(
            """INSERT OR REPLACE INTO columns (id, board_id, name, position)
               VALUES (?,?,?,?)""",
            (column.id, board.id, column.name, column.position),
        )
    remotes = [(board.remote_url, 1)] if board.remote_url else []
    remotes += [(alias, 0) for alias in board.remote_aliases]
    for url, primary in remotes:
        conn.execute(
            """INSERT OR IGNORE INTO board_remotes (board_id, remote_url, is_primary)
               VALUES (?,?,?)""",
            (board.id, url, primary),
        )


def _migrate_cards(conn: sqlite3.Connection, board_file: Path, board_id: str) -> int:
    path = board_file.with_name(f"{board_id}_cards.json")
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    count = 0
    for entry in payload:
        card = Card.model_validate(entry)
        write_card(conn, card)
        count += 1
    return count


def _migrate_edges(conn: sqlite3.Connection, board_file: Path, board_id: str) -> int:
    path = board_file.with_name(f"{board_id}_edges.json")
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    count = 0
    for entry in payload:
        write_edge(conn, Edge.model_validate(entry))
        count += 1
    return count


def _migrate_events(conn: sqlite3.Connection, board_id: str) -> int:
    path = home() / "events" / f"{board_id}.jsonl"
    if not path.exists():
        return 0
    count = 0
    # Line order is the journal's total order; inserting in the same order
    # gives the events table the same sequence.
    with open(path) as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                write_event(conn, Event.model_validate_json(line))
            except (ValueError, sqlite3.Error):
                continue
            count += 1
    return count
