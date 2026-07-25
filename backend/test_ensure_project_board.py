import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

import board_store


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

    def test_different_projects_get_different_boards(self):
        a = board_store.ensure_project_board("git@github.com:acme/widgets.git")
        b = board_store.ensure_project_board("git@github.com:acme/gadgets.git")

        self.assertNotEqual(a.id, b.id)
        self.assertEqual(len(board_store.list_boards()), 2)


if __name__ == "__main__":
    unittest.main()
