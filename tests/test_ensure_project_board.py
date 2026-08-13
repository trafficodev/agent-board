import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

from agent_board import board_store
from fastapi.testclient import TestClient
from agent_board.main import app


class EnsureProjectBoardTest(unittest.TestCase):
    def setUp(self):
        # Each test gets its own boards dir so board-count assertions aren't
        # polluted by boards other tests in this file already created.
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

    def test_normalizes_scp_ssh_and_https_forms_to_the_same_identity(self):
        forms = [
            "git@github.com:ofekron/better-agent.git",
            "ssh://git@github.com/ofekron/better-agent.git",
            "https://github.com/ofekron/better-agent.git",
            "https://github.com/ofekron/better-agent",
            "https://github.com/ofekron/better-agent/",
        ]
        normalized = {board_store.normalize_remote_url(f) for f in forms}
        self.assertEqual(normalized, {"github.com/ofekron/better-agent"})

    def test_ensure_project_board_creates_once_and_is_idempotent(self):
        first = board_store.ensure_project_board("git@github.com:acme/widgets.git")
        second = board_store.ensure_project_board("https://github.com/acme/widgets")

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.remote_url, "github.com/acme/widgets")
        self.assertEqual([c.name for c in first.columns], ["Open Items", "In Progress", "In Testing", "Done"])
        self.assertEqual(len(board_store.list_boards()), 1)

    def test_ensure_project_board_finds_a_board_created_manually_with_remote_url(self):
        manual = board_store.create_board("Widgets", remote_url="git@github.com:acme/widgets.git")

        found = board_store.ensure_project_board("https://github.com/acme/widgets.git")

        self.assertEqual(found.id, manual.id)
        self.assertEqual(len(board_store.list_boards()), 1)

    def test_ensure_project_board_requires_a_remote_url(self):
        with self.assertRaises(ValueError):
            board_store.ensure_project_board("")

    def test_ensure_project_board_rejects_a_bare_token_instead_of_a_remote(self):
        """Regression: a caller once passed another board's bare id where a
        git remote_url was expected. normalize_remote_url silently accepted
        it as a valid identity, so ensure_project_board kept finding and
        reusing the resulting phantom board on every subsequent mistaken
        call instead of failing loudly."""
        self.assertEqual(board_store.normalize_remote_url("f247f3dde620"), "")
        with self.assertRaisesRegex(ValueError, "remote_url is required"):
            board_store.ensure_project_board("f247f3dde620")
        self.assertEqual(board_store.list_boards(), [])

    def test_different_projects_get_different_boards(self):
        a = board_store.ensure_project_board("git@github.com:acme/widgets.git")
        b = board_store.ensure_project_board("git@github.com:acme/gadgets.git")

        self.assertNotEqual(a.id, b.id)
        self.assertEqual(len(board_store.list_boards()), 2)

    def test_linked_remote_resolves_to_existing_board_and_persists(self):
        private = board_store.ensure_project_board("git@gitlab.com:acme/widgets-private.git")

        linked = board_store.link_project_remote(private.id, "https://github.com/acme/widgets.git")
        ensured = board_store.ensure_project_board("git@github.com:acme/widgets.git")
        reloaded = board_store.get_board(private.id)

        self.assertEqual(linked.id, private.id)
        self.assertEqual(ensured.id, private.id)
        self.assertEqual(reloaded.remote_aliases, ["github.com/acme/widgets"])
        self.assertEqual(len(board_store.list_boards()), 1)

    def test_link_is_idempotent_and_rejects_a_remote_owned_by_another_board(self):
        private = board_store.ensure_project_board("git@gitlab.com:acme/widgets-private.git")
        public = board_store.ensure_project_board("git@github.com:acme/widgets.git")

        same = board_store.link_project_remote(private.id, private.remote_url)
        self.assertEqual(same.id, private.id)
        with self.assertRaisesRegex(ValueError, "already linked to another board"):
            board_store.link_project_remote(private.id, public.remote_url)

    def test_concurrent_different_alias_links_do_not_lose_updates(self):
        board = board_store.ensure_project_board("git@gitlab.com:acme/widgets-private.git")
        barrier = threading.Barrier(3)
        errors = []

        def link(remote_url):
            try:
                barrier.wait()
                board_store.link_project_remote(board.id, remote_url)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=link, args=("git@github.com:acme/widgets.git",)),
            threading.Thread(target=link, args=("git@gitlab.com:acme/widgets-public.git",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(
            set(board_store.get_board(board.id).remote_aliases),
            {"github.com/acme/widgets", "gitlab.com/acme/widgets-public"},
        )

    def test_legacy_board_json_without_remote_aliases_migrates(self):
        """Board documents predating remote_aliases must still import, and the
        board must still be findable by the remote it does carry."""
        from agent_board import db

        boards_dir = Path(os.environ["AGENT_BOARD_HOME"]) / "boards"
        boards_dir.mkdir(parents=True, exist_ok=True)
        legacy = {
            "id": "legacy000001",
            "name": "Widgets",
            "description": "",
            "remote_url": "git@github.com:acme/widgets.git",
            "columns": [
                {"id": "legacycol001", "board_id": "legacy000001", "name": "Open", "position": 0}
            ],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        (boards_dir / "legacy000001.json").write_text(json.dumps(legacy))
        db.connect().execute("DELETE FROM schema_meta WHERE key='json_migrated'")

        db.migrate_json_if_needed()

        reloaded = board_store.get_board("legacy000001")
        self.assertEqual(reloaded.remote_aliases, [])
        self.assertEqual(
            board_store.get_board_by_remote_url("git@github.com:acme/widgets.git").id,
            "legacy000001",
        )

    def test_link_project_remote_api_reports_conflict(self):
        private = board_store.ensure_project_board("git@gitlab.com:acme/widgets-private.git")
        public = board_store.ensure_project_board("git@github.com:acme/widgets.git")

        response = TestClient(app).post(
            f"/api/boards/{private.id}/project-remotes",
            json={"remote_url": public.remote_url},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "remote_url is already linked to another board")


if __name__ == "__main__":
    unittest.main()
