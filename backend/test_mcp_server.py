import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-mcp-test-")

import mcp_server
from card_semantics import CardSemantics
from mcp_tools import GROUPS


class MCPServerTests(unittest.TestCase):
    def test_every_tool_has_one_dispatch_owner(self) -> None:
        owners = mcp_server._tool_owners()
        expected = [tool.name for group in GROUPS.values() for tool in group.TOOLS]

        self.assertEqual(set(owners), set(expected))
        self.assertEqual(len(owners), len(expected))

    def test_single_server_lists_every_group_tool(self) -> None:
        with patch.object(mcp_server.db, "initialize_storage") as initialize:
            app = mcp_server.build_app()

        self.assertEqual(app.name, "agent-board")
        initialize.assert_called_once_with()

    def test_concurrency_configuration_fails_closed(self) -> None:
        with patch.dict(
            "os.environ",
            {"AGENT_BOARD_MCP_MAX_CONCURRENCY": "0"},
        ):
            with self.assertRaisesRegex(RuntimeError, "between 1 and 32"):
                mcp_server.ToolDispatchOwner()


class MCPDispatchCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_recursively_compacts_without_losing_false_or_zero(self):
        class Group:
            @staticmethod
            def dispatch(_name, _arguments):
                return {
                    "empty": "",
                    "none": None,
                    "nested": {"empty": [], "kept": False},
                    "items": [{"empty": {}, "count": 0}],
                    "semantics": CardSemantics(),
                    "has_more": False,
                }

        owner = mcp_server.ToolDispatchOwner(max_concurrency=1)
        owner._owners = {"sample": Group}
        try:
            self.assertEqual(
                await owner.call("sample", {}),
                {
                    "nested": {"kept": False},
                    "items": [{"count": 0}],
                    "has_more": False,
                },
            )
        finally:
            await owner.close()

    async def test_empty_root_containers_remain_valid_results(self):
        class Group:
            @staticmethod
            def dispatch(name, _arguments):
                return {} if name == "object" else []

        owner = mcp_server.ToolDispatchOwner(max_concurrency=1)
        owner._owners = {"object": Group, "array": Group}
        try:
            self.assertEqual(await owner.call("object", {}), {})
            self.assertEqual(await owner.call("array", {}), [])
        finally:
            await owner.close()


if __name__ == "__main__":
    unittest.main()
