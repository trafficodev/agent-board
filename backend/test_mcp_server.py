import unittest
from unittest.mock import patch

import mcp_server
from mcp_tools import GROUPS


class MCPServerTests(unittest.TestCase):
    def test_every_tool_has_one_dispatch_owner(self) -> None:
        owners = mcp_server._tool_owners()
        expected = [tool.name for group in GROUPS.values() for tool in group.TOOLS]

        self.assertEqual(set(owners), set(expected))
        self.assertEqual(len(owners), len(expected))

    def test_single_server_lists_every_group_tool(self) -> None:
        app = mcp_server.build_app()

        self.assertEqual(app.name, "agent-board")

    def test_concurrency_configuration_fails_closed(self) -> None:
        with patch.dict(
            "os.environ",
            {"AGENT_BOARD_MCP_MAX_CONCURRENCY": "0"},
        ):
            with self.assertRaisesRegex(RuntimeError, "between 1 and 32"):
                mcp_server.ToolDispatchOwner()


if __name__ == "__main__":
    unittest.main()
