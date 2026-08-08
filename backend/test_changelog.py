import unittest

from card_diff import canonical_document, project_change_items
from models import Card, CardNote, SessionEntry


class CardChangelogProjectionTest(unittest.TestCase):
    def _card(self, **changes):
        values = {
            "id": "card-1",
            "board_id": "board-1",
            "title": "Title",
            "body": "line one\nline two",
            "column_id": "open",
            "priority": "medium",
            "labels": ["backend", "feature"],
            "metadata": {"review": {"status": "pending"}},
        }
        values.update(changes)
        return Card(**values)

    def test_one_metadata_leaf_produces_one_diff_item(self):
        before = canonical_document(self._card())
        after = canonical_document(
            self._card(metadata={"review": {"status": "approved"}})
        )

        items = project_change_items("event-1", before, after)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, "/metadata/review/status")
        self.assertEqual(items[0].operation, "replace")
        self.assertIn("-pending", items[0].diff)
        self.assertIn("+approved", items[0].diff)

    def test_label_order_is_not_an_authored_change(self):
        before = canonical_document(self._card())
        after = canonical_document(self._card(labels=["feature", "backend"]))

        self.assertEqual(project_change_items("event-2", before, after), [])

    def test_change_ids_are_stable_and_versioned(self):
        before = canonical_document(self._card(body="before"))
        after = canonical_document(self._card(body="after"))

        first = project_change_items("event-3", before, after)
        second = project_change_items("event-3", before, after)

        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertEqual(first[0].projection_version, 1)
        self.assertEqual(len(first[0].id), 64)

    def test_json_pointer_escapes_metadata_keys(self):
        before = canonical_document(self._card(metadata={"a/b": {"~key": 1}}))
        after = canonical_document(self._card(metadata={"a/b": {"~key": 2}}))

        item = project_change_items("event-4", before, after)[0]

        self.assertEqual(item.path, "/metadata/a~1b/~0key")

    def test_new_nested_metadata_expands_to_leaf_items(self):
        before = canonical_document(self._card(metadata={}))
        after = canonical_document(
            self._card(metadata={"review": {"status": "pending", "round": 1}})
        )

        items = project_change_items("event-nested", before, after)

        self.assertEqual(
            [item.path for item in items],
            ["/metadata/review/round", "/metadata/review/status"],
        )
        self.assertTrue(all(item.operation == "add" for item in items))

    def test_metadata_array_is_one_stable_field_change(self):
        before = canonical_document(self._card(metadata={"claims": []}))
        after = canonical_document(self._card(metadata={"claims": ["one", "two"]}))

        items = project_change_items("event-array", before, after)

        self.assertEqual(
            [(item.path, item.operation) for item in items],
            [("/metadata/claims", "replace")],
        )

    def test_adding_a_label_does_not_rewrite_existing_labels(self):
        before = canonical_document(self._card(labels=["backend", "feature"]))
        after = canonical_document(self._card(labels=["api", "backend", "feature"]))

        items = project_change_items("event-label", before, after)

        self.assertEqual(
            [(item.path, item.operation) for item in items],
            [("/labels/api", "add")],
        )

    def test_trailing_newline_change_has_a_visible_diff(self):
        before = canonical_document(self._card(body="line"))
        after = canonical_document(self._card(body="line\n"))

        item = project_change_items("event-newline", before, after)[0]

        self.assertTrue(item.diff)
        self.assertIn("+", item.diff)

    def test_creation_and_deletion_are_leaf_items(self):
        document = canonical_document(self._card())

        created = project_change_items("event-5", None, document)
        deleted = project_change_items("event-6", document, None)

        self.assertIn(("/title", "add"), [(item.path, item.operation) for item in created])
        self.assertIn(("/title", "remove"), [(item.path, item.operation) for item in deleted])
        self.assertTrue(all(item.path for item in [*created, *deleted]))

    def test_empty_string_addition_still_has_a_visible_diff(self):
        item = project_change_items("event-empty", None, {"body": ""})[0]

        self.assertIn("+", item.diff)

    def test_nested_entities_are_keyed_by_stable_ids(self):
        document = canonical_document(self._card(
            notes=[
                CardNote(id="note-1", kind="note", text="context"),
                CardNote(
                    id="question-1",
                    kind="question",
                    text="ship it?",
                    answer="yes",
                    answered_by="reviewer",
                ),
            ],
            session_history=[
                SessionEntry(session_id="generated", action="updated"),
                SessionEntry(
                    claim_id="claim-1",
                    session_id="worker",
                    system="codex",
                    action="implemented",
                    outcome="success",
                ),
            ],
        ))

        self.assertEqual(document["notes"], {"note-1": {"text": "context"}})
        self.assertEqual(
            document["questions"],
            {"question-1": {"text": "ship it?"}},
        )
        self.assertEqual(
            document["answers"],
            {"question-1": {"text": "yes"}},
        )
        self.assertEqual(document["claims"], {
            "claim-1": {
                "action": "implemented",
                "outcome": "success",
                "session_id": "worker",
                "system": "codex",
            },
        })
        self.assertNotIn("generated", str(document))
        self.assertNotIn("reviewer", str(document))

    def test_answer_and_claim_each_project_as_their_own_leaf(self):
        card = self._card(notes=[
            CardNote(id="question-1", kind="question", text="ship it?"),
        ])
        before_answer = canonical_document(card)
        card.notes[0].answer = "yes"
        after_answer = canonical_document(card)

        answer_items = project_change_items("event-answer", before_answer, after_answer)

        self.assertEqual(
            [(item.path, item.operation) for item in answer_items],
            [("/answers/question-1/text", "add")],
        )

        card.session_history.append(SessionEntry(
            claim_id="claim-1",
            session_id="worker",
            action="implemented",
        ))
        after_claim = canonical_document(card)
        claim_items = project_change_items("event-claim", after_answer, after_claim)

        self.assertEqual(
            [item.path for item in claim_items],
            ["/claims/claim-1/action", "/claims/claim-1/session_id"],
        )


if __name__ == "__main__":
    unittest.main()
