import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-bulk-test-")

import board_store
import card_bulk
import card_history
import card_store
import edge_store
from card_semantics import CardSemantics
from models import AddNote, BulkCards, CreateCard, CreateEdge, MAX_BULK_OPERATIONS, MoveCard
from pydantic import ValidationError


class BulkCardsTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-bulk"
        self.board = board_store.create_board("B", "", ["Open", "Done"])
        self.open_id = self.board.columns[0].id
        self.done_id = self.board.columns[1].id

    def tearDown(self):
        os.environ.pop("BETTER_AGENT_APP_SESSION_ID", None)

    def _bulk(self, operations):
        return card_bulk.bulk_cards(self.board.id, BulkCards(operations=operations))

    def _card(self, title="T", **kwargs):
        kwargs.setdefault("semantics", CardSemantics(kind="task"))
        return card_store.create_card(
            self.board.id, CreateCard(title=title, column_id=self.open_id, **kwargs)
        )

    def _titles(self):
        return {c.title for c in card_store.list_cards(self.board.id)}

    def test_one_call_creates_many_cards(self):
        result = self._bulk([
            {"op": "create", "title": f"C{i}", "column_id": self.open_id}
            for i in range(10)
        ])

        self.assertTrue(result.applied)
        self.assertEqual(len(result.results), 10)
        self.assertEqual(self._titles(), {f"C{i}" for i in range(10)})

    def test_a_ref_lets_a_later_op_target_a_card_the_batch_just_created(self):
        result = self._bulk([
            {"op": "create", "ref": "parent", "title": "Parent", "column_id": self.open_id},
            {"op": "create", "title": "Child", "column_id": self.open_id, "parent_id": "@parent"},
            {"op": "note", "card_id": "@parent", "text": "one round-trip"},
        ])

        self.assertTrue(result.applied)
        cards = {c.title: c for c in card_store.list_cards(self.board.id)}
        self.assertEqual(cards["Child"].parent_id, cards["Parent"].id)
        self.assertEqual([n.text for n in cards["Parent"].notes], ["one round-trip"])

    def test_mixed_operations_apply_in_order(self):
        stale = self._card("stale")
        keep = self._card("keep")

        result = self._bulk([
            {"op": "create", "title": "fresh", "column_id": self.open_id},
            {"op": "update", "card_id": keep.id, "priority": "critical"},
            {"op": "move", "card_id": keep.id, "column_id": self.done_id},
            {"op": "delete", "card_id": stale.id},
        ])

        self.assertTrue(result.applied)
        self.assertEqual([r.op for r in result.results], ["create", "update", "move", "delete"])
        cards = {c.title: c for c in card_store.list_cards(self.board.id)}
        self.assertEqual(set(cards), {"fresh", "keep"})
        self.assertEqual(cards["keep"].priority, "critical")
        self.assertEqual(cards["keep"].column_id, self.done_id)

    def test_a_failing_op_rolls_back_every_earlier_op(self):
        """A half-applied batch is worse than a rejected one: the caller cannot
        tell which half landed."""
        before = self._titles()

        result = self._bulk([
            {"op": "create", "title": "first", "column_id": self.open_id},
            {"op": "create", "title": "second", "column_id": self.open_id},
            {"op": "update", "card_id": "nosuchcard0", "title": "boom"},
            {"op": "create", "title": "third", "column_id": self.open_id},
        ])

        self.assertFalse(result.applied)
        self.assertEqual(result.failed_index, 2)
        self.assertIn("no card", result.error)
        self.assertEqual(self._titles(), before)

    def test_a_rolled_back_batch_writes_nothing_to_the_journal(self):
        card = self._card("solo")
        before = len(card_history.read_events(self.board.id))

        self._bulk([
            {"op": "update", "card_id": card.id, "title": "renamed"},
            {"op": "delete", "card_id": "nosuchcard0"},
        ])

        self.assertEqual(len(card_history.read_events(self.board.id)), before)
        self.assertEqual(card_store.get_card(self.board.id, card.id).title, "solo")

    def test_an_unknown_ref_is_refused_rather_than_guessed(self):
        result = self._bulk([
            {"op": "create", "title": "orphan", "column_id": self.open_id, "parent_id": "@missing"},
        ])

        self.assertFalse(result.applied)
        self.assertIn("unknown ref", result.error)
        self.assertEqual(self._titles(), set())

    def test_a_duplicate_ref_is_refused(self):
        result = self._bulk([
            {"op": "create", "ref": "dup", "title": "A", "column_id": self.open_id},
            {"op": "create", "ref": "dup", "title": "B", "column_id": self.open_id},
        ])

        self.assertFalse(result.applied)
        self.assertEqual(result.failed_index, 1)
        self.assertIn("duplicate ref", result.error)

    def test_every_op_still_records_its_own_version(self):
        result = self._bulk([
            {"op": "create", "ref": "c", "title": "tracked", "column_id": self.open_id},
            {"op": "update", "card_id": "@c", "body": "filled in"},
            {"op": "move", "card_id": "@c", "column_id": self.done_id},
        ])
        card_id = result.results[0].card_id

        versions = card_history.card_versions(self.board.id, card_id)

        self.assertEqual([v.action for v in versions], ["created", "updated", "moved"])
        self.assertTrue(all(v.session_id == "sess-bulk" for v in versions))

    def test_bulk_delete_cleans_up_edges(self):
        a = self._card("a")
        b = self._card("b")
        edge_store.create_edge(
            self.board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on")
        )
        self.assertEqual(len(edge_store.list_edges(self.board.id)), 1)

        result = self._bulk([{"op": "delete", "card_id": a.id}])

        self.assertTrue(result.applied)
        self.assertEqual(edge_store.list_edges(self.board.id), [])

    def test_an_empty_batch_is_refused(self):
        with self.assertRaises(ValidationError):
            BulkCards(operations=[])

    def test_an_oversized_batch_is_refused(self):
        with self.assertRaises(ValidationError):
            BulkCards(operations=[
                {"op": "create", "title": "x", "column_id": self.open_id}
                for _ in range(MAX_BULK_OPERATIONS + 1)
            ])

    def test_an_unknown_op_is_refused_not_coerced(self):
        with self.assertRaises(ValidationError):
            BulkCards(operations=[{"op": "truncate_everything", "card_id": "x"}])

    def test_update_distinguishes_an_omitted_parent_from_an_explicit_null(self):
        parent = self._card("parent")
        child = self._card("child", parent_id=parent.id)

        self._bulk([{"op": "update", "card_id": child.id, "title": "renamed"}])
        self.assertEqual(card_store.get_card(self.board.id, child.id).parent_id, parent.id)

        self._bulk([{"op": "update", "card_id": child.id, "parent_id": None}])
        self.assertIsNone(card_store.get_card(self.board.id, child.id).parent_id)

    def test_a_batch_cannot_build_a_parent_cycle(self):
        """A cycle wedges every later walk of the tree, so it is refused rather
        than persisted."""
        result = self._bulk([
            {"op": "create", "ref": "a", "title": "A", "column_id": self.open_id},
            {"op": "create", "ref": "b", "title": "B", "column_id": self.open_id, "parent_id": "@a"},
            {"op": "update", "card_id": "@a", "parent_id": "@b"},
        ])

        self.assertFalse(result.applied)
        self.assertEqual(result.failed_index, 2)
        self.assertEqual(self._titles(), set())

    def test_a_card_cannot_become_its_own_parent(self):
        card = self._card("self")

        result = self._bulk([{"op": "update", "card_id": card.id, "parent_id": card.id}])

        self.assertFalse(result.applied)
        self.assertIsNone(card_store.get_card(self.board.id, card.id).parent_id)

    def test_a_cycle_stays_impossible_through_the_single_card_api(self):
        parent = self._card("p")
        child = self._card("c", parent_id=parent.id)

        from models import UpdateCard
        self.assertIsNone(
            card_store.update_card(self.board.id, parent.id, UpdateCard(parent_id=child.id))
        )
        self.assertIsNone(card_store.get_card(self.board.id, parent.id).parent_id)

    def test_an_update_to_an_unknown_column_is_refused_not_ignored(self):
        """Silently keeping the old lane would report a move that never happened."""
        card = self._card("stays")

        result = self._bulk([{"op": "update", "card_id": card.id, "column_id": "nosuchcol00"}])

        self.assertFalse(result.applied)
        self.assertIn("unknown column", result.error)
        self.assertEqual(card_store.get_card(self.board.id, card.id).column_id, self.open_id)

    def test_an_update_can_target_a_column_by_ref_free_id(self):
        card = self._card("moves")

        result = self._bulk([{"op": "update", "card_id": card.id, "column_id": self.done_id}])

        self.assertTrue(result.applied)
        self.assertEqual(card_store.get_card(self.board.id, card.id).column_id, self.done_id)

    def test_deleting_a_parent_promotes_its_children_instead_of_dangling_them(self):
        parent = self._card("parent")
        child = self._card("child", parent_id=parent.id)

        result = self._bulk([{"op": "delete", "card_id": parent.id}])

        self.assertTrue(result.applied)
        self.assertIsNone(card_store.get_card(self.board.id, child.id).parent_id)

    def test_deleting_many_cards_drops_every_edge_in_the_same_commit(self):
        a, b, c = self._card("a"), self._card("b"), self._card("c")
        for src, dst in ((a.id, b.id), (b.id, c.id), (c.id, a.id)):
            edge_store.create_edge(
                self.board.id, CreateEdge(from_card_id=src, to_card_id=dst, type="relates_to")
            )
        self.assertEqual(len(edge_store.list_edges(self.board.id)), 3)

        result = self._bulk([
            {"op": "delete", "card_id": a.id},
            {"op": "delete", "card_id": b.id},
        ])

        self.assertTrue(result.applied)
        self.assertEqual(edge_store.list_edges(self.board.id), [])
        self.assertEqual(self._titles(), {"c"})

    def test_a_rolled_back_batch_leaves_edges_untouched(self):
        a, b = self._card("a"), self._card("b")
        edge_store.create_edge(
            self.board.id, CreateEdge(from_card_id=a.id, to_card_id=b.id, type="depends_on")
        )

        result = self._bulk([
            {"op": "delete", "card_id": a.id},
            {"op": "delete", "card_id": "nosuchcard0"},
        ])

        self.assertFalse(result.applied)
        self.assertEqual(len(edge_store.list_edges(self.board.id)), 1)
        self.assertEqual(self._titles(), {"a", "b"})

    def test_bulk_matches_the_same_ops_done_one_at_a_time(self):
        """Bulk and single-card writes share one implementation, so the board
        they produce must be identical."""
        single = board_store.create_board("S", "", ["Open", "Done"])
        s_open, s_done = single.columns[0].id, single.columns[1].id
        parent = card_store.create_card(
            single.id,
            CreateCard(title="P", body="epic", column_id=s_open, priority="high", labels=["x"]),
        )
        card_store.create_card(
            single.id,
            CreateCard(title="C", column_id=s_open, parent_id=parent.id, metadata={"k": "v"}),
        )
        card_store.add_note(single.id, parent.id, AddNote(kind="note", text="hello"))
        card_store.move_card(single.id, parent.id, MoveCard(column_id=s_done))

        self._bulk([
            {"op": "create", "ref": "p", "title": "P", "body": "epic",
             "column_id": self.open_id, "priority": "high", "labels": ["x"]},
            {"op": "create", "title": "C", "column_id": self.open_id,
             "parent_id": "@p", "metadata": {"k": "v"}},
            {"op": "note", "card_id": "@p", "text": "hello", "kind": "note"},
            {"op": "move", "card_id": "@p", "column_id": self.done_id},
        ])

        def shape(board_id, done_id):
            cards = card_store.list_cards(board_id)
            by_id = {c.id: c for c in cards}
            return sorted(
                (
                    c.title, c.body, c.column_id == done_id, c.position, c.priority,
                    tuple(c.labels), tuple(sorted(c.metadata.items())),
                    by_id[c.parent_id].title if c.parent_id else None,
                    tuple((n.kind, n.text) for n in c.notes),
                    tuple(e.action for e in c.session_history),
                )
                for c in cards
            )

        self.assertEqual(shape(self.board.id, self.done_id), shape(single.id, s_done))


if __name__ == "__main__":
    unittest.main()
