import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-semantics-store-")

import board_store
import card_store
import card_history
import db
from card_semantics import CardSemantics
from models import CreateCard, RevertCard, UpdateCard


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
