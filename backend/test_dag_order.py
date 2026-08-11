import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

import board_store
import card_store
import edge_store
from card_semantics import CardSemantics
from models import CreateCard, CreateEdge, MoveCard, UpdateCard


def _backlog_board(*titles: str):
    board = board_store.create_board("T", "", ["Open Items", "Done"])
    column_id = board.columns[0].id
    cards = [card_store.create_card(
        board.id,
        CreateCard(title=t, column_id=column_id, semantics=CardSemantics(kind="task")),
    ) for t in titles]
    return board, column_id, cards


def _titles(board_id: str, column_id: str) -> list[str]:
    return [c.title for c in card_store.list_cards(board_id, column_id=column_id)]


def _block(board_id: str, blocker, blocked):
    return edge_store.create_edge(
        board_id,
        CreateEdge(
            from_card_id=blocked.id,
            to_card_id=blocker.id,
            type="depends_on",
        ),
    )


class DagOrderTest(unittest.TestCase):
    def test_blocker_rises_above_what_it_blocks(self):
        board, column_id, (a, b) = _backlog_board("A", "B")
        self.assertEqual(_titles(board.id, column_id), ["A", "B"])

        _block(board.id, blocker=b, blocked=a)
        self.assertEqual(_titles(board.id, column_id), ["B", "A"])

    def test_depends_on_points_from_dependent_to_dependency(self):
        board, column_id, (a, b) = _backlog_board("A", "B")
        _block(board.id, blocker=b, blocked=a)
        self.assertEqual(_titles(board.id, column_id), ["B", "A"])

    def test_only_the_blocked_card_moves(self):
        """Everything unconstrained keeps its order; the blocked card is the
        only one pushed, and only as far as its blocker demands."""
        board, column_id, (a, b, c, d) = _backlog_board("A", "B", "C", "D")
        _block(board.id, blocker=d, blocked=b)
        self.assertEqual(_titles(board.id, column_id), ["A", "C", "D", "B"])

    def test_chain_orders_transitively(self):
        board, column_id, (a, b, c) = _backlog_board("A", "B", "C")
        _block(board.id, blocker=b, blocked=a)
        _block(board.id, blocker=c, blocked=b)
        self.assertEqual(_titles(board.id, column_id), ["C", "B", "A"])

    def test_cycle_is_rejected_without_losing_cards(self):
        board, column_id, (a, b) = _backlog_board("A", "B")
        self.assertIsNotNone(_block(board.id, blocker=a, blocked=b))
        self.assertIsNone(_block(board.id, blocker=b, blocked=a))
        self.assertEqual(sorted(_titles(board.id, column_id)), ["A", "B"])

    def test_non_blocking_edge_types_do_not_reorder(self):
        board, column_id, (a, b) = _backlog_board("A", "B")
        edge_store.create_edge(
            board.id,
            CreateEdge(from_card_id=b.id, to_card_id=a.id, type="supersedes"),
        )
        self.assertEqual(_titles(board.id, column_id), ["A", "B"])

    def test_deleting_the_edge_leaves_the_resolved_order_in_place(self):
        """Position is the one order there is. Dropping a constraint frees the
        cards to be moved again; it does not resurrect a pre-blocking order."""
        board, column_id, (a, b) = _backlog_board("A", "B")
        edge = _block(board.id, blocker=b, blocked=a)
        self.assertEqual(_titles(board.id, column_id), ["B", "A"])

        edge_store.delete_edge(board.id, edge.id)
        self.assertEqual(_titles(board.id, column_id), ["B", "A"])

        card_store.update_card(board.id, a.id, UpdateCard(position=0))
        self.assertEqual(_titles(board.id, column_id), ["A", "B"])

    def test_only_the_dependency_ordered_column_is_sorted(self):
        board, open_items, (a, b) = _backlog_board("A", "B")
        done = board.columns[1].id
        card_store.move_card(board.id, a.id, MoveCard(column_id=done))
        card_store.move_card(board.id, b.id, MoveCard(column_id=done))
        _block(board.id, blocker=b, blocked=a)
        self.assertEqual(_titles(board.id, done), ["A", "B"])

    def test_edges_out_of_the_column_do_not_reorder_it(self):
        board, open_items, (a, b) = _backlog_board("A", "B")
        done = board.columns[1].id
        blocker = card_store.create_card(board.id, CreateCard(
            title="Z", column_id=done, semantics=CardSemantics(kind="task")
        ))
        _block(board.id, blocker=blocker, blocked=a)
        self.assertEqual(_titles(board.id, open_items), ["A", "B"])

    def test_card_moved_into_the_backlog_lands_in_dependency_order(self):
        board, open_items, (a,) = _backlog_board("A")
        done = board.columns[1].id
        blocker = card_store.create_card(board.id, CreateCard(
            title="Z", column_id=done, semantics=CardSemantics(kind="task")
        ))
        _block(board.id, blocker=blocker, blocked=a)

        card_store.move_card(board.id, blocker.id, MoveCard(column_id=open_items))
        self.assertEqual(_titles(board.id, open_items), ["Z", "A"])

    def test_manual_position_is_honoured_where_the_graph_allows(self):
        board, column_id, (a, b, c) = _backlog_board("A", "B", "C")
        _block(board.id, blocker=c, blocked=a)
        self.assertEqual(_titles(board.id, column_id), ["B", "C", "A"])

        card_store.update_card(board.id, c.id, UpdateCard(position=0))
        self.assertEqual(_titles(board.id, column_id), ["C", "B", "A"])

    def test_manual_position_cannot_outrank_a_blocker(self):
        board, column_id, (a, b, c) = _backlog_board("A", "B", "C")
        _block(board.id, blocker=c, blocked=a)

        card_store.update_card(board.id, a.id, UpdateCard(position=0))
        self.assertEqual(_titles(board.id, column_id), ["B", "C", "A"])

    def test_deleting_a_card_drops_its_ordering_constraint(self):
        board, column_id, (a, b, c) = _backlog_board("A", "B", "C")
        _block(board.id, blocker=c, blocked=a)
        self.assertEqual(_titles(board.id, column_id), ["B", "C", "A"])

        card_store.delete_card(board.id, c.id)
        self.assertEqual(_titles(board.id, column_id), ["B", "A"])


if __name__ == "__main__":
    unittest.main()
