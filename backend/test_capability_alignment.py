import inspect
import unittest
from unittest.mock import patch

import cli
import client
from mcp_tools import GROUPS
from card_semantics import RelationshipType
from models import ChangeContext
from typing import get_args


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

    def test_mcp_card_semantics_schema_and_sparse_forwarding(self):
        from mcp_tools import cards

        tools = {tool.name: tool for tool in cards.TOOLS}
        semantics = tools["create_card"].inputSchema["properties"]["semantics"]
        self.assertFalse(semantics["additionalProperties"])
        self.assertEqual(
            set(semantics["properties"]),
            {
                "kind", "catalog_lifecycle", "outcome", "acceptance_criteria",
                "exclusions", "owning_surface", "ownership",
                "evidence", "decisions",
            },
        )
        self.assertEqual(
            tools["update_card"].inputSchema["properties"]["semantics"],
            semantics,
        )
        self.assertEqual(
            tools["bulk_cards"].inputSchema["properties"]["operations"]
            ["items"]["oneOf"][0]["properties"]["semantics"],
            semantics,
        )
        bulk_variants = tools["bulk_cards"].inputSchema["properties"]["operations"]["items"]["oneOf"]
        self.assertEqual(
            [variant["properties"]["op"]["const"] for variant in bulk_variants],
            ["create", "update", "move", "delete", "note"],
        )

        required = {"board_id": "b", "title": "T", "column_id": "c"}
        with patch.object(client, "create_card", return_value={}) as create:
            cards.dispatch("create_card", required)
        create.assert_called_once_with(
            "b", "T", "c", change_context=None,
        )

        explicit = {
            **required,
            "body": "",
            "parent_id": None,
            "labels": [],
            "metadata": {},
            "semantics": {},
        }
        with patch.object(client, "create_card", return_value={}) as create:
            cards.dispatch("create_card", explicit)
        create.assert_called_once_with(
            "b", "T", "c", change_context=None,
            body="", parent_id=None, labels=[], metadata={}, semantics={},
        )

        with patch.object(client, "update_card", return_value={}) as update:
            cards.dispatch("update_card", {
                "board_id": "b", "card_id": "c", "body": "",
                "parent_id": None, "labels": [], "metadata": {}, "semantics": {},
            })
        update.assert_called_once_with(
            "b", "c", change_context=None, body="", parent_id=None,
            labels=[], metadata={}, semantics={},
        )

    def test_client_create_card_omits_unsupplied_defaults(self):
        context = ChangeContext.system("test")
        with patch.object(client, "_req", return_value={}) as request:
            client.create_card("board", "Title", "column", change_context=context)
            client.create_card(
                "board", "Title", "column", body="", parent_id=None,
                labels=[], metadata={}, semantics={},
                change_context=context,
            )
        self.assertEqual(
            request.call_args_list[0].args[2],
            {"title": "Title", "column_id": "column"},
        )
        self.assertEqual(
            request.call_args_list[1].args[2],
            {
                "title": "Title", "column_id": "column", "body": "",
                "parent_id": None, "labels": [], "metadata": {}, "semantics": {},
            },
        )

    def test_cli_card_semantics_and_explicit_clears_are_presence_based(self):
        parser = cli.build_parser()
        create_args = parser.parse_args([
            "create_card", "board", "Title", "column",
            "--semantics-json", '{"kind":"task"}',
        ])
        with patch.object(client, "create_card", return_value={}) as create:
            create_args.func(create_args)
        create.assert_called_once_with(
            "board", "Title", "column", semantics={"kind": "task"},
        )

        omitted = parser.parse_args(["update_card", "board", "card"])
        with patch.object(client, "update_card", return_value={}) as update:
            omitted.func(omitted)
        update.assert_called_once_with("board", "card")

        clearing = parser.parse_args([
            "update_card", "board", "card", "--clear-parent", "--labels",
            "--metadata-json", "{}", "--semantics-json", "{}",
        ])
        with patch.object(client, "update_card", return_value={}) as update:
            clearing.func(clearing)
        update.assert_called_once_with(
            "board", "card", parent_id=None, labels=[], metadata={}, semantics={},
        )

    def test_mcp_edge_schema_uses_exact_controlled_vocabulary(self):
        edge_tools = {tool.name: tool for tool in GROUPS["edges"].TOOLS}
        schema = edge_tools["create_edge"].inputSchema
        self.assertIn("type", schema["required"])
        self.assertEqual(
            schema["properties"]["type"]["enum"],
            list(get_args(RelationshipType)),
        )

        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["create_edge", "board", "from", "to"])
        for relationship in get_args(RelationshipType):
            args = parser.parse_args([
                "create_edge", "board", "from", "to", "--type", relationship,
            ])
            with patch.object(client, "create_edge", return_value={}) as create:
                args.func(args)
            create.assert_called_once_with(
                "board", "from", "to", type=relationship, label="",
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
