import inspect
import unittest

import cli
import client
from mcp_tools import GROUPS


UI_CAPABILITIES = {
    "list_boards",
    "get_board",
    "create_board",
    "update_board",
    "delete_board",
    "get_canvas_sync",
    "enable_canvas_sync",
    "disable_canvas_sync",
    "sync_canvas",
    "add_column",
    "update_column",
    "delete_column",
    "list_cards",
    "search_cards",
    "ai_search_cards",
    "create_card",
    "bulk_cards",
    "get_card",
    "update_card",
    "move_card",
    "delete_card",
    "add_session",
    "get_events",
    "get_card_history",
    "revert_card",
    "get_session_activity",
    "list_edges",
    "create_edge",
    "delete_edge",
}


class CapabilityAlignmentTest(unittest.TestCase):
    def test_sdk_mcp_and_cli_cover_ui_capabilities(self):
        sdk_capabilities = {
            name
            for name, value in inspect.getmembers(client, inspect.isfunction)
            if not name.startswith("_")
        }
        mcp_capabilities = {tool.name for group in GROUPS.values() for tool in group.TOOLS}
        cli_capabilities = set(cli.build_parser()._subparsers._group_actions[0].choices)

        self.assertEqual(set(), UI_CAPABILITIES - sdk_capabilities)
        self.assertEqual(set(), UI_CAPABILITIES - mcp_capabilities)
        self.assertEqual(set(), UI_CAPABILITIES - cli_capabilities)

    def test_no_tool_registered_in_more_than_one_group(self):
        seen = []
        for group in GROUPS.values():
            seen.extend(tool.name for tool in group.TOOLS)
        duplicates = {name for name in seen if seen.count(name) > 1}
        self.assertEqual(set(), duplicates)

    def test_card_search_sort_is_exposed_across_surfaces(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        for command_name in ("list_cards", "search_cards"):
            option_strings = {
                option
                for action in choices[command_name]._actions
                for option in action.option_strings
            }
            self.assertIn("--sort", option_strings)

        card_tools = {tool.name: tool for tool in GROUPS["cards"].TOOLS}
        for tool_name in ("list_cards", "search_cards"):
            properties = card_tools[tool_name].inputSchema["properties"]
            self.assertIn("sort", properties)


if __name__ == "__main__":
    unittest.main()
