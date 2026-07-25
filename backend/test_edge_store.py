import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

import board_store
import card_store
import edge_store
from models import CreateCard, CreateEdge


def _board_with_two_cards():
    board = board_store.create_board("T", "", ["Open"])
    column_id = board.columns[0].id
    a = card_store.create_card(board.id, CreateCard(title="A", column_id=column_id))
    b = card_store.create_card(board.id, CreateCard(title="B", column_id=column_id))
    return board, a, b


class EdgeStoreTest(unittest.TestCase):
    def test_create_edge_persists_arbitrary_type(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(
            board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="blocked_by", label="waiting on quota"),
        )
        self.assertIsNotNone(edge)
        self.assertEqual(edge.type, "blocked_by")

        reloaded = edge_store.get_edge(board.id, edge.id)
        self.assertEqual(reloaded.from_card_id, a.id)
        self.assertEqual(reloaded.to_card_id, b.id)
        self.assertEqual(reloaded.label, "waiting on quota")

    def test_create_edge_rejects_unknown_card(self):
        board, a, _ = _board_with_two_cards()
        edge = edge_store.create_edge(
            board.id, CreateEdge(from_card_id=a.id, to_card_id="doesnotexist", type="relates_to"),
        )
        self.assertIsNone(edge)

    def test_list_edges_filters_by_card_and_type(self):
        board, a, b = _board_with_two_cards()
        c = card_store.create_card(board.id, CreateCard(title="C", column_id=board.columns[0].id))
        edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="blocked_by"))
        edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=c.id, type="duplicates"))

        for_a = edge_store.list_edges(board.id, card_id=a.id)
        self.assertEqual(len(for_a), 2)

        for_b = edge_store.list_edges(board.id, card_id=b.id)
        self.assertEqual(len(for_b), 1)
        self.assertEqual(for_b[0].to_card_id, b.id)

        blocked_only = edge_store.list_edges(board.id, type="blocked_by")
        self.assertEqual(len(blocked_only), 1)

    def test_delete_edge(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id))
        self.assertTrue(edge_store.delete_edge(board.id, edge.id))
        self.assertIsNone(edge_store.get_edge(board.id, edge.id))
        self.assertFalse(edge_store.delete_edge(board.id, edge.id))

    def test_deleting_a_card_removes_its_edges(self):
        board, a, b = _board_with_two_cards()
        edge = edge_store.create_edge(board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="blocks"))
        self.assertTrue(card_store.delete_card(board.id, a.id))
        self.assertIsNone(edge_store.get_edge(board.id, edge.id))
        self.assertEqual(edge_store.list_edges(board.id), [])


if __name__ == "__main__":
    unittest.main()
