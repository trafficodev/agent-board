import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-db-test-")

from agent_board import db
from agent_board.models import (
    AnswerNote,
    Board,
    Card,
    CardNote,
    Column,
    Edge,
    Event,
    FieldChange,
    SessionEntry,
)
from agent_board.card_semantics import CardEvidence, CardOwnership, CardSemantics


def _use_fresh_home() -> Path:
    """Point the whole module at an empty state directory and drop any
    connection to the previous one."""
    home = Path(tempfile.mkdtemp(prefix="agent-board-db-test-"))
    os.environ["AGENT_BOARD_HOME"] = str(home)
    db.close()
    return home


class SchemaTest(unittest.TestCase):
    def setUp(self):
        _use_fresh_home()
        self.conn = db.connect()

    def _names(self, kind: str) -> set[str]:
        return {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (kind,)
            )
        }

    def test_every_documented_index_exists(self):
        """INDEX_NOTES is the justification for each index; an index named there
        and missing from the schema means a tool lost its support."""
        indexes = self._names("index")
        for name in db.INDEX_NOTES:
            self.assertIn(name, indexes, f"{name} is documented but not created")

    def test_every_created_index_is_documented(self):
        created = {n for n in self._names("index") if n.startswith("idx_")}
        for name in created:
            self.assertIn(name, db.INDEX_NOTES, f"{name} exists with no recorded caller")

    def test_edge_identity_is_unique(self):
        with db.transaction() as conn:
            _seed_board(conn, "board1")
            conn.execute(
                "INSERT INTO cards (id,board_id,title,column_id,created_at,updated_at)"
                " VALUES ('card0000001','board1','A','col1',?,?)",
                (_NOW, _NOW),
            )
            conn.execute(
                "INSERT INTO cards (id,board_id,title,column_id,created_at,updated_at)"
                " VALUES ('card0000002','board1','B','col1',?,?)",
                (_NOW, _NOW),
            )
            conn.execute(
                "INSERT INTO edges VALUES ('edge1','board1','card0000001',"
                " 'card0000002','depends_on','',?)",
                (_NOW,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO edges VALUES ('edge2','board1','card0000001',"
                    " 'card0000002','depends_on','',?)",
                    (_NOW,),
                )

    def test_core_tables_exist(self):
        tables = self._names("table")
        for name in (
            "boards", "board_remotes", "columns", "cards", "card_labels",
            "card_notes", "card_sessions", "edges", "events",
            "change_votes",
        ):
            self.assertIn(name, tables)

    def test_event_cutover_columns_exist(self):
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(events)")
        }
        self.assertTrue({
            "projection_version",
            "document",
            "provider",
            "native_session_id",
            "reviewed_commit_sha",
            "voteable",
            "cutover_state",
        }.issubset(columns))
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0],
            str(db.SCHEMA_VERSION),
        )
        indexes = {
            row["name"]: row["unique"]
            for row in self.conn.execute("PRAGMA index_list(events)")
        }
        self.assertEqual(indexes["idx_events_id"], 1)

    def test_card_semantics_column_exists(self):
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(cards)")
        }
        self.assertIn("semantics", columns)

    def test_wal_and_foreign_keys_are_on(self):
        self.assertEqual(
            self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal"
        )
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_column_filter_uses_its_index(self):
        plan = self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM cards WHERE board_id=? AND column_id=? "
            "ORDER BY position",
            ("b", "c"),
        ).fetchall()
        self.assertIn("idx_cards_board_column", " ".join(str(row["detail"]) for row in plan))

    def test_open_question_lookup_uses_its_partial_index(self):
        plan = self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM card_notes WHERE board_id=? "
            "AND kind='question' AND answer='' ORDER BY created_at DESC",
            ("b",),
        ).fetchall()
        self.assertIn("idx_card_notes_open", " ".join(str(row["detail"]) for row in plan))

    def test_external_id_is_unique_per_board_but_blanks_are_not(self):
        with db.transaction() as conn:
            _seed_board(conn, "board1")
            for card_id in ("card0000001", "card0000002"):
                conn.execute(
                    "INSERT INTO cards (id, board_id, external_id, title, column_id,"
                    " created_at, updated_at) VALUES (?,?,'','t','col1',?,?)",
                    (card_id, "board1", _NOW, _NOW),
                )
            conn.execute(
                "INSERT INTO cards (id, board_id, external_id, title, column_id,"
                " created_at, updated_at) VALUES ('card0000003','board1','x','t','col1',?,?)",
                (_NOW, _NOW),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO cards (id, board_id, external_id, title, column_id,"
                    " created_at, updated_at) VALUES ('card0000004','board1','x','t','col1',?,?)",
                    (_NOW, _NOW),
                )


_NOW = datetime.now(timezone.utc).isoformat()


def _seed_board(conn, board_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO boards (id, name, description, remote_url,"
        " created_at, updated_at) VALUES (?,?,'','',?,?)",
        (board_id, "B", _NOW, _NOW),
    )
    conn.execute(
        "INSERT OR REPLACE INTO columns (id, board_id, name, position)"
        " VALUES ('col1',?,'Open',0)",
        (board_id,),
    )


def _card(**kwargs) -> Card:
    kwargs.setdefault("id", "card0000001")
    kwargs.setdefault("board_id", "board1")
    kwargs.setdefault("title", "T")
    kwargs.setdefault("column_id", "col1")
    return Card(**kwargs)


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        _use_fresh_home()
        with db.transaction() as conn:
            _seed_board(conn, "board1")

    def _read_one(self) -> Card:
        conn = db.connect()
        rows = conn.execute("SELECT * FROM cards WHERE board_id='board1'").fetchall()
        return db.hydrate_cards(conn, rows)[0]

    def test_card_survives_a_write_and_read_unchanged(self):
        original = _card(
            body="body text",
            external_id="ext:1",
            priority="critical",
            labels=["alpha", "beta"],
            metadata={"files": ["a.py"], "nested": {"k": 1}},
            semantics=CardSemantics(
                kind="requirement",
                catalog_lifecycle="active",
                outcome="Users can inspect state",
                acceptance_criteria=["State is current"],
                ownership=CardOwnership(component="storage"),
                evidence=[CardEvidence(kind="test", locator="backend/test_db.py")],
            ),
            notes=[
                CardNote(kind="question", text="why?"),
                CardNote(kind="note", text="because"),
            ],
            session_history=[
                SessionEntry(
                    claim_id="claim000001", session_id="s1", system="claude", action="created",
                    outcome="success",
                    changes=[FieldChange(field="title", before=None, after="T")],
                )
            ],
        )
        with db.transaction() as conn:
            db.write_card(conn, original)

        restored = self._read_one()
        self.assertEqual(restored.model_dump(mode="json"), original.model_dump(mode="json"))

    def test_rewriting_a_card_replaces_rather_than_accumulates_side_rows(self):
        card = _card(labels=["a", "b"], notes=[CardNote(text="one")])
        with db.transaction() as conn:
            db.write_card(conn, card)
        card.labels = ["c"]
        card.notes = []
        with db.transaction() as conn:
            db.write_card(conn, card)

        conn = db.connect()
        self.assertEqual(
            [r["label"] for r in conn.execute("SELECT label FROM card_labels")], ["c"]
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM card_notes").fetchone()[0], 0)

    def test_deleting_a_card_takes_its_side_rows_with_it(self):
        with db.transaction() as conn:
            db.write_card(conn, _card(labels=["a"], notes=[CardNote(text="n")]))
        with db.transaction() as conn:
            conn.execute("DELETE FROM cards WHERE id='card0000001'")
        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM card_labels").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM card_notes").fetchone()[0], 0)

    def test_full_text_search_finds_and_forgets_cards(self):
        with db.transaction() as conn:
            db.write_card(conn, _card(title="unmistakable", body="haystack"))
        conn = db.connect()
        hits = conn.execute(
            "SELECT c.id FROM cards_fts f JOIN cards c ON c.rowid=f.rowid"
            " WHERE cards_fts MATCH 'unmistakable'"
        ).fetchall()
        self.assertEqual([r["id"] for r in hits], ["card0000001"])

        with db.transaction() as conn:
            conn.execute("DELETE FROM cards WHERE id='card0000001'")
        self.assertEqual(
            db.connect().execute(
                "SELECT COUNT(*) FROM cards_fts WHERE cards_fts MATCH 'unmistakable'"
            ).fetchone()[0],
            0,
        )

    def test_full_text_index_follows_an_edited_body(self):
        with db.transaction() as conn:
            db.write_card(conn, _card(body="original wording"))
        card = self._read_one()
        card.body = "replacement wording"
        with db.transaction() as conn:
            db.write_card(conn, card)
        conn = db.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM cards_fts WHERE cards_fts MATCH 'original'"
            ).fetchone()[0], 0,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM cards_fts WHERE cards_fts MATCH 'replacement'"
            ).fetchone()[0], 1,
        )

    def test_event_and_edge_round_trip(self):
        event = Event(
            type="card_created", detail="d", board_id="board1",
            card_id="card0000001", session_id="s1", system="claude",
            action="created", snapshot={"title": "T"},
            projection_version=1,
            document={"title": "T"},
            provider="codex",
            native_session_id="native-1",
            reviewed_commit_sha="a" * 40,
            voteable=True,
            cutover_state="authored",
        )
        edge = Edge(
            board_id="board1", from_card_id="card0000001",
            to_card_id="card0000002", type="blocked_by", label="why",
        )
        with db.transaction() as conn:
            db.write_event(conn, event)
            db.write_edge(conn, edge)
        conn = db.connect()
        restored_event = db.event_from_row(conn.execute("SELECT * FROM events").fetchone())
        self.assertEqual(restored_event.snapshot, {"title": "T"})
        self.assertEqual(restored_event.document, {"title": "T"})
        self.assertEqual(restored_event.actor, "codex")
        self.assertTrue(restored_event.voteable)
        self.assertEqual(restored_event.reviewed_commit_sha, "a" * 40)
        restored_edge = db.edge_from_row(conn.execute("SELECT * FROM edges").fetchone())
        self.assertEqual(restored_edge.model_dump(mode="json"), edge.model_dump(mode="json"))

    def test_events_keep_insertion_order(self):
        with db.transaction() as conn:
            for i in range(5):
                db.write_event(conn, Event(type="t", detail=str(i), board_id="board1"))
        details = [
            r["detail"] for r in
            db.connect().execute("SELECT detail FROM events ORDER BY seq")
        ]
        self.assertEqual(details, ["0", "1", "2", "3", "4"])

    def test_failed_transaction_leaves_nothing_behind(self):
        with self.assertRaises(RuntimeError):
            with db.transaction() as conn:
                db.write_card(conn, _card(labels=["a"]))
                raise RuntimeError("boom")
        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM card_labels").fetchone()[0], 0)


class JsonMigrationTest(unittest.TestCase):
    def setUp(self):
        self.home = _use_fresh_home()
        self.boards = self.home / "boards"
        self.boards.mkdir(parents=True, exist_ok=True)
        (self.home / "events").mkdir(parents=True, exist_ok=True)

    def _write_legacy_board(self) -> Board:
        board = Board(
            id="board0000001", name="Legacy", description="d",
            remote_url="git@example.com:x.git", remote_aliases=["github.com/x"],
            columns=[
                Column(id="col000000001", board_id="board0000001", name="Open", position=0),
                Column(id="col000000002", board_id="board0000001", name="Done", position=1),
            ],
        )
        (self.boards / "board0000001.json").write_text(
            json.dumps(board.model_dump(mode="json"))
        )
        cards = [
            Card(
                id="card0000001", board_id="board0000001", title="First",
                body="hello", column_id="col000000001", labels=["x"],
                metadata={"k": "v"},
                notes=[CardNote(id="note0000001", kind="question", text="open?")],
                session_history=[SessionEntry(session_id="s1", action="created")],
            ),
            Card(
                id="card0000002", board_id="board0000001", title="Second",
                column_id="col000000002", parent_id="card0000001",
            ),
        ]
        (self.boards / "board0000001_cards.json").write_text(
            json.dumps([c.model_dump(mode="json") for c in cards])
        )
        (self.boards / "board0000001_edges.json").write_text(json.dumps([
            Edge(
                id="edge0000001", board_id="board0000001",
                from_card_id="card0000002", to_card_id="card0000001",
                type="blocked_by",
            ).model_dump(mode="json")
        ]))
        (self.home / "events" / "board0000001.jsonl").write_text(
            "\n".join(
                Event(type="card_created", detail=str(i), board_id="board0000001").model_dump_json()
                for i in range(3)
            ) + "\n"
        )
        return board

    def test_legacy_board_is_imported_whole(self):
        self._write_legacy_board()
        result = db.migrate_json_if_needed()
        self.assertEqual(
            (result["boards"], result["cards"], result["edges"], result["events"]),
            (1, 2, 1, 3),
        )
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM cards WHERE board_id='board0000001' ORDER BY id"
        ).fetchall()
        cards = db.hydrate_cards(conn, rows)
        self.assertEqual([c.title for c in cards], ["First", "Second"])
        self.assertEqual(cards[0].labels, ["x"])
        self.assertEqual(cards[0].metadata, {"k": "v"})
        self.assertEqual(cards[0].notes[0].text, "open?")
        self.assertEqual(cards[0].session_history[0].session_id, "s1")
        self.assertEqual(cards[1].parent_id, "card0000001")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0], 2
        )
        baselines = conn.execute(
            "SELECT * FROM events WHERE cutover_state='baseline' ORDER BY card_id"
        ).fetchall()
        self.assertEqual([row["card_id"] for row in baselines], ["card0000001", "card0000002"])
        self.assertTrue(all(row["voteable"] == 0 for row in baselines))
        self.assertTrue(all(row["reviewed_commit_sha"] == "" for row in baselines))
        self.assertTrue(all(json.loads(row["document"])["title"] for row in baselines))

    def test_remote_url_and_aliases_both_resolve_to_the_board(self):
        self._write_legacy_board()
        db.migrate_json_if_needed()
        from agent_board import board_store
        # Remotes are stored normalized, so either written form -- the scp-like
        # remote_url or the already-normalized alias -- resolves to the board.
        for url in ("git@example.com:x.git", "example.com/x", "github.com/x"):
            self.assertEqual(
                board_store.get_board_by_remote_url(url).id, "board0000001", url
            )

    def test_migration_runs_once(self):
        self._write_legacy_board()
        self.assertTrue(db.migrate_json_if_needed()["migrated"])
        second = db.migrate_json_if_needed()
        self.assertFalse(second["migrated"])
        self.assertEqual(
            db.connect().execute("SELECT COUNT(*) FROM cards").fetchone()[0], 2
        )

    def test_failed_import_rolls_back_every_board_and_completion_marker(self):
        self._write_legacy_board()
        board = json.loads((self.boards / "board0000001.json").read_text())
        board["id"] = "board0000002"
        board["columns"] = []
        (self.boards / "board0000002.json").write_text(json.dumps(board))
        (self.boards / "board0000002_cards.json").write_text(
            json.dumps([{"id": "invalid"}])
        )

        with self.assertRaises(ValueError):
            db.migrate_json_if_needed()

        conn = db.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 0)
        self.assertIsNone(
            conn.execute(
                "SELECT value FROM schema_meta WHERE key='json_migrated'"
            ).fetchone()
        )

    def test_sidecar_and_malformed_files_are_not_mistaken_for_boards(self):
        self._write_legacy_board()
        (self.boards / "board0000001_open_questions.json").write_text('{"questions": []}')
        (self.boards / "garbage.json").write_text("not json{")
        result = db.migrate_json_if_needed()
        self.assertEqual(result["boards"], 1)

    def test_migrated_cards_are_searchable(self):
        self._write_legacy_board()
        db.migrate_json_if_needed()
        hits = db.connect().execute(
            "SELECT c.title FROM cards_fts f JOIN cards c ON c.rowid=f.rowid"
            " WHERE cards_fts MATCH 'hello'"
        ).fetchall()
        self.assertEqual([r["title"] for r in hits], ["First"])

    def test_nothing_to_migrate_is_not_an_error(self):
        result = db.migrate_json_if_needed()
        self.assertEqual(result["boards"], 0)
        self.assertIsNone(
            db.connect().execute(
                "SELECT value FROM schema_meta WHERE key='json_migrated'"
            ).fetchone()
        )

        self._write_legacy_board()
        recovered = db.migrate_json_if_needed()
        self.assertTrue(recovered["migrated"])
        self.assertEqual(recovered["boards"], 1)


class SqliteCutoverMigrationTest(unittest.TestCase):
    def setUp(self):
        self.home = _use_fresh_home()
        conn = sqlite3.connect(db.db_path())
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '1');
            CREATE TABLE boards (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', remote_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE columns (
                id TEXT PRIMARY KEY, board_id TEXT NOT NULL, name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE cards (
                id TEXT PRIMARY KEY, board_id TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', column_id TEXT NOT NULL,
                parent_id TEXT, position INTEGER NOT NULL DEFAULT 0,
                priority TEXT NOT NULL DEFAULT 'medium', metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
                board_id TEXT NOT NULL DEFAULT '', card_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '', system TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '', type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL, snapshot TEXT
            );
            CREATE TABLE card_sessions (
                board_id TEXT NOT NULL, card_id TEXT NOT NULL, seq INTEGER NOT NULL,
                session_id TEXT NOT NULL DEFAULT '', system TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL, action TEXT NOT NULL DEFAULT '', outcome TEXT,
                changes TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (card_id, seq)
            );
            """
        )
        conn.execute(
            "INSERT INTO boards VALUES (?,?,?,?,?,?)",
            ("board1", "B", "", "", _NOW, _NOW),
        )
        conn.execute("INSERT INTO columns VALUES ('col1','board1','Open',0)")
        conn.execute(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("card1", "board1", "", "Live", "body", "col1", None, 0, "high", "{}", _NOW, _NOW),
        )
        conn.execute(
            "INSERT INTO events (id,board_id,card_id,type,detail,timestamp,snapshot)"
            " VALUES ('legacy1','board1','card1','card_updated','old',?,?)",
            (_NOW, json.dumps({"title": "Old"})),
        )
        conn.commit()
        conn.close()

    def test_migration_preserves_legacy_and_creates_one_non_voteable_baseline(self):
        conn = db.connect()
        card_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(cards)")
        }
        self.assertIn("semantics", card_columns)
        self.assertEqual(
            conn.execute("SELECT semantics FROM cards WHERE id='card1'").fetchone()[0],
            "{}",
        )
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(card_sessions)")
        }
        self.assertIn("claim_id", session_columns)
        legacy = conn.execute("SELECT * FROM events WHERE id='legacy1'").fetchone()
        self.assertEqual(legacy["cutover_state"], "legacy")
        self.assertEqual(legacy["voteable"], 0)
        self.assertIsNone(legacy["document"])

        baseline = conn.execute(
            "SELECT * FROM events WHERE card_id='card1' AND cutover_state='baseline'"
        ).fetchall()
        self.assertEqual(len(baseline), 1)
        self.assertEqual(json.loads(baseline[0]["document"])["title"], "Live")
        self.assertEqual(baseline[0]["reviewed_commit_sha"], "")
        self.assertEqual(baseline[0]["voteable"], 0)

        db.close()
        db.connect()
        self.assertEqual(
            db.connect().execute(
                "SELECT COUNT(*) FROM events WHERE card_id='card1' AND cutover_state='baseline'"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
