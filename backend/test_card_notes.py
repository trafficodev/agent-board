import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-notes-test-")

import board_store
import card_history
import card_store
from card_diff import project_change_items
from models import AddNote, AddSession, AnswerNote, CreateCard, UpdateCard


class CardNotesTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)
        os.environ["BETTER_AGENT_APP_SESSION_ID"] = "sess-asker"
        self.board = board_store.create_board("N", "", ["Open"])
        self.card = card_store.create_card(
            self.board.id, CreateCard(title="C", column_id=self.board.columns[0].id)
        )

    def tearDown(self):
        os.environ.pop("BETTER_AGENT_APP_SESSION_ID", None)

    def test_a_note_records_its_author_and_is_not_a_question(self):
        card = card_store.add_note(self.board.id, self.card.id, AddNote(kind="note", text="context"))

        note = card.notes[-1]
        self.assertEqual(note.kind, "note")
        self.assertEqual(note.text, "context")
        self.assertEqual(note.session_id, "sess-asker")
        self.assertFalse(card_store.is_open_question(note))

    def test_a_question_is_open_until_answered(self):
        card = card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="ship it?"))
        note = card.notes[-1]
        self.assertTrue(card_store.is_open_question(note))
        self.assertEqual(len(card_store.open_questions(self.board.id)), 1)

        answered = card_store.answer_note(
            self.board.id, self.card.id, note.id, AnswerNote(answer="yes", answered_by="user")
        )

        self.assertFalse(card_store.is_open_question(answered.notes[-1]))
        self.assertEqual(answered.notes[-1].answer, "yes")
        self.assertEqual(answered.notes[-1].answered_by, "user")
        self.assertIsNotNone(answered.notes[-1].answered_at)
        self.assertEqual(card_store.open_questions(self.board.id), [])

    def test_open_questions_carry_enough_to_navigate_to_the_card(self):
        card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="which lane?"))

        item = card_store.open_questions(self.board.id)[0]

        self.assertEqual(item["board_id"], self.board.id)
        self.assertEqual(item["card_id"], self.card.id)
        self.assertEqual(item["card_title"], "C")
        self.assertEqual(item["text"], "which lane?")
        self.assertEqual(item["session_id"], "sess-asker")

    def test_open_questions_span_every_board_when_unscoped(self):
        other = board_store.create_board("O", "", ["Open"])
        other_card = card_store.create_card(
            other.id, CreateCard(title="D", column_id=other.columns[0].id)
        )
        card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="q1"))
        card_store.add_note(other.id, other_card.id, AddNote(kind="question", text="q2"))

        # Other tests share this temp home, so assert both are present rather
        # than pinning a global total.
        unscoped = {item["text"] for item in card_store.open_questions()}
        self.assertIn("q1", unscoped)
        self.assertIn("q2", unscoped)
        self.assertEqual([i["text"] for i in card_store.open_questions(other.id)], ["q2"])

    def test_empty_text_and_empty_answer_are_rejected(self):
        self.assertIsNone(card_store.add_note(self.board.id, self.card.id, AddNote(text="   ")))
        card = card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="q"))
        note_id = card.notes[-1].id
        self.assertIsNone(
            card_store.answer_note(self.board.id, self.card.id, note_id, AnswerNote(answer="  "))
        )

    def test_only_questions_can_be_answered(self):
        card = card_store.add_note(self.board.id, self.card.id, AddNote(kind="note", text="just a note"))
        note_id = card.notes[-1].id

        self.assertIsNone(
            card_store.answer_note(self.board.id, self.card.id, note_id, AnswerNote(answer="x"))
        )

    def test_notes_and_answers_appear_in_card_history(self):
        card = card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="q"))
        card_store.answer_note(self.board.id, self.card.id, card.notes[-1].id, AnswerNote(answer="a"))

        actions = [e.action for e in card_store.get_card(self.board.id, self.card.id).session_history]
        self.assertEqual(actions[-2:], ["asked", "answered"])

    def test_notes_survive_a_round_trip_to_disk(self):
        card_store.add_note(self.board.id, self.card.id, AddNote(kind="question", text="persisted?"))

        reread = card_store.get_card(self.board.id, self.card.id)

        self.assertEqual(reread.notes[-1].text, "persisted?")

    def test_nested_entity_events_keep_post_mutation_canonical_documents(self):
        asked = card_store.add_note(
            self.board.id,
            self.card.id,
            AddNote(kind="question", text="ship it?"),
        )
        note_id = asked.notes[-1].id
        card_store.answer_note(
            self.board.id,
            self.card.id,
            note_id,
            AnswerNote(answer="yes", answered_by="reviewer"),
        )
        card_store.add_session(
            self.board.id,
            self.card.id,
            AddSession(
                session_id="worker",
                system="codex",
                action="implemented",
                outcome="success",
            ),
        )
        card_store.update_card(
            self.board.id,
            self.card.id,
            UpdateCard(title="renamed"),
        )

        events = [
            event
            for event in card_history.read_events(self.board.id)
            if event.card_id == self.card.id
        ]
        created, asked_event, answered_event, claim_event, renamed = events
        self.assertEqual(created.document["questions"], {})
        self.assertEqual(
            asked_event.document["questions"],
            {note_id: {"text": "ship it?"}},
        )
        self.assertEqual(
            answered_event.document["answers"],
            {note_id: {"text": "yes"}},
        )
        claim_id = next(iter(claim_event.document["claims"]))
        self.assertEqual(
            claim_event.document["claims"][claim_id]["action"],
            "implemented",
        )
        self.assertEqual(renamed.document["claims"], claim_event.document["claims"])

        self.assertEqual(
            [item.path for item in project_change_items(
                answered_event.id,
                asked_event.document,
                answered_event.document,
            )],
            [f"/answers/{note_id}/text"],
        )

    def test_explicit_claims_are_not_evicted_with_generated_history(self):
        with patch.object(card_store, "_HISTORY_LIMIT", 3):
            claimed = card_store.add_session(
                self.board.id,
                self.card.id,
                AddSession(session_id="worker", action="implemented"),
            )
            claim_id = next(
                entry.claim_id for entry in claimed.session_history if entry.claim_id
            )
            for index in range(8):
                card_store.update_card(
                    self.board.id,
                    self.card.id,
                    UpdateCard(body=str(index)),
                )

            reread = card_store.get_card(self.board.id, self.card.id)
        claims = [entry for entry in reread.session_history if entry.claim_id]
        generated = [entry for entry in reread.session_history if not entry.claim_id]
        self.assertEqual([entry.claim_id for entry in claims], [claim_id])
        self.assertEqual(len(generated), 3)

        latest_event = [
            event for event in card_history.read_events(self.board.id)
            if event.card_id == self.card.id
        ][-1]
        self.assertIn(claim_id, latest_event.document["claims"])


if __name__ == "__main__":
    unittest.main()
