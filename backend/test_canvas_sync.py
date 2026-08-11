import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-canvas-test-")

import board_store
import canvas_sync
import card_store
import edge_store
from models import CardSemantics, CreateCard, CreateEdge


class CanvasProjectionTest(unittest.TestCase):
    def test_projection_includes_sparse_semantics_and_typed_relationships(self):
        board = board_store.create_board("Mapped", "", ["Open", "Done"])
        area = card_store.create_card(board.id, CreateCard(
            title="Auditability",
            column_id=board.columns[0].id,
            semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
        ))
        feature = card_store.create_card(board.id, CreateCard(
            title="Change history",
            column_id=board.columns[0].id,
            parent_id=area.id,
            semantics=CardSemantics(kind="feature", catalog_lifecycle="active"),
        ))
        requirement = card_store.create_card(board.id, CreateCard(
            title="Trace every change",
            column_id=board.columns[0].id,
            parent_id=feature.id,
            semantics=CardSemantics(
                kind="requirement",
                catalog_lifecycle="active",
                outcome="Every change is attributable",
                acceptance_criteria=["Provider and session are recorded"],
            ),
        ))
        delivery = card_store.create_card(board.id, CreateCard(
            title="Add attribution",
            column_id=board.columns[0].id,
            semantics=CardSemantics(kind="task"),
        ))
        relationship = edge_store.create_edge(board.id, CreateEdge(
            from_card_id=delivery.id,
            to_card_id=requirement.id,
            type="implements",
        ))

        payload = canvas_sync.build_graph_payload(board.id)

        requirement_node = next(
            node for node in payload["nodes"] if node["external_id"] == requirement.id
        )
        self.assertEqual(requirement_node["semantics"]["kind"], "requirement")
        self.assertNotIn("ownership", requirement_node["semantics"])
        self.assertIn("Outcome: Every change is attributable", requirement_node["body"])
        self.assertIn({
            "external_id": relationship.id,
            "from_id": delivery.id,
            "to_id": requirement.id,
            "label": "implements",
            "type": "implements",
        }, payload["edges"])


if __name__ == "__main__":
    unittest.main()
