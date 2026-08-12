import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-mutation-errors-")

from fastapi.testclient import TestClient

import board_store
import card_store
import db
import main
from card_semantics import CardSemantics
from models import CreateCard


class CardMutationErrorTests(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
            prefix="agent-board-mutation-errors-"
        )
        db.close()
        self.board = board_store.create_board("Mutations", "", ["Open"])
        self.column_id = self.board.columns[0].id
        self.parent = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Parent",
                column_id=self.column_id,
                semantics=CardSemantics(kind="test"),
            ),
        )
        self.card = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Child",
                column_id=self.column_id,
                parent_id=self.parent.id,
                semantics=CardSemantics(kind="test"),
            ),
        )
        self.client = TestClient(
            main.app,
            headers={
                "X-Agent-Board-Provider": "codex",
                "X-Agent-Board-Session": "mutation-error-test",
                "X-Agent-Board-Commit": "0" * 40,
            },
        )

    def tearDown(self):
        db.close()

    def test_readable_card_accepts_direct_and_bulk_updates(self):
        card_url = f"/api/boards/{self.board.id}/cards/{self.card.id}"
        self.assertEqual(self.client.get(card_url).status_code, 200)

        direct = self.client.patch(card_url, json={"priority": "high"})
        bulk = self.client.post(
            f"/api/boards/{self.board.id}/cards/bulk",
            json={
                "operations": [
                    {"op": "update", "card_id": self.card.id, "labels": ["native-send"]}
                ]
            },
        )

        self.assertEqual(direct.status_code, 200)
        self.assertEqual(bulk.status_code, 200)

    def test_direct_create_accepts_a_bug_under_a_test_parent(self):
        response = self.client.post(
            f"/api/boards/{self.board.id}/cards",
            json={
                "title": "Observed product bug",
                "column_id": self.column_id,
                "parent_id": self.parent.id,
                "semantics": {"kind": "bug"},
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["parent_id"], self.parent.id)

    def test_constraint_rejection_is_not_reported_as_missing_card(self):
        card_url = f"/api/boards/{self.board.id}/cards/{self.card.id}"
        invalid_semantics = {
            "semantics": {"kind": "product_area", "catalog_lifecycle": "active"}
        }

        direct = self.client.patch(card_url, json=invalid_semantics)
        bulk = self.client.post(
            f"/api/boards/{self.board.id}/cards/bulk",
            json={
                "operations": [
                    {"op": "update", "card_id": self.card.id, **invalid_semantics}
                ]
            },
        )

        self.assertEqual(direct.status_code, 409)
        self.assertIn("semantic", direct.json()["detail"])
        self.assertEqual(bulk.status_code, 422)
        self.assertNotIn("no card", bulk.json()["detail"]["error"])

    def test_missing_card_still_returns_not_found(self):
        missing = "deadbeef0000"
        direct = self.client.patch(
            f"/api/boards/{self.board.id}/cards/{missing}",
            json={"title": "Nope"},
        )
        bulk = self.client.post(
            f"/api/boards/{self.board.id}/cards/bulk",
            json={"operations": [{"op": "update", "card_id": missing, "title": "Nope"}]},
        )

        self.assertEqual(direct.status_code, 404)
        self.assertEqual(bulk.status_code, 422)
        self.assertIn("no card", bulk.json()["detail"]["error"])


if __name__ == "__main__":
    unittest.main()
