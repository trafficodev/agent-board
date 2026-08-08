import unittest

import provider_hook


class ProviderHookTest(unittest.TestCase):
    def test_mutation_gets_native_session_provider_and_exact_head(self):
        updated = provider_hook.inject_context(
            {
                "session_id": "native-codex-thread",
                "cwd": "/repo",
                "tool_name": "mcp__agent_board__set_change_vote",
                "tool_input": {"direction": 1, "reviewed_commit_sha": "f" * 40},
            },
            "codex",
            lambda cwd: "a" * 40 if cwd == "/repo" else "",
        )

        self.assertEqual(updated["provider"], "codex")
        self.assertEqual(updated["native_session_id"], "native-codex-thread")
        self.assertEqual(updated["reviewed_commit_sha"], "a" * 40)

    def test_read_gets_identity_without_git_lookup(self):
        updated = provider_hook.inject_context(
            {
                "session_id": "native-gemini-chat",
                "cwd": "/not-a-repo",
                "tool_name": "mcp_agent_board_get_card_changes",
                "tool_input": {"card_id": "card"},
            },
            "gemini",
            lambda _cwd: self.fail("read should not resolve Git"),
        )

        self.assertEqual(updated["native_session_id"], "native-gemini-chat")
        self.assertNotIn("reviewed_commit_sha", updated)

    def test_missing_native_session_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "native session"):
            provider_hook.inject_context(
                {
                    "cwd": "/repo",
                    "tool_name": "mcp__agent_board__create_card",
                    "tool_input": {},
                },
                "claude",
            )

    def test_unrelated_tool_with_matching_suffix_is_ignored(self):
        payload = {
            "session_id": "native",
            "cwd": "/repo",
            "tool_name": "mcp__other_server__create_card",
            "tool_input": {},
        }

        self.assertIs(provider_hook.inject_context(payload, "codex"), payload)

    def test_provider_outputs_use_native_rewrite_contracts(self):
        payload = {
            "session_id": "native",
            "cwd": "/repo",
            "tool_name": "mcp_agent_board_get_card_changes",
            "tool_input": {},
        }
        gemini = provider_hook.hook_output(payload, "gemini")
        claude = provider_hook.hook_output(
            {**payload, "tool_name": "mcp__agent_board__get_card_changes"},
            "claude",
        )

        self.assertIn("tool_input", gemini["hookSpecificOutput"])
        self.assertIn("updatedInput", claude["hookSpecificOutput"])


if __name__ == "__main__":
    unittest.main()
