import unittest

from pydantic import ValidationError

from agent_board.card_semantics import (
    CardDecision,
    CardEvidence,
    CardOwnership,
    CardSemantics,
    sparse_value,
    valid_semantic_parent,
)


class CardSemanticsTest(unittest.TestCase):
    def test_sparse_value_recursively_omits_only_empty_values(self):
        self.assertEqual(
            sparse_value(
                {
                    "none": None,
                    "string": "",
                    "list": [],
                    "object": {},
                    "false": False,
                    "zero": 0,
                    "nested": {"empty": "", "value": "kept"},
                    "items": [None, "", {}, [], False, 0, {"x": "y"}],
                }
            ),
            {
                "false": False,
                "zero": 0,
                "nested": {"value": "kept"},
                "items": [False, 0, {"x": "y"}],
            },
        )

    def test_semantics_dump_is_sparse_and_typed(self):
        semantics = CardSemantics(
            kind="requirement",
            catalog_lifecycle="active",
            outcome="Users can inspect coverage",
            acceptance_criteria=["Coverage is derived"],
            ownership=CardOwnership(component="coverage"),
            evidence=[CardEvidence(kind="test", locator="backend/test_coverage.py")],
        )

        self.assertEqual(
            semantics.sparse_dump(),
            {
                "kind": "requirement",
                "catalog_lifecycle": "active",
                "outcome": "Users can inspect coverage",
                "acceptance_criteria": ["Coverage is derived"],
                "ownership": {"component": "coverage"},
                "evidence": [
                    {"kind": "test", "locator": "backend/test_coverage.py"}
                ],
            },
        )

    def test_requirement_requires_outcome_and_acceptance_criteria(self):
        with self.assertRaisesRegex(ValidationError, "require an outcome"):
            CardSemantics(
                kind="requirement",
                catalog_lifecycle="active",
                acceptance_criteria=["Defined"],
            )
        with self.assertRaisesRegex(ValidationError, "require acceptance criteria"):
            CardSemantics(
                kind="requirement",
                catalog_lifecycle="active",
                outcome="Defined",
            )

    def test_catalog_lifecycle_rejects_delivery_kinds(self):
        with self.assertRaisesRegex(ValidationError, "only valid for catalog"):
            CardSemantics(kind="task", catalog_lifecycle="active")

    def test_catalog_lifecycle_and_decision_graph_are_validated(self):
        with self.assertRaisesRegex(ValidationError, "require catalog_lifecycle"):
            CardSemantics(kind="feature")
        with self.assertRaisesRegex(ValidationError, "must reference"):
            CardSemantics(
                kind="task",
                decisions=[CardDecision(id="new", text="New", supersedes="missing")],
            )
        with self.assertRaisesRegex(ValidationError, "must be acyclic"):
            CardSemantics(
                kind="task",
                decisions=[
                    CardDecision(id="a", text="A", supersedes="b"),
                    CardDecision(id="b", text="B", supersedes="a"),
                ],
            )

    def test_required_nested_identity_rejects_blank_values(self):
        with self.assertRaisesRegex(ValidationError, "locator must not be empty"):
            CardEvidence(kind="test", locator="  ")
        with self.assertRaisesRegex(ValidationError, "must not be empty"):
            CardDecision(id="decision-1", text=" ")

    def test_delivery_hierarchy_allows_mixed_delivery_kinds_but_not_catalog_parents(self):
        task = CardSemantics(kind="task")
        bug = CardSemantics(kind="bug")
        requirement = CardSemantics(
            kind="requirement",
            catalog_lifecycle="active",
            outcome="Defined",
            acceptance_criteria=["Observable"],
        )
        self.assertTrue(valid_semantic_parent(task, task))
        self.assertTrue(valid_semantic_parent(bug, task))
        self.assertTrue(valid_semantic_parent(task, None))
        self.assertFalse(valid_semantic_parent(task, requirement))


if __name__ == "__main__":
    unittest.main()
