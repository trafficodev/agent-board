import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-mcp-test-")

from agent_board import mcp_server
from agent_board import card_projection
from agent_board.card_semantics import CardSemantics
from agent_board.mcp_tools import GROUPS
from agent_board.mcp_tools import cards


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
    async def test_mcp_projection_boundary_rejects_invalid_include_and_exclude(self):
        def resolve(_board_id, **arguments):
            return card_projection.resolve_card_fields(
                "list_cards",
                include=arguments.get("include"),
                exclude=arguments.get("exclude"),
            )

        owner = mcp_server.ToolDispatchOwner(max_concurrency=1)
        owner._owners = {"list_cards": cards}
        try:
            with patch.object(cards.client, "list_cards", side_effect=resolve):
                for arguments in (
                    {"board_id": "board", "include": ["unknown"]},
                    {"board_id": "board", "exclude": ["unknown"]},
                ):
                    with self.subTest(arguments=arguments):
                        with self.assertRaisesRegex(ValueError, "invalid_card_fields:unknown"):
                            await owner.call("list_cards", arguments)
                self.assertEqual(
                    await owner.call(
                        "list_cards", {"board_id": "board", "include": []}
                    ),
                    ["id"],
                )
        finally:
            await owner.close()

    async def test_exact_projection_tools_preserve_selected_empty_values_only(self):
        projection_result = {
            "items": [{
                "id": "card",
                "body": "",
                "parent_id": None,
                "labels": [],
                "semantics": CardSemantics(kind="bug"),
            }],
            "next_cursor": None,
            "has_more": False,
        }
        expected_projection = {
            "items": [{
                "id": "card",
                "body": "",
                "parent_id": None,
                "labels": [],
                "semantics": {"kind": "bug"},
            }],
            "next_cursor": None,
            "has_more": False,
        }

        class CardGroup:
            EXACT_PROJECTION_TOOLS = frozenset({
                "list_cards", "search_cards", "relevant_candidates",
            })

            @staticmethod
            def dispatch(_name, _arguments):
                return projection_result

        class OtherGroup:
            @staticmethod
            def dispatch(_name, _arguments):
                return projection_result

        owner = mcp_server.ToolDispatchOwner(max_concurrency=1)
        owner._owners = {
            **{name: CardGroup for name in CardGroup.EXACT_PROJECTION_TOOLS},
            "get_card": CardGroup,
            "other_tool": OtherGroup,
        }
        try:
            for name in CardGroup.EXACT_PROJECTION_TOOLS:
                with self.subTest(name=name):
                    self.assertEqual(await owner.call(name, {}), expected_projection)
            compacted = {
                "items": [{"id": "card", "semantics": {"kind": "bug"}}],
                "has_more": False,
            }
            self.assertEqual(await owner.call("get_card", {}), compacted)
            self.assertEqual(await owner.call("other_tool", {}), compacted)
        finally:
            await owner.close()

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
