import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-semantics-import-")

import card_store
import db
import main


class CardSemanticsImportTest(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
            prefix="agent-board-semantics-import-"
        )
        db.close()

    def tearDown(self):
        db.close()

    def test_structured_import_preserves_semantics_and_hierarchy(self):
        board = main.api_import_board(main.ImportBoard(
            name="Catalog",
            columns=["Catalog"],
            cards=[
                main.ImportCard(
                    external_id="area:cards",
                    title="Cards",
                    column="Catalog",
                    semantics={
                        "kind": "product_area",
                        "catalog_lifecycle": "active",
                        "ownership": {"component": "cards"},
                    },
                ),
                main.ImportCard(
                    external_id="feature:cards/semantics",
                    parent_external_id="area:cards",
                    title="Semantics",
                    column="Catalog",
                    semantics={
                        "kind": "feature",
                        "catalog_lifecycle": "active",
                    },
                ),
                main.ImportCard(
                    external_id="req:cards/semantics/typed",
                    parent_external_id="feature:cards/semantics",
                    title="Typed semantics",
                    column="Catalog",
                    semantics={
                        "kind": "requirement",
                        "catalog_lifecycle": "active",
                        "outcome": "Cards express typed semantics",
                        "acceptance_criteria": ["Round-trip preserves semantics"],
                    },
                ),
            ],
        ))

        cards = {card.external_id: card for card in card_store.list_cards(board.id)}
        self.assertEqual(cards["area:cards"].semantics.kind, "product_area")
        self.assertEqual(
            cards["feature:cards/semantics"].parent_id,
            cards["area:cards"].id,
        )
        self.assertEqual(
            cards["req:cards/semantics/typed"].semantics.outcome,
            "Cards express typed semantics",
        )


if __name__ == "__main__":
    unittest.main()
