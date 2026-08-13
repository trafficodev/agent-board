import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-semantics-store-")

from agent_board import board_store
from agent_board import card_store
from agent_board import card_history
from agent_board import db
from agent_board.card_semantics import CardSemantics
from agent_board.models import CreateCard, RevertCard, UpdateCard


class CardSemanticsStoreTest(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
            prefix="agent-board-semantics-store-"
        )
        db.close()
        self.board = board_store.create_board("Semantics", "", ["Catalog", "Done"])
        self.catalog = self.board.columns[0].id

    def tearDown(self):
        db.close()

    def _create(self, title, kind, parent_id=None):
        if kind == "requirement":
            semantics = CardSemantics(
                kind=kind,
                catalog_lifecycle="active",
                outcome=f"Outcome for {title}",
                acceptance_criteria=["Observable behavior passes"],
            )
        else:
            semantics = CardSemantics(kind=kind, catalog_lifecycle="active")
        return card_store.create_card(
            self.board.id,
            CreateCard(
                title=title,
                column_id=self.catalog,
                parent_id=parent_id,
                semantics=semantics,
            ),
        )

    def test_catalog_hierarchy_is_enforced_on_create_and_update(self):
        area = self._create("Area", "product_area")
        feature = self._create("Feature", "feature", area.id)
        requirement = self._create("Requirement", "requirement", feature.id)

        self.assertIsNotNone(requirement)
        self.assertIsNone(self._create("Root feature", "feature"))
        self.assertIsNone(self._create("Requirement under area", "requirement", area.id))
        self.assertIsNone(
            card_store.update_card(
                self.board.id,
                feature.id,
                UpdateCard(parent_id=None),
            )
        )

    def test_delivery_kind_is_accepted_under_a_kind_less_parent(self):
        # Regression: a grouping card with no semantic kind used to block any
        # of its children from taking a kind at all, so `kind=test`/`decision`
        # 422'd with a misleading edge/remote-sounding error. A kind-less parent
        # is a container, not a catalog node, and must not gate its children.
        grouping = card_store.create_card(
            self.board.id,
            CreateCard(title="Grouping", column_id=self.catalog),
        )
        child = card_store.create_card(
            self.board.id,
            CreateCard(title="Child", column_id=self.catalog, parent_id=grouping.id),
        )
        for kind in ("task", "test", "decision"):
            updated = card_store.update_card(
                self.board.id,
                child.id,
                UpdateCard(semantics=CardSemantics(kind=kind, acceptance_criteria=["x"])),
            )
            self.assertIsNotNone(updated, f"{kind} should be accepted under a kind-less parent")
            self.assertEqual(updated.semantics.kind, kind)

    def test_catalog_hierarchy_refusal_names_the_specific_reason(self):
        # The refusal reason identifies the semantic-hierarchy cause and field,
        # rather than OR-ing three unrelated causes together.
        area = self._create("Area", "product_area")
        _, reject = card_store.create_card_result(
            self.board.id,
            CreateCard(
                title="Requirement under area",
                column_id=self.catalog,
                parent_id=area.id,
                semantics=CardSemantics(
                    kind="requirement",
                    catalog_lifecycle="active",
                    outcome="o",
                    acceptance_criteria=["a"],
                ),
            ),
        )
        self.assertIsNotNone(reject)
        self.assertEqual(reject.code, "semantic_hierarchy")
        self.assertEqual(reject.field, "semantics.kind")
        self.assertIn("semantic hierarchy", reject.reason)
        self.assertNotIn("edge conflict", reject.reason)
        self.assertNotIn("unknown column", reject.reason)

    def test_semantics_participate_in_history_and_revert(self):
        area = self._create("Area", "product_area")
        updated = card_store.update_card(
            self.board.id,
            area.id,
            UpdateCard(
                semantics=CardSemantics(
                    kind="product_area",
                    catalog_lifecycle="deprecated",
                )
            ),
        )
        self.assertEqual(updated.semantics.catalog_lifecycle, "deprecated")
        versions = card_history.card_versions(self.board.id, area.id)
        self.assertIn("semantics", [change.field for change in versions[-1].changes])

        reverted = card_store.revert_card(
            self.board.id,
            area.id,
            RevertCard(version=1),
        )
        self.assertEqual(reverted.semantics.catalog_lifecycle, "active")


if __name__ == "__main__":
    unittest.main()
