import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-edge-api-errors-")

from fastapi.testclient import TestClient

from agent_board import board_store
from agent_board import card_store
from agent_board import db
from agent_board import main
from agent_board.card_semantics import CardSemantics
from agent_board.models import CreateCard


def _card(board_id, column_id, title, kind, parent_id=None, **semantics):
    return card_store.create_card(
        board_id,
        CreateCard(
            title=title,
            column_id=column_id,
            parent_id=parent_id,
            semantics=CardSemantics(kind=kind, **semantics),
        ),
    )


class EdgeApiErrorTests(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
            prefix="agent-board-edge-api-errors-"
        )
        db.close()
        self.board = board_store.create_board("Edges", "", ["Open"])
        self.column_id = self.board.columns[0].id
        self.client = TestClient(
            main.app,
            headers={
                "X-Agent-Board-Provider": "codex",
                "X-Agent-Board-Session": "edge-api-error-test",
                "X-Agent-Board-Commit": "0" * 40,
            },
        )

    def tearDown(self):
        db.close()

    def _post_edge(self, from_id, to_id, type, **extra):
        return self.client.post(
            f"/api/boards/{self.board.id}/edges",
            json={"from_card_id": from_id, "to_card_id": to_id, "type": type, **extra},
        )

    def test_unknown_card_is_404_not_generic_400(self):
        a = _card(self.board.id, self.column_id, "A", "task")
        response = self._post_edge(a.id, "deadbeef0000", "depends_on")
        self.assertEqual(response.status_code, 404)
        self.assertIn("deadbeef0000", response.json()["detail"])

    def test_untyped_cards_report_missing_kind_not_invalid_id(self):
        first = card_store.create_card(
            self.board.id, CreateCard(title="A", column_id=self.column_id)
        )
        second = card_store.create_card(
            self.board.id, CreateCard(title="B", column_id=self.column_id)
        )
        response = self._post_edge(first.id, second.id, "depends_on")
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("semantic kind", detail)
        self.assertNotIn("invalid", detail.lower())

    def test_invalid_direction_names_type_and_kinds(self):
        area = _card(self.board.id, self.column_id, "Area", "product_area", catalog_lifecycle="active")
        feature = _card(
            self.board.id, self.column_id, "Feature", "feature",
            parent_id=area.id, catalog_lifecycle="active",
        )
        requirement = _card(
            self.board.id, self.column_id, "Requirement", "requirement",
            parent_id=feature.id, catalog_lifecycle="active",
            outcome="Works", acceptance_criteria=["Observable"],
        )
        delivery = _card(self.board.id, self.column_id, "Delivery", "task")
        # implements only goes task/bug -> requirement, so requirement -> task is invalid.
        response = self._post_edge(requirement.id, delivery.id, "implements")
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("implements", detail)
        self.assertIn("requirement", detail)
        self.assertIn("task", detail)

    def test_self_loop_is_422(self):
        a = _card(self.board.id, self.column_id, "A", "task")
        response = self._post_edge(a.id, a.id, "depends_on")
        self.assertEqual(response.status_code, 422)
        self.assertIn("itself", response.json()["detail"])

    def test_duplicate_edge_is_409(self):
        a = _card(self.board.id, self.column_id, "A", "task")
        b = _card(self.board.id, self.column_id, "B", "task")
        self.assertEqual(self._post_edge(a.id, b.id, "depends_on").status_code, 201)
        response = self._post_edge(a.id, b.id, "depends_on")
        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["detail"])

    def test_dependency_cycle_is_409(self):
        a = _card(self.board.id, self.column_id, "A", "task")
        b = _card(self.board.id, self.column_id, "B", "task")
        self.assertEqual(self._post_edge(a.id, b.id, "depends_on").status_code, 201)
        response = self._post_edge(b.id, a.id, "depends_on")
        self.assertEqual(response.status_code, 409)
        self.assertIn("cycle", response.json()["detail"])

    def test_parent_relationship_required_is_422(self):
        area = _card(self.board.id, self.column_id, "Area", "product_area", catalog_lifecycle="active")
        other_area = _card(self.board.id, self.column_id, "Other", "product_area", catalog_lifecycle="active")
        feature = _card(
            self.board.id, self.column_id, "Feature", "feature",
            parent_id=area.id, catalog_lifecycle="active",
        )
        response = self._post_edge(other_area.id, feature.id, "defines")
        self.assertEqual(response.status_code, 422)
        self.assertIn("parent", response.json()["detail"])


class ChangeContextHeaderTests(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
            prefix="agent-board-edge-api-provider-"
        )
        db.close()
        self.board = board_store.create_board("Provider", "", ["Open"])
        self.column_id = self.board.columns[0].id
        self.card = _card(self.board.id, self.column_id, "Child", "test")
        # A client with no change-provider header at all.
        self.client = TestClient(main.app)

    def tearDown(self):
        db.close()

    def test_missing_provider_header_names_the_header(self):
        response = self.client.patch(
            f"/api/boards/{self.board.id}/cards/{self.card.id}",
            json={"priority": "high"},
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("X-Agent-Board-Provider", detail)


if __name__ == "__main__":
    unittest.main()
