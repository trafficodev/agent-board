import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-upsert-test-")

import board_store
import card_store
from models import AddNote, CreateCard


class CardUpsertTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board("B", "", ["Open", "Done"])
        self.open_id = self.board.columns[0].id
        self.done_id = self.board.columns[1].id

    def test_upsert_creates_then_updates_in_one_read_transaction(self):
        with patch.object(card_store, "_read_cards", wraps=card_store._read_cards) as read_cards:
            created = card_store.upsert_card(
                self.board.id,
                CreateCard(
                    external_id="assistant:req-1",
                    title="First",
                    column_id=self.open_id,
                    metadata={"version": 1},
                ),
            )
        self.assertEqual(read_cards.call_count, 1)
        self.assertIsNotNone(created)
        card_store.add_note(
            self.board.id,
            created.id,
            AddNote(kind="note", text="preserve me"),
        )

        with patch.object(card_store, "_read_cards", wraps=card_store._read_cards) as read_cards:
            updated = card_store.upsert_card(
                self.board.id,
                CreateCard(
                    external_id="assistant:req-1",
                    title="Second",
                    column_id=self.done_id,
                    metadata={"version": 2},
                ),
            )

        self.assertEqual(read_cards.call_count, 1)
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.title, "Second")
        self.assertEqual(updated.column_id, self.done_id)
        self.assertEqual(updated.metadata, {"version": 2})
        self.assertEqual([note.text for note in updated.notes], ["preserve me"])
        self.assertEqual(
            [card.external_id for card in card_store.list_cards(self.board.id)],
            ["assistant:req-1"],
        )

    def test_upsert_requires_external_identity(self):
        with self.assertRaisesRegex(ValueError, "external_id is required"):
            card_store.upsert_card(
                self.board.id,
                CreateCard(title="No identity", column_id=self.open_id),
            )


if __name__ == "__main__":
    unittest.main()
