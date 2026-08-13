import os
import tempfile
import unittest

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-test-")

from agent_board import board_store
from agent_board import card_store
from agent_board.models import CreateCard, UpdateCard


class CardMetadataTest(unittest.TestCase):
    def test_card_external_id_and_metadata_persist_through_update(self):
        board = board_store.create_board("T", "", ["Open"])
        column_id = board.columns[0].id

        created = card_store.create_card(
            board.id,
            CreateCard(
                external_id="assistant:s1",
                title="Fix bug",
                column_id=column_id,
                metadata={"turn_id": "s1", "edited_files": ["frontend/src/App.tsx"]},
                labels=["assistant"],
            ),
        )

        self.assertEqual(created.external_id, "assistant:s1")
        self.assertEqual(created.metadata["turn_id"], "s1")

        updated = card_store.update_card(
            board.id,
            created.id,
            UpdateCard(metadata={"turn_id": "s1", "reported_closed": True}),
        )

        loaded = card_store.get_card(board.id, created.id)
        self.assertEqual(updated.metadata["reported_closed"], True)
        self.assertEqual(loaded.external_id, "assistant:s1")
        self.assertEqual(loaded.metadata["turn_id"], "s1")


if __name__ == "__main__":
    unittest.main()
