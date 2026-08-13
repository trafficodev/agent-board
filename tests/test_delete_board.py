import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

from agent_board import board_store
from fastapi.testclient import TestClient
from agent_board.main import app


class DeleteBoardTest(unittest.TestCase):
    """Regression test for a 500 on DELETE /api/boards/{id}: the endpoint calls
    canvas_sync.disable_sync(board_id), which called board_store._with_lock /
    _release_lock -- removed when board writes moved onto SQLite transactions
    (the write transaction is the lock now). Any delete crashed with
    AttributeError, surfaced to callers as a bare 500."""

    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")
        self.client = TestClient(app)

    def test_delete_empty_board_succeeds(self):
        board = board_store.create_board("Scratch")

        response = self.client.delete(f"/api/boards/{board.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertIsNone(board_store.get_board(board.id))

    def test_delete_board_with_non_url_remote_string_succeeds(self):
        """Mirrors the stray board that triggered this bug: remote_url was set
        to a literal board-id string instead of a real git remote URL."""
        board = board_store.create_board("Stray duplicate", remote_url="f247f3dde620")

        response = self.client.delete(f"/api/boards/{board.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(board_store.get_board(board.id))
        self.assertIsNone(board_store.get_board_by_remote_url("f247f3dde620"))

    def test_delete_unknown_board_returns_404(self):
        response = self.client.delete("/api/boards/000000000000")

        self.assertEqual(response.status_code, 404)

    def test_delete_board_with_canvas_sync_enabled_still_succeeds(self):
        """enable_sync also called the removed lock functions; cover it too
        so the whole canvas_sync module stays consistent with board_store."""
        board = board_store.create_board("Synced")
        from agent_board import canvas_sync

        canvas_sync._write_syncs({board.id: {"enabled": True, "canvas_api_url": "http://localhost:8002/api"}})

        response = self.client.delete(f"/api/boards/{board.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(board_store.get_board(board.id))


if __name__ == "__main__":
    unittest.main()
