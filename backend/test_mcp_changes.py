import unittest
from unittest.mock import patch

from models import ChangeContext
from mcp_tools import cards, changes
from mcp_tools.context import change_context_from_arguments


class MCPChangeToolsTest(unittest.TestCase):
    def test_vote_tools_expose_set_get_and_audit(self):
        self.assertEqual(
            {tool.name for tool in changes.TOOLS},
            {"get_card_changes", "set_change_vote", "get_change_vote_audit"},
        )
        set_vote = next(tool for tool in changes.TOOLS if tool.name == "set_change_vote")
        self.assertTrue({
            "provider", "native_session_id", "reviewed_commit_sha"
        }.issubset(set_vote.inputSchema["required"]))

    def test_mutating_card_tools_accept_provider_injected_context(self):
        create = next(tool for tool in cards.TOOLS if tool.name == "create_card")
        self.assertTrue({
            "provider", "native_session_id", "reviewed_commit_sha"
        }.issubset(create.inputSchema["properties"]))

        context = change_context_from_arguments({
            "provider": "codex",
            "native_session_id": "native-thread",
            "reviewed_commit_sha": "a" * 40,
        })
        self.assertEqual(
            context,
            ChangeContext.authored("codex", "native-thread", "a" * 40),
        )

    def test_partial_injected_context_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "native_session_id"):
            change_context_from_arguments({"provider": "codex"})

    def test_set_vote_dispatch_forwards_the_explicit_identity(self):
        arguments = {
            "board_id": "board",
            "card_id": "card",
            "target_id": "a" * 64,
            "direction": -1,
            "provider": "gemini",
            "native_session_id": "native-chat",
            "reviewed_commit_sha": "b" * 40,
        }
        with patch.object(changes.client, "set_change_vote", return_value={}) as set_vote:
            changes.dispatch("set_change_vote", arguments)

        set_vote.assert_called_once_with(
            "board", "card", "a" * 64, -1, "gemini", "native-chat", "b" * 40
        )


if __name__ == "__main__":
    unittest.main()
