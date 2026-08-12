import gc
import os
import tempfile
import threading
import unittest
import warnings

import db


class DatabaseConnectionLifecycleTest(unittest.TestCase):
    def test_worker_thread_closes_its_connection_when_the_thread_ends(self):
        with tempfile.TemporaryDirectory(prefix="agent-board-db-lifecycle-") as home:
            os.environ["AGENT_BOARD_HOME"] = home
            worker = threading.Thread(target=db.connect)

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                worker.start()
                worker.join()
                gc.collect()

            leaked = [warning for warning in caught if warning.category is ResourceWarning]
            self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
