import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
    prefix="agent-board-main-startup-test-"
)

import main
import board_store
import card_history
from models import (
    CHANGE_NATIVE_SESSION_HEADER,
    CHANGE_PROVIDER_HEADER,
    CHANGE_REVIEWED_COMMIT_HEADER,
)


class MainStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_initializes_storage_before_starting_workers(self):
        with patch.object(main.db, "initialize_storage") as initialize, patch.object(
            main.validation_worker, "is_enabled", return_value=False
        ), patch.object(
            main.board_consolidation, "recover_pending_consolidations"
        ) as recover:
            await main._start_validation_worker()

        initialize.assert_called_once_with()
        recover.assert_called_once_with()
        self.assertIsNone(main._validation_task)


class MainChangeContextTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board("Context", "", ["Open"])
        self.client = TestClient(main.app)
        self.path = f"/api/boards/{self.board.id}/cards"
        self.body = {"title": "T", "column_id": self.board.columns[0].id}

    def test_authored_card_mutation_fails_closed_without_context(self):
        response = self.client.post(self.path, json=self.body)

        self.assertEqual(response.status_code, 400)

    def test_ui_can_resolve_the_exact_current_revision(self):
        response = self.client.get("/api/revision")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json()["commit_sha"], r"^[0-9a-f]{40}$")

    def test_authored_card_mutation_persists_supplied_context(self):
        reviewed_sha = "a" * 40
        response = self.client.post(self.path, json=self.body, headers={
            CHANGE_PROVIDER_HEADER: "codex",
            CHANGE_NATIVE_SESSION_HEADER: "native-http",
            CHANGE_REVIEWED_COMMIT_HEADER: reviewed_sha,
        })

        self.assertEqual(response.status_code, 201)
        card_id = response.json()["id"]
        event = next(
            event
            for event in card_history.read_events(self.board.id)
            if event.card_id == card_id
        )
        self.assertEqual(event.native_session_id, "native-http")
        self.assertEqual(event.reviewed_commit_sha, reviewed_sha)
        self.assertTrue(event.voteable)

    def test_authored_card_mutation_rejects_short_commit_sha(self):
        response = self.client.post(self.path, json=self.body, headers={
            CHANGE_PROVIDER_HEADER: "codex",
            CHANGE_NATIVE_SESSION_HEADER: "native-http",
            CHANGE_REVIEWED_COMMIT_HEADER: "abc1234",
        })

        self.assertEqual(response.status_code, 400)

    def test_changelog_vote_api_returns_diff_totals_and_audit(self):
        headers = {
            CHANGE_PROVIDER_HEADER: "codex",
            CHANGE_NATIVE_SESSION_HEADER: "native-http",
            CHANGE_REVIEWED_COMMIT_HEADER: "d" * 40,
        }
        created = self.client.post(self.path, json=self.body, headers=headers).json()
        self.client.patch(
            f"{self.path}/{created['id']}",
            json={"body": "changed"},
            headers=headers,
        )

        changes = self.client.get(
            f"{self.path}/{created['id']}/changes",
            headers={
                CHANGE_PROVIDER_HEADER: "codex",
                CHANGE_NATIVE_SESSION_HEADER: "native-http",
            },
        ).json()["items"]
        target = next(
            item
            for item in changes
            if item["path"] == "/body" and item["operation"] == "replace"
        )
        self.assertEqual(target["diff"].count("changed"), 1)

        result = self.client.post(
            f"{self.path}/{created['id']}/changes/{target['id']}/vote",
            json={"direction": 1},
            headers=headers,
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["summary"], {"up": 1, "down": 0, "current": 1})

        audit = self.client.get(
            f"{self.path}/{created['id']}/changes/{target['id']}/votes"
        ).json()["votes"]
        self.assertEqual(audit[0]["reviewed_commit_sha"], "d" * 40)
        self.assertTrue(audit[0]["timestamp"])

    def test_changelog_vote_api_fails_closed_without_context(self):
        headers = {
            CHANGE_PROVIDER_HEADER: "codex",
            CHANGE_NATIVE_SESSION_HEADER: "native-http",
            CHANGE_REVIEWED_COMMIT_HEADER: "e" * 40,
        }
        created = self.client.post(self.path, json=self.body, headers=headers).json()
        target = self.client.get(
            f"{self.path}/{created['id']}/changes"
        ).json()["items"][0]

        response = self.client.post(
            f"{self.path}/{created['id']}/changes/{target['id']}/vote",
            json={"direction": 1},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
