import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

import board_store
import card_store
from models import CreateCard, MoveCard


class ClosedCardPruningTest(unittest.TestCase):
    def setUp(self):
        self.original_now = card_store._now
        self.now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        card_store._now = lambda: self.now
        self.board = board_store.create_board("T", "", ["Open", "Closed"])
        self.open_column = self.board.columns[0].id
        self.closed_column = self.board.columns[1].id

    def tearDown(self):
        card_store._now = self.original_now

    def test_prunes_cards_three_days_after_entering_closed_lane(self):
        card = card_store.create_card(
            self.board.id,
            CreateCard(title="Done", column_id=self.open_column),
        )

        closed = card_store.move_card(
            self.board.id,
            card.id,
            MoveCard(column_id=self.closed_column),
        )

        self.assertEqual(closed.metadata[card_store.CLOSED_AT_METADATA_KEY], self.now.isoformat())

        self.now += timedelta(days=3, seconds=1)

        self.assertEqual(card_store.list_cards(self.board.id), [])
        self.assertIsNone(card_store.get_card(self.board.id, card.id))

    def test_reopened_cards_are_not_pruned_by_old_closed_timestamp(self):
        card = card_store.create_card(
            self.board.id,
            CreateCard(title="Reopen", column_id=self.open_column),
        )
        card_store.move_card(self.board.id, card.id, MoveCard(column_id=self.closed_column))

        self.now += timedelta(days=1)
        reopened = card_store.move_card(self.board.id, card.id, MoveCard(column_id=self.open_column))

        self.assertNotIn(card_store.CLOSED_AT_METADATA_KEY, reopened.metadata)

        self.now += timedelta(days=3, seconds=1)

        self.assertEqual([card.title for card in card_store.list_cards(self.board.id)], ["Reopen"])


if __name__ == "__main__":
    unittest.main()
