import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-votes-test-")

import board_store
import card_history
import card_store
import db
import vote_store
from models import ChangeContext, CreateCard, UpdateCard


class ChangeVoteStoreTest(unittest.TestCase):
    def setUp(self):
        db.close()
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-votes-case-")
        self.board = board_store.create_board("Votes", "", ["Open"])
        self.context = ChangeContext.authored("codex", "session-a", "a" * 40)
        token = card_store.request_change_context.set(self.context)
        try:
            self.card = card_store.create_card(
                self.board.id,
                CreateCard(title="Claim", body="before", column_id=self.board.columns[0].id),
            )
            card_store.update_card(
                self.board.id,
                self.card.id,
                UpdateCard(body="after"),
            )
        finally:
            card_store.request_change_context.reset(token)
        self.target = next(
            item
            for item in card_history.card_change_items(self.board.id, self.card.id)
            if item.path == "/body"
        )

    def test_every_vote_is_appended_with_identity_timestamp_and_commit(self):
        first = vote_store.set_vote(
            self.board.id, self.card.id, self.target.id, 1, self.context
        )
        changed = ChangeContext.authored("codex", "session-a", "b" * 40)
        second = vote_store.set_vote(
            self.board.id, self.card.id, self.target.id, -1, changed
        )

        audit = vote_store.vote_audit(self.target.id)
        self.assertEqual([vote.id for vote in audit], [second.id, first.id])
        self.assertEqual([vote.reviewed_commit_sha for vote in audit], ["b" * 40, "a" * 40])
        self.assertTrue(all(vote.timestamp for vote in audit))

        summary = vote_store.vote_summaries([self.target.id], changed)[self.target.id]
        self.assertEqual((summary.up, summary.down, summary.current), (0, 1, -1))

    def test_latest_vote_per_native_session_counts(self):
        vote_store.set_vote(self.board.id, self.card.id, self.target.id, 1, self.context)
        other = ChangeContext.authored("claude", "session-b", "c" * 40)
        vote_store.set_vote(self.board.id, self.card.id, self.target.id, 1, other)
        vote_store.set_vote(self.board.id, self.card.id, self.target.id, -1, self.context)

        summary = vote_store.vote_summaries([self.target.id], other)[self.target.id]
        self.assertEqual((summary.up, summary.down, summary.current), (1, 1, 1))

    def test_unknown_or_non_voteable_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            vote_store.set_vote(
                self.board.id, self.card.id, "0" * 64, 1, self.context
            )

    def test_non_authored_context_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            vote_store.set_vote(
                self.board.id,
                self.card.id,
                self.target.id,
                1,
                ChangeContext.system("test"),
            )

    def test_votes_survive_connection_restart(self):
        vote_store.set_vote(self.board.id, self.card.id, self.target.id, 1, self.context)
        db.close()
        summary = vote_store.vote_summaries([self.target.id], self.context)[self.target.id]
        self.assertEqual((summary.up, summary.current), (1, 1))

    def test_board_deletion_removes_its_vote_audit(self):
        vote_store.set_vote(self.board.id, self.card.id, self.target.id, 1, self.context)

        board_store.delete_board(self.board.id)

        self.assertEqual(vote_store.vote_audit(self.target.id), [])

    def test_target_validation_and_vote_write_share_one_transaction(self):
        validation_started = threading.Event()
        release_validation = threading.Event()
        deletion_finished = threading.Event()
        original = card_history.card_change_items

        def paused_projection(*args, **kwargs):
            validation_started.set()
            self.assertTrue(release_validation.wait(2))
            return original(*args, **kwargs)

        def delete_board():
            try:
                return board_store.delete_board(self.board.id)
            finally:
                deletion_finished.set()
                db.close()

        def cast_vote():
            try:
                return vote_store.set_vote(
                    self.board.id,
                    self.card.id,
                    self.target.id,
                    1,
                    self.context,
                )
            finally:
                db.close()

        with patch.object(card_history, "card_change_items", paused_projection):
            with ThreadPoolExecutor(max_workers=2) as executor:
                vote_future = executor.submit(cast_vote)
                self.assertTrue(validation_started.wait(2))
                delete_future = executor.submit(delete_board)
                self.assertFalse(deletion_finished.wait(0.1))
                release_validation.set()
                vote_future.result(timeout=2)
                self.assertTrue(delete_future.result(timeout=2))

        self.assertEqual(vote_store.vote_audit(self.target.id), [])


if __name__ == "__main__":
    unittest.main()
