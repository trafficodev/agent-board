import os
import tempfile
import threading
import unittest
from datetime import timedelta

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-ver-test-")

import board_store
import card_history
import card_store
from models import AddNote, CreateCard, Event, MoveCard, RevertCard, UpdateCard


class CardVersioningTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-alpha"
        os.environ["BETTER_AGENT_PROVIDER_KIND"] = "claude"
        self.board = board_store.create_board("V", "", ["Open", "Done"])
        self.open_id = self.board.columns[0].id
        self.done_id = self.board.columns[1].id

    def tearDown(self):
        os.environ.pop("BETTER_AGENT_APP_SESSION_ID", None)
        os.environ.pop("BETTER_AGENT_PROVIDER_KIND", None)

    def _card(self, **kwargs):
        kwargs.setdefault("title", "T")
        return card_store.create_card(
            self.board.id, CreateCard(column_id=self.open_id, **kwargs)
        )

    def test_full_history_outlives_the_capped_on_card_projection(self):
        """The card carries the last N entries; the journal carries every one."""
        card = self._card(body="0")
        total = card_store._HISTORY_LIMIT + 20
        for i in range(1, total):
            card_store.update_card(self.board.id, card.id, UpdateCard(body=str(i)))

        reread = card_store.get_card(self.board.id, card.id)
        versions = card_history.card_versions(self.board.id, card.id)

        self.assertEqual(len(reread.session_history), card_store._HISTORY_LIMIT)
        self.assertEqual(len(versions), total)
        self.assertEqual(versions[0].action, "created")
        self.assertEqual(versions[0].snapshot["body"], "0")
        self.assertEqual(versions[-1].snapshot["body"], str(total - 1))

    def test_journal_keeps_values_the_card_projection_truncates(self):
        long_body = "y" * 5000
        card = self._card(body="x")
        card_store.update_card(self.board.id, card.id, UpdateCard(body=long_body))

        on_card = card_store.get_card(self.board.id, card.id).session_history[-1].changes[0].after
        in_journal = card_history.card_versions(self.board.id, card.id)[-1].snapshot["body"]

        self.assertTrue(on_card.endswith("…"))
        self.assertEqual(in_journal, long_body)

    def test_every_version_names_the_session_that_authored_it(self):
        card = self._card()
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-beta"
        card_store.update_card(self.board.id, card.id, UpdateCard(title="renamed"))
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-gamma"
        card_store.move_card(self.board.id, card.id, MoveCard(column_id=self.done_id))

        versions = card_history.card_versions(self.board.id, card.id)

        self.assertEqual(
            [(v.action, v.session_id) for v in versions],
            [("created", "sess-alpha"), ("updated", "sess-beta"), ("moved", "sess-gamma")],
        )
        self.assertTrue(all(v.system == "claude" for v in versions))

    def test_a_no_op_update_does_not_mint_a_version(self):
        card = self._card(body="same")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="same"))

        self.assertEqual(len(card_history.card_versions(self.board.id, card.id)), 1)

    def test_notes_and_answers_appear_in_the_timeline(self):
        card = self._card()
        card_store.add_note(self.board.id, card.id, AddNote(kind="question", text="which way?"))

        actions = [v.action for v in card_history.card_versions(self.board.id, card.id)]

        self.assertEqual(actions, ["created", "asked"])

    def test_history_survives_deleting_the_card(self):
        card = self._card(body="mattered")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="still mattered"))

        self.assertTrue(card_store.delete_card(self.board.id, card.id))

        self.assertIsNone(card_store.get_card(self.board.id, card.id))
        versions = card_history.card_versions(self.board.id, card.id)
        self.assertEqual([v.action for v in versions], ["created", "updated", "deleted"])
        self.assertEqual(versions[-1].snapshot["body"], "still mattered")

    def test_a_subtask_dragged_by_its_parent_records_its_own_version(self):
        parent = self._card()
        child = card_store.create_card(
            self.board.id,
            CreateCard(title="child", column_id=self.open_id, parent_id=parent.id),
        )

        card_store.move_card(self.board.id, parent.id, MoveCard(column_id=self.done_id))

        child_versions = card_history.card_versions(self.board.id, child.id)
        self.assertEqual([v.action for v in child_versions], ["created", "moved"])
        self.assertEqual(child_versions[-1].snapshot["column_id"], self.done_id)

    def test_card_can_be_reconstructed_as_of_a_past_moment(self):
        card = self._card(body="first")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="second"))
        card_store.update_card(self.board.id, card.id, UpdateCard(body="third"))

        versions = card_history.card_versions(self.board.id, card.id)
        at_second = card_history.card_as_of(self.board.id, card.id, versions[1].timestamp)

        self.assertEqual(at_second.snapshot["body"], "second")

    def test_reconstruction_before_the_card_existed_is_none(self):
        card = self._card()
        created_at = card_history.card_versions(self.board.id, card.id)[0].timestamp

        self.assertIsNone(
            card_history.card_as_of(self.board.id, card.id, created_at - timedelta(seconds=1))
        )

    def test_session_activity_spans_boards(self):
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-worker"
        other = board_store.create_board("V2", "", ["Open"])
        self._card(title="here")
        card_store.create_card(other.id, CreateCard(title="there", column_id=other.columns[0].id))

        activity = card_history.session_activity("sess-worker")

        self.assertEqual({e.board_id for e in activity}, {self.board.id, other.id})
        self.assertTrue(all(e.session_id == "sess-worker" for e in activity))

    def test_session_activity_ignores_other_sessions(self):
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-one"
        self._card(title="mine")
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-two"
        self._card(title="theirs")

        titles = {e.snapshot["title"] for e in card_history.session_activity("sess-one")}

        self.assertEqual(titles, {"mine"})

    def test_revert_restores_content_and_is_itself_recorded(self):
        card = self._card(body="original", priority="low")
        card_store.update_card(
            self.board.id, card.id, UpdateCard(body="rewritten", priority="critical")
        )

        reverted = card_store.revert_card(self.board.id, card.id, RevertCard(version=1))

        self.assertEqual(reverted.body, "original")
        self.assertEqual(reverted.priority, "low")
        actions = [v.action for v in card_history.card_versions(self.board.id, card.id)]
        self.assertEqual(actions, ["created", "updated", "reverted"])

    def test_revert_never_rewrites_the_journal(self):
        card = self._card(body="one")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="two"))
        before = card_history.card_versions(self.board.id, card.id)

        card_store.revert_card(self.board.id, card.id, RevertCard(version=1))
        after = card_history.card_versions(self.board.id, card.id)

        self.assertEqual([v.event_id for v in before], [v.event_id for v in after[:len(before)]])
        self.assertEqual(after[-1].snapshot["body"], "one")

    def test_revert_to_an_unknown_version_is_refused(self):
        card = self._card()

        self.assertIsNone(card_store.revert_card(self.board.id, card.id, RevertCard(version=99)))
        self.assertIsNone(card_store.revert_card(self.board.id, card.id, RevertCard(version=0)))

    def test_revert_into_a_deleted_column_is_refused(self):
        card = self._card()
        card_store.move_card(self.board.id, card.id, MoveCard(column_id=self.done_id))
        board_store.delete_column(self.board.id, self.open_id)

        self.assertIsNone(card_store.revert_card(self.board.id, card.id, RevertCard(version=1)))

    def test_point_in_time_accepts_a_naive_timestamp(self):
        card = self._card(body="first")
        card_store.update_card(self.board.id, card.id, UpdateCard(body="second"))
        aware = card_history.card_versions(self.board.id, card.id)[0].timestamp

        version = card_history.card_as_of(self.board.id, card.id, aware.replace(tzinfo=None))

        self.assertEqual(version.snapshot["body"], "first")

    def test_events_written_before_structured_attribution_keep_their_actor(self):
        legacy = Event.model_validate_json(
            '{"timestamp": "2024-01-01T00:00:00+00:00", "type": "card_created",'
            ' "actor": "claude-session-42", "detail": "old"}'
        )

        self.assertEqual(legacy.actor, "claude-session-42")

    def test_the_activity_feed_does_not_carry_card_snapshots(self):
        """The feed answers 'what happened'; shipping every version's full body
        would make a routine request scale with the whole journal."""
        card = self._card(body="x" * 5000)
        card_store.update_card(self.board.id, card.id, UpdateCard(body="y" * 5000))

        feed = card_store.get_events(self.board.id, limit=10)

        self.assertTrue(all(e.snapshot is None for e in feed))
        self.assertEqual({e.card_id for e in feed if e.card_id}, {card.id})

    def test_concurrent_writers_do_not_lose_each_others_cards(self):
        """Each mutator rewrites the whole cards list, so the read has to happen
        inside the lock or two writers each save a list missing the other."""
        errors: list[BaseException] = []

        def make(prefix: str):
            try:
                for i in range(15):
                    card_store.create_card(
                        self.board.id,
                        CreateCard(title=f"{prefix}{i}", column_id=self.open_id),
                    )
            except BaseException as exc:  # surfaced below; a thread death is a failure
                errors.append(exc)

        threads = [threading.Thread(target=make, args=(p,)) for p in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        titles = {c.title for c in card_store.list_cards(self.board.id)}
        self.assertEqual(titles, {f"{p}{i}" for p in ("A", "B") for i in range(15)})

    def test_a_card_the_board_lost_is_not_left_asserted_by_the_journal(self):
        """Every card the journal says was created must still be on the board,
        unless the journal also says it was deleted."""
        for i in range(10):
            self._card(title=f"C{i}")

        on_board = {c.id for c in card_store.list_cards(self.board.id)}
        events = card_history.read_events(self.board.id)
        created = {e.card_id for e in events if e.type == "card_created"}
        deleted = {e.card_id for e in events if e.type == "card_deleted"}

        self.assertEqual(created - deleted - on_board, set())


if __name__ == "__main__":
    unittest.main()
