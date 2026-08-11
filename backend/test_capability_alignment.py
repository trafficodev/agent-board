import inspect
import unittest
from unittest.mock import patch

import cli
import client
from mcp_tools import GROUPS


UI_CAPABILITIES = {
    "list_boards",
    "get_board",
    "create_board",
    "update_board",
    "link_project_remote",
    "consolidate_project_board",
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
    "create_card",
    "bulk_cards",
    "get_card",
    "update_card",
    "move_card",
    "delete_card",
    "add_session",
    "get_events",
    "get_card_history",
    "get_card_changes",
    "set_change_vote",
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

    def test_card_exploration_controls_are_exposed_across_surfaces(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        for command_name in ("list_cards", "search_cards", "relevant_candidates"):
            option_strings = {
                option
                for action in choices[command_name]._actions
                for option in action.option_strings
            }
            self.assertTrue({"--limit", "--cursor", "--include", "--exclude"}.issubset(option_strings))
            self.assertNotIn("--detail", option_strings)

        card_tools = {tool.name: tool for tool in GROUPS["cards"].TOOLS}
        for tool_name in ("list_cards", "search_cards", "relevant_candidates"):
            properties = card_tools[tool_name].inputSchema["properties"]
            self.assertTrue({"limit", "cursor", "include", "exclude"}.issubset(properties))
            self.assertEqual(properties["limit"]["default"], 50)
            self.assertEqual(properties["limit"]["maximum"], 100)
            self.assertEqual(properties["include"]["type"], "array")
            self.assertEqual(properties["exclude"]["type"], "array")
            self.assertNotIn("detail", properties)

        relevant_options = {
            option
            for action in choices["relevant_candidates"]._actions
            for option in action.option_strings
        }
        self.assertTrue({"--max-candidates", "--limit", "--cursor"}.issubset(relevant_options))
        relevant_properties = card_tools["relevant_candidates"].inputSchema["properties"]
        self.assertTrue({"max_candidates", "limit", "cursor"}.issubset(relevant_properties))
        self.assertEqual(relevant_properties["max_candidates"]["default"], 40)

    def test_client_forwards_projection_values_repeatably(self):
        with patch.object(client, "_req", return_value={}) as request:
            client.list_cards("board")
            client.search_cards(
                "board",
                query="bug",
                limit=25,
                cursor="next page",
                include=["body,metadata", "notes"],
                exclude="labels",
            )

        self.assertEqual(
            request.call_args_list[0].args,
            ("GET", "/api/boards/board/cards?limit=50"),
        )
        self.assertEqual(
            request.call_args_list[1].args,
            (
                "GET",
                "/api/boards/board/cards/search?query=bug&limit=25&cursor=next+page&include=body%2Cmetadata&include=notes&exclude=labels",
            ),
        )

    def test_cli_forwards_card_exploration_controls(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "list_cards",
            "board",
            "--limit",
            "25",
            "--cursor",
            "opaque",
            "--include", "body,metadata",
            "--include", "notes",
            "--exclude", "labels",
        ])

        with patch.object(client, "list_cards", return_value={}) as list_cards:
            args.func(args)

        list_cards.assert_called_once_with(
            "board",
            column_id=None,
            parent_id=None,
            priority=None,
            label=None,
            sort=None,
            limit=25,
            cursor="opaque",
            include=["body,metadata", "notes"],
            exclude=["labels"],
        )

    def test_mcp_dispatch_forwards_card_exploration_controls(self):
        from mcp_tools import cards

        arguments = {
            "board_id": "board",
            "query": "bug",
            "limit": 25,
            "cursor": "opaque",
            "include": ["body", "metadata"],
            "exclude": ["labels"],
        }
        with patch.object(client, "search_cards", return_value={}) as search_cards:
            cards.dispatch("search_cards", arguments)

        search_cards.assert_called_once_with(
            "board",
            query="bug",
            priority=None,
            label=None,
            sort=None,
            limit=25,
            cursor="opaque",
            include=["body", "metadata"],
            exclude=["labels"],
        )

    def test_relevance_progression_is_forwarded_across_surfaces(self):
        with patch.object(client, "_req", return_value={}) as request:
            client.relevant_candidates(
                "board", "bug", max_candidates=20, limit=5, cursor="next page",
                include="body", exclude=["labels", "priority"],
            )

        self.assertEqual(request.call_args.args, (
            "GET",
            "/api/boards/board/cards/relevant-candidates?query=bug&max_candidates=20&limit=5&cursor=next+page&include=body&exclude=labels&exclude=priority",
        ))

        parser = cli.build_parser()
        args = parser.parse_args([
            "relevant_candidates", "board", "bug", "--max-candidates", "20",
            "--limit", "5", "--cursor", "opaque", "--include", "body", "--exclude", "labels,priority",
        ])
        with patch.object(client, "relevant_candidates", return_value={}) as relevance:
            args.func(args)
        relevance.assert_called_once_with(
            "board", "bug", priority=None, label=None,
            max_candidates=20, limit=5, cursor="opaque", include=["body"], exclude=["labels,priority"],
        )

        from mcp_tools import cards

        with patch.object(client, "relevant_candidates", return_value={}) as relevance:
            cards.dispatch("relevant_candidates", {
                "board_id": "board", "query": "bug", "max_candidates": 20,
                "limit": 5, "cursor": "opaque", "include": ["body"], "exclude": ["labels"],
            })
        relevance.assert_called_once_with(
            "board", "bug", priority=None, label=None,
            max_candidates=20, limit=5, cursor="opaque", include=["body"], exclude=["labels"],
        )

    def test_change_voting_is_exposed_across_surfaces(self):
        choices = cli.build_parser()._subparsers._group_actions[0].choices
        self.assertIn("get_change_vote_audit", choices)

        vote_options = {
            option
            for action in choices["set_change_vote"]._actions
            for option in action.option_strings
        }
        self.assertTrue({
            "--provider", "--native-session-id", "--reviewed-commit-sha"
        }.issubset(vote_options))

        change_tools = {tool.name for tool in GROUPS["changes"].TOOLS}
        self.assertEqual(
            {"get_card_changes", "set_change_vote", "get_change_vote_audit"},
            change_tools,
        )


if __name__ == "__main__":
    unittest.main()
