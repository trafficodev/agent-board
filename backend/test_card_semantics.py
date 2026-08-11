import unittest

from pydantic import ValidationError

from card_semantics import CardDecision, CardEvidence, CardOwnership, CardSemantics, sparse_value


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
            CardSemantics(kind="requirement", acceptance_criteria=["Defined"])
        with self.assertRaisesRegex(ValidationError, "require acceptance criteria"):
            CardSemantics(kind="requirement", outcome="Defined")

    def test_catalog_lifecycle_rejects_delivery_kinds(self):
        with self.assertRaisesRegex(ValidationError, "only valid for catalog"):
            CardSemantics(kind="task", catalog_lifecycle="active")

    def test_required_nested_identity_rejects_blank_values(self):
        with self.assertRaisesRegex(ValidationError, "locator must not be empty"):
            CardEvidence(kind="test", locator="  ")
        with self.assertRaisesRegex(ValidationError, "must not be empty"):
            CardDecision(id="decision-1", text=" ")


if __name__ == "__main__":
    unittest.main()
