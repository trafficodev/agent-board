import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-notes-test-")

import board_store
import card_store
from models import AddNote, AnswerNote, CreateCard, UpdateCard


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

    def test_open_questions_uses_the_projection_after_writes(self):
        card_store.add_note(
            self.board.id,
            self.card.id,
            AddNote(kind="question", text="cached?"),
        )
        card_store.open_questions(self.board.id)

        with patch.object(card_store, "_read_cards", wraps=card_store._read_cards) as read_cards:
            cached = card_store.open_questions(self.board.id)
            self.assertEqual(read_cards.call_count, 0)
            self.assertEqual(cached[0]["card_title"], "C")

            card_store.update_card(
                self.board.id,
                self.card.id,
                UpdateCard(title="Renamed"),
            )
            read_cards.reset_mock()

            refreshed = card_store.open_questions(self.board.id)
            self.assertEqual(read_cards.call_count, 0)
            self.assertEqual(refreshed[0]["card_title"], "Renamed")
            card_store.open_questions(self.board.id)
            self.assertEqual(read_cards.call_count, 0)

    def test_open_questions_invalidates_after_cross_process_replace(self):
        card_store.add_note(
            self.board.id,
            self.card.id,
            AddNote(kind="question", text="cross-process?"),
        )
        card_store.open_questions(self.board.id)
        cards_path = card_store._cards_path(self.board.id)
        script = """
import json
import os
import sys
import uuid
from pathlib import Path

path = Path(sys.argv[1])
card_id = sys.argv[2]
cards = json.loads(path.read_text())
next(card for card in cards if card["id"] == card_id)["title"] = "External rename"
temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
temporary.write_text(json.dumps(cards))
os.replace(temporary, path)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(cards_path), self.card.id],
            check=True,
        )

        refreshed = card_store.open_questions(self.board.id)

        self.assertEqual(refreshed[0]["card_title"], "External rename")

    def test_open_questions_rejects_a_projection_replaced_between_fingerprints(self):
        card_store.add_note(
            self.board.id,
            self.card.id,
            AddNote(kind="question", text="busy?"),
        )
        cards_path = card_store._cards_path(self.board.id)
        original_read = card_store._read_open_question_projection

        def replace_cards(board_id, fingerprint):
            cards = card_store._read_cards(board_id)
            cards[0].title = "Concurrent rename"
            card_store._write_cards(board_id, cards)
            return original_read(board_id, fingerprint)

        with patch.object(
            card_store,
            "_read_open_question_projection",
            side_effect=replace_cards,
        ):
            result = card_store.open_questions(self.board.id)

        self.assertEqual(result[0]["card_title"], "Concurrent rename")
        self.assertIsNotNone(card_store._cards_fingerprint(cards_path))

    def test_projection_recovers_from_missing_corrupt_and_stale_files(self):
        card_store.add_note(
            self.board.id,
            self.card.id,
            AddNote(kind="question", text="recover?"),
        )
        projection = card_store._open_questions_path(self.board.id)
        for replacement in (None, "{broken", '{"cards_fingerprint":[0],"questions":[]}'):
            projection.unlink(missing_ok=True)
            if replacement is not None:
                projection.write_text(replacement)
            result = card_store.open_questions(self.board.id)
            self.assertEqual(result[0]["text"], "recover?")
            self.assertEqual(projection.stat().st_mode & 0o777, 0o600)

    def test_projection_recovers_after_interruption_between_card_and_projection_writes(self):
        with patch.object(
            card_store,
            "_write_open_question_projection",
            side_effect=OSError("interrupted"),
        ):
            card_store.add_note(
                self.board.id,
                self.card.id,
                AddNote(kind="question", text="landed before interruption"),
            )

        result = card_store.open_questions(self.board.id)

        self.assertEqual(result[0]["text"], "landed before interruption")

    def test_projection_cleanup_failure_does_not_abort_the_card_write(self):
        projection = MagicMock()
        projection.unlink.side_effect = OSError("cleanup failed")
        with (
            patch.object(
                card_store,
                "_write_open_question_projection",
                side_effect=OSError("projection failed"),
            ),
            patch.object(card_store, "_open_questions_path", return_value=projection),
        ):
            card = card_store.add_note(
                self.board.id,
                self.card.id,
                AddNote(kind="question", text="authoritative write survives"),
            )

        self.assertEqual(card.notes[-1].text, "authoritative write survives")

    def test_projection_converges_after_separate_process_writers(self):
        script = """
import os
import sys
sys.path.insert(0, sys.argv[1])
import card_store
from models import AddNote
card_store.add_note(sys.argv[2], sys.argv[3], AddNote(kind="question", text=sys.argv[4]))
"""
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    os.path.dirname(card_store.__file__),
                    self.board.id,
                    self.card.id,
                    text,
                ],
                env=os.environ.copy(),
            )
            for text in ("writer one", "writer two")
        ]
        for process in processes:
            self.assertEqual(process.wait(timeout=10), 0)

        questions = {item["text"] for item in card_store.open_questions(self.board.id)}

        self.assertTrue({"writer one", "writer two"} <= questions)

    def test_projection_path_is_confined_to_the_cards_directory(self):
        projection = card_store._open_questions_path(self.board.id)
        self.assertEqual(projection.parent, card_store._cards_path(self.board.id).parent)
        self.assertEqual(projection.name, f"{self.board.id}_open_questions.json")

    def test_projection_temporary_file_is_owner_only_from_creation(self):
        with patch.object(card_store.os, "open", wraps=os.open) as open_file:
            card_store.add_note(
                self.board.id,
                self.card.id,
                AddNote(kind="question", text="private at creation"),
            )

        self.assertTrue(any(call.args[2] == 0o600 for call in open_file.call_args_list))

    def test_deleting_a_board_removes_its_projection(self):
        projection = card_store._open_questions_path(self.board.id)
        self.assertTrue(projection.exists())

        self.assertTrue(board_store.delete_board(self.board.id))

        self.assertFalse(projection.exists())

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


if __name__ == "__main__":
    unittest.main()
