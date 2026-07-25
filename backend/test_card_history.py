import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-hist-test-")

import board_store
import card_store
from models import CreateCard, MoveCard, UpdateCard


class CardHistoryTest(unittest.TestCase):
    def setUp(self):
        # The legacy twin is inherited from the surrounding Better Agent
        # session, so a test that wants "no session" must clear both.
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-alpha"
        self.board = board_store.create_board("H", "", ["Open", "Done"])
        self.open_id = self.board.columns[0].id
        self.done_id = self.board.columns[1].id

    def tearDown(self):
        os.environ.pop("BETTER_AGENT_APP_SESSION_ID", None)
        os.environ.pop("BETTER_AGENT_PROVIDER_KIND", None)

    def _card(self, **kwargs):
        return card_store.create_card(
            self.board.id, CreateCard(title="T", column_id=self.open_id, **kwargs)
        )

    def test_creation_is_attributed_to_the_acting_session(self):
        card = self._card()
        self.assertEqual(len(card.session_history), 1)
        entry = card.session_history[0]
        self.assertEqual(entry.session_id, "sess-alpha")
        self.assertEqual(entry.action, "created")
        self.assertIsNotNone(entry.timestamp)

    def test_update_records_only_the_fields_that_changed(self):
        card = self._card(body="before")
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-beta"

        updated = card_store.update_card(
            self.board.id, card.id, UpdateCard(body="after", title="T")
        )

        entry = updated.session_history[-1]
        self.assertEqual(entry.session_id, "sess-beta")
        self.assertEqual(entry.action, "updated")
        changed = {c.field: (c.before, c.after) for c in entry.changes}
        self.assertEqual(changed, {"body": ("before", "after")})

    def test_a_no_op_update_records_nothing(self):
        card = self._card(body="same")
        before = len(card.session_history)

        updated = card_store.update_card(self.board.id, card.id, UpdateCard(body="same"))

        self.assertEqual(len(updated.session_history), before)

    def test_move_records_the_lane_change_and_cascades_to_subtasks(self):
        parent = self._card()
        child = card_store.create_card(
            self.board.id,
            CreateCard(title="child", column_id=self.open_id, parent_id=parent.id),
        )

        moved = card_store.move_card(self.board.id, parent.id, MoveCard(column_id=self.done_id))

        entry = moved.session_history[-1]
        self.assertEqual(entry.action, "moved")
        changed = {c.field: (c.before, c.after) for c in entry.changes}
        self.assertEqual(changed, {"column_id": (self.open_id, self.done_id)})

        child_after = card_store.get_card(self.board.id, child.id)
        child_entry = child_after.session_history[-1]
        self.assertEqual(child_entry.action, "moved")
        self.assertEqual(
            {c.field for c in child_entry.changes}, {"column_id"}
        )

    def test_history_survives_a_round_trip_to_disk(self):
        card = self._card(body="one")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="two"))

        reread = card_store.get_card(self.board.id, card.id)

        self.assertEqual([e.action for e in reread.session_history], ["created", "updated"])
        self.assertEqual(reread.session_history[-1].changes[0].after, "two")

    def test_unknown_session_is_recorded_as_empty_not_guessed(self):
        os.environ.pop("BETTER_AGENT_APP_SESSION_ID", None)
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)

        card = self._card()

        self.assertEqual(card.session_history[0].session_id, "")

    def test_long_values_are_truncated_so_history_stays_small(self):
        card = self._card(body="x")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="y" * 5000))

        after = card_store.get_card(self.board.id, card.id).session_history[-1].changes[0].after
        self.assertLess(len(after), 500)
        self.assertTrue(after.endswith("…"))

    def test_history_is_capped(self):
        card = self._card(body="0")
        for i in range(1, card_store._HISTORY_LIMIT + 20):
            card_store.update_card(self.board.id, card.id, UpdateCard(body=str(i)))

        reread = card_store.get_card(self.board.id, card.id)
        self.assertEqual(len(reread.session_history), card_store._HISTORY_LIMIT)
        # The most recent edits are the ones kept.
        self.assertEqual(reread.session_history[-1].changes[0].after, str(card_store._HISTORY_LIMIT + 19))

    def test_closed_at_stamp_is_not_reported_as_a_user_edit(self):
        board = board_store.create_board("C", "", ["Open", "Done"])
        card = card_store.create_card(
            board.id, CreateCard(title="c", column_id=board.columns[0].id)
        )

        moved = card_store.move_card(board.id, card.id, MoveCard(column_id=board.columns[1].id))

        fields = {c.field for c in moved.session_history[-1].changes}
        self.assertNotIn("metadata", fields)


if __name__ == "__main__":
    unittest.main()
