import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(
    prefix="agent-board-main-startup-test-"
)

import main


class MainStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_initializes_storage_before_starting_workers(self):
        with patch.object(main.db, "initialize_storage") as initialize, patch.object(
            main.validation_worker, "is_enabled", return_value=False
        ), patch.object(
            main.board_consolidation, "recover_pending_consolidations"
        ) as recover:
            await main._start_validation_worker()

        initialize.assert_called_once_with()
        recover.assert_called_once_with()
        self.assertIsNone(main._validation_task)


if __name__ == "__main__":
    unittest.main()
