import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

from agent_board import board_store
from agent_board import card_store
from agent_board import edge_store
from agent_board.card_semantics import CardSemantics
from agent_board.models import CreateCard, CreateEdge, UpdateCard


def _board_with_two_cards():
    board = board_store.create_board("T", "", ["Open"])
    column_id = board.columns[0].id
    semantics = CardSemantics(kind="task")
    a = card_store.create_card(board.id, CreateCard(title="A", column_id=column_id, semantics=semantics))
    b = card_store.create_card(board.id, CreateCard(title="B", column_id=column_id, semantics=semantics))
    return board, a, b


class EdgeStoreTest(unittest.TestCase):
    def test_create_edge_persists_controlled_type(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(
            board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on", label="waiting on quota"),
        )
        self.assertIsNotNone(edge)
        self.assertEqual(edge.type, "depends_on")

        reloaded = edge_store.get_edge(board.id, edge.id)
        self.assertEqual(reloaded.from_card_id, a.id)
        self.assertEqual(reloaded.to_card_id, b.id)
        self.assertEqual(reloaded.label, "waiting on quota")

    def test_create_edge_rejects_unknown_card(self):
        board, a, _ = _board_with_two_cards()
        with self.assertRaises(edge_store.UnknownEndpoint) as caught:
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=a.id, to_card_id="doesnotexist", type="depends_on"),
            )
        self.assertIn("doesnotexist", str(caught.exception))

    def test_create_edge_rejects_unknown_board(self):
        board, a, b = _board_with_two_cards()
        with self.assertRaises(edge_store.UnknownEndpoint):
            edge_store.create_edge(
                "nosuchboard",
                CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"),
            )

    def test_list_edges_filters_by_card_and_type(self):
        board, a, b = _board_with_two_cards()
        c = card_store.create_card(board.id, CreateCard(
            title="C", column_id=board.columns[0].id, semantics=CardSemantics(kind="task")
        ))
        edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"))
        edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=c.id, type="supersedes"))

        for_a = edge_store.list_edges(board.id, card_id=a.id)
        self.assertEqual(len(for_a), 2)

        for_b = edge_store.list_edges(board.id, card_id=b.id)
        self.assertEqual(len(for_b), 1)
        self.assertEqual(for_b[0].to_card_id, b.id)

        blocked_only = edge_store.list_edges(board.id, type="depends_on")
        self.assertEqual(len(blocked_only), 1)

    def test_delete_edge(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"))
        self.assertTrue(edge_store.delete_edge(board.id, edge.id))
        self.assertIsNone(edge_store.get_edge(board.id, edge.id))
        self.assertFalse(edge_store.delete_edge(board.id, edge.id))

    def test_deleting_a_card_removes_its_edges(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"))
        self.assertTrue(card_store.delete_card(board.id, a.id))
        self.assertIsNone(edge_store.get_edge(board.id, edge.id))
        self.assertEqual(edge_store.list_edges(board.id), [])

    def test_rejects_duplicate_self_and_dependency_cycle(self):
        board, a, b = _board_with_two_cards()
        with self.assertRaises(edge_store.SelfLoop):
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=a.id, to_card_id=a.id, type="depends_on"),
            )
        self.assertIsNotNone(edge_store.create_edge(
            board.id,
            CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"),
        ))
        with self.assertRaises(edge_store.DuplicateEdge):
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on"),
            )
        with self.assertRaises(edge_store.DependencyCycle):
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=b.id, to_card_id=a.id, type="depends_on"),
            )

    def test_enforces_direction_when_card_kinds_are_known(self):
        board, delivery, _ = _board_with_two_cards()
        delivery = card_store.update_card(
            board.id,
            delivery.id,
            UpdateCard(semantics=CardSemantics(kind="task")),
        )
        area = card_store.create_card(
            board.id,
            CreateCard(
                title="Area",
                column_id=board.columns[0].id,
                semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
            ),
        )
        feature = card_store.create_card(
            board.id,
            CreateCard(
                title="Feature",
                column_id=board.columns[0].id,
                parent_id=area.id,
                semantics=CardSemantics(kind="feature", catalog_lifecycle="active"),
            ),
        )
        requirement = card_store.create_card(
            board.id,
            CreateCard(
                title="Requirement",
                column_id=board.columns[0].id,
                parent_id=feature.id,
                semantics=CardSemantics(
                    kind="requirement",
                    catalog_lifecycle="active",
                    outcome="Behavior exists",
                    acceptance_criteria=["Behavior is observable"],
                ),
            ),
        )
        self.assertIsNotNone(edge_store.create_edge(
            board.id,
            CreateEdge(
                from_card_id=delivery.id,
                to_card_id=requirement.id,
                type="implements",
            ),
        ))
        with self.assertRaises(edge_store.InvalidDirection) as caught:
            edge_store.create_edge(
                board.id,
                CreateEdge(
                    from_card_id=requirement.id,
                    to_card_id=delivery.id,
                    type="implements",
                ),
            )
        # The message names the edge type and both kinds, not "invalid id".
        message = str(caught.exception)
        self.assertIn("implements", message)
        self.assertIn("requirement", message)
        self.assertIn("task", message)

    def test_rejects_controlled_relationships_between_untyped_cards(self):
        board = board_store.create_board("Untyped", "", ["Open"])
        first = card_store.create_card(board.id, CreateCard(title="A", column_id=board.columns[0].id))
        second = card_store.create_card(board.id, CreateCard(title="B", column_id=board.columns[0].id))
        with self.assertRaises(edge_store.MissingSemanticKind):
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=first.id, to_card_id=second.id, type="depends_on"),
            )

    def test_defines_requires_the_same_parent_relationship(self):
        board = board_store.create_board("Catalog", "", ["Open"])
        area = card_store.create_card(board.id, CreateCard(
            title="Area", column_id=board.columns[0].id,
            semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
        ))
        other_area = card_store.create_card(board.id, CreateCard(
            title="Other area", column_id=board.columns[0].id,
            semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
        ))
        feature = card_store.create_card(board.id, CreateCard(
            title="Feature", column_id=board.columns[0].id,
            parent_id=area.id,
            semantics=CardSemantics(kind="feature", catalog_lifecycle="active"),
        ))
        with self.assertRaises(edge_store.ParentRelationshipRequired):
            edge_store.create_edge(
                board.id,
                CreateEdge(from_card_id=other_area.id, to_card_id=feature.id, type="defines"),
            )

    def test_kind_change_cannot_invalidate_an_incident_relationship(self):
        board, delivery, _ = _board_with_two_cards()
        area = card_store.create_card(board.id, CreateCard(
            title="Area", column_id=board.columns[0].id,
            semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
        ))
        feature = card_store.create_card(board.id, CreateCard(
            title="Feature", column_id=board.columns[0].id, parent_id=area.id,
            semantics=CardSemantics(kind="feature", catalog_lifecycle="active"),
        ))
        requirement = card_store.create_card(board.id, CreateCard(
            title="Requirement", column_id=board.columns[0].id, parent_id=feature.id,
            semantics=CardSemantics(
                kind="requirement", catalog_lifecycle="active", outcome="Works",
                acceptance_criteria=["Observable"],
            ),
        ))
        self.assertIsNotNone(edge_store.create_edge(
            board.id,
            CreateEdge(from_card_id=delivery.id, to_card_id=requirement.id, type="implements"),
        ))
        self.assertIsNone(card_store.update_card(
            board.id, delivery.id, UpdateCard(semantics=CardSemantics(kind="test"))
        ))


if __name__ == "__main__":
    unittest.main()
