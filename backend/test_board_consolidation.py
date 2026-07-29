import os
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

import board_consolidation
import board_store
import canvas_sync
import card_history
import card_store
import edge_store
import search_logic
from fastapi.testclient import TestClient
from main import app
from models import AddNote, AddSession, CreateCard, CreateEdge, MoveCard, UpdateCard


class BoardConsolidationTest(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")
        self.target = board_store.ensure_project_board("git@gitlab.com:acme/widgets-private.git")
        self.source = board_store.ensure_project_board("git@github.com:acme/widgets.git")

    def _column(self, board, name):
        return next(column for column in board.columns if column.name == name)

    def _build_source_history(self):
        source_open = self._column(self.source, "Open Items")
        source_testing = self._column(self.source, "In Testing")
        parent = card_store.create_card(
            self.source.id,
            CreateCard(
                title="Parent",
                column_id=source_open.id,
                metadata={
                    "edited_files": ["frontend/src/App.tsx"],
                    "git_commits": ["abc123"],
                    "worktrees": ["/repo/dev"],
                },
            ),
        )
        child = card_store.create_card(
            self.source.id,
            CreateCard(
                title="Child",
                column_id=source_open.id,
                parent_id=parent.id,
            ),
        )
        card_store.add_note(
            self.source.id,
            parent.id,
            AddNote(kind="question", text="Still blocked?"),
        )
        card_store.add_session(
            self.source.id,
            parent.id,
            AddSession(session_id="session-1", action="investigated"),
        )
        card_store.move_card(
            self.source.id,
            parent.id,
            MoveCard(column_id=source_testing.id),
        )
        deleted = card_store.create_card(
            self.source.id,
            CreateCard(title="Deleted", column_id=source_open.id),
        )
        card_store.update_card(
            self.source.id,
            deleted.id,
            UpdateCard(body="Preserved in journal"),
        )
        card_store.delete_card(self.source.id, deleted.id)
        edge = edge_store.create_edge(
            self.source.id,
            CreateEdge(
                from_card_id=parent.id,
                to_card_id=child.id,
                type="blocks",
                label="parent first",
            ),
        )
        return parent, child, deleted, edge

    def test_consolidates_cards_history_edges_columns_and_remotes(self):
        parent, child, deleted, edge = self._build_source_history()
        source_event_ids = {event.id for event in card_history.read_events(self.source.id)}

        result = board_consolidation.consolidate_project_board(self.source.id, self.target.id)

        self.assertEqual(result.id, self.target.id)
        self.assertIsNone(board_store.get_board(self.source.id))
        self.assertEqual(
            board_store.ensure_project_board("git@github.com:acme/widgets.git").id,
            self.target.id,
        )
        target_cards = {card.id: card for card in card_store.list_cards(self.target.id)}
        self.assertIn(parent.id, target_cards)
        self.assertEqual(target_cards[child.id].parent_id, parent.id)
        self.assertEqual(target_cards[parent.id].board_id, self.target.id)
        self.assertEqual(
            target_cards[parent.id].metadata["edited_files"],
            ["frontend/src/App.tsx"],
        )
        self.assertEqual(target_cards[parent.id].notes[0].text, "Still blocked?")
        self.assertEqual(
            [question["text"] for question in card_store.open_questions(self.target.id)],
            ["Still blocked?"],
        )
        self.assertTrue(
            any(q['card_id'] == parent.id for q in card_store.open_questions(self.target.id))
        )
        target_columns = {column.id for column in result.columns}
        self.assertIn(target_cards[parent.id].column_id, target_columns)
        self.assertTrue(all(
            change.after in target_columns
            for entry in target_cards[parent.id].session_history
            for change in entry.changes
            if change.field == "column_id" and change.after is not None
        ))
        migrated_edge = edge_store.get_edge(self.target.id, edge.id)
        self.assertEqual(migrated_edge.board_id, self.target.id)
        self.assertGreaterEqual(len(card_history.card_versions(self.target.id, deleted.id)), 2)
        target_event_ids = {event.id for event in card_history.read_events(self.target.id)}
        self.assertTrue(source_event_ids <= target_event_ids)
        migrated_source_events = [
            event
            for event in card_history.read_events(self.target.id)
            if event.id in source_event_ids
        ]
        self.assertTrue(all(event.board_id == self.target.id for event in migrated_source_events))
        searchable_cards = [card.model_dump(mode="json") for card in target_cards.values()]
        searchable_columns = [column.model_dump(mode="json") for column in result.columns]
        self.assertEqual(
            [card["id"] for card in search_logic.search_cards(
                searchable_cards,
                searchable_columns,
                query="has:file",
            )],
            [parent.id, child.id],
        )
        self.assertFalse(board_consolidation.has_pending_consolidation())

    def _assert_enabled_canvas_sync_fails_closed(self, board_id):
        syncs = {
            board_id: {
                "enabled": True,
                "canvas_board_id": "canvas-source",
                "canvas_api_url": "http://localhost:8002/api",
            },
        }
        canvas_sync._write_syncs(syncs)

        with self.assertRaisesRegex(
            board_consolidation.ConsolidationConflictError,
            "canvas sync must be disabled",
        ):
            board_consolidation.consolidate_project_board(self.source.id, self.target.id)

        self.assertIsNotNone(board_store.get_board(self.source.id))
        self.assertFalse(board_consolidation.has_pending_consolidation())

    def test_enabled_source_canvas_sync_fails_closed(self):
        self._assert_enabled_canvas_sync_fails_closed(self.source.id)

    def test_enabled_target_canvas_sync_fails_closed(self):
        self._assert_enabled_canvas_sync_fails_closed(self.target.id)

    def test_mid_apply_failure_recovers_from_durable_manifest(self):
        self._build_source_history()
        original = board_consolidation._atomic_write_json
        calls = 0

        def fail_once(path, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            return original(path, payload)

        with patch.object(board_consolidation, "_atomic_write_json", side_effect=fail_once):
            result = board_consolidation.consolidate_project_board(self.source.id, self.target.id)

        self.assertEqual(result.id, self.target.id)
        self.assertIsNone(board_store.get_board(self.source.id))
        self.assertFalse(board_consolidation.has_pending_consolidation())

    def test_source_writer_serializes_behind_consolidation(self):
        source_open = self._column(self.source, "Open Items")
        entered = threading.Event()
        release = threading.Event()
        writer_done = threading.Event()
        writer_result = []
        consolidation_errors = []
        original = board_consolidation._build_manifest

        def pause_build(source, target):
            entered.set()
            release.wait(timeout=5)
            return original(source, target)

        def write_source():
            writer_result.append(card_store.create_card(
                self.source.id,
                CreateCard(title="Concurrent", column_id=source_open.id),
            ))
            writer_done.set()

        def consolidate():
            try:
                board_consolidation.consolidate_project_board(self.source.id, self.target.id)
            except Exception as exc:
                consolidation_errors.append(exc)

        with patch.object(board_consolidation, "_build_manifest", side_effect=pause_build):
            consolidation = threading.Thread(
                target=consolidate,
            )
            consolidation.start()
            self.assertTrue(entered.wait(timeout=5))
            writer = threading.Thread(target=write_source)
            writer.start()
            self.assertFalse(writer_done.wait(timeout=0.1))
            release.set()
            consolidation.join(timeout=5)
            writer.join(timeout=5)

        self.assertTrue(writer_done.is_set())
        self.assertEqual(consolidation_errors, [])
        self.assertEqual(writer_result, [None])

    def test_source_edge_writer_serializes_behind_consolidation(self):
        source_open = self._column(self.source, "Open Items")
        first = card_store.create_card(
            self.source.id,
            CreateCard(title="First", column_id=source_open.id),
        )
        second = card_store.create_card(
            self.source.id,
            CreateCard(title="Second", column_id=source_open.id),
        )
        entered = threading.Event()
        release = threading.Event()
        writer_done = threading.Event()
        writer_result = []
        original = board_consolidation._build_manifest

        def pause_build(source, target):
            entered.set()
            release.wait(timeout=5)
            return original(source, target)

        def write_source_edge():
            writer_result.append(edge_store.create_edge(
                self.source.id,
                CreateEdge(from_card_id=first.id, to_card_id=second.id, type="blocks"),
            ))
            writer_done.set()

        with patch.object(board_consolidation, "_build_manifest", side_effect=pause_build):
            consolidation = threading.Thread(
                target=board_consolidation.consolidate_project_board,
                args=(self.source.id, self.target.id),
            )
            consolidation.start()
            self.assertTrue(entered.wait(timeout=5))
            writer = threading.Thread(target=write_source_edge)
            writer.start()
            self.assertFalse(writer_done.wait(timeout=0.1))
            release.set()
            consolidation.join(timeout=5)
            writer.join(timeout=5)

        self.assertTrue(writer_done.is_set())
        self.assertEqual(writer_result, [None])
        self.assertEqual(edge_store.list_edges(self.source.id), [])

    def test_identical_card_id_is_deduplicated(self):
        source_open = self._column(self.source, "Open Items")
        target_open = self._column(self.target, "Open Items")
        source_card = card_store.create_card(
            self.source.id,
            CreateCard(title="Same", column_id=source_open.id),
        )
        target_card = source_card.model_copy(deep=True)
        target_card.board_id = self.target.id
        target_card.column_id = target_open.id
        for entry in target_card.session_history:
            for change in entry.changes:
                if change.field == "column_id":
                    change.after = target_open.id
        card_store._write_cards(self.target.id, [target_card])

        board_consolidation.consolidate_project_board(self.source.id, self.target.id)

        matching = [
            card for card in card_store.list_cards(self.target.id)
            if card.id == source_card.id
        ]
        self.assertEqual(len(matching), 1)

    def test_pending_manifest_blocks_ordinary_api_reads(self):
        manifest = board_consolidation._manifest_path(self.source.id, self.target.id)
        manifest.write_text("{}")
        try:
            response = TestClient(app).get("/api/boards")
        finally:
            manifest.unlink()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Board consolidation recovery is pending",
        )
