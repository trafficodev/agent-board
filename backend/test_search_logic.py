import unittest
import tempfile
from pathlib import Path

from search_logic import ai_search_cards, parse_search_query, search_cards


def edge(edge_id, from_card_id, to_card_id, **overrides):
    data = {"id": edge_id, "from_card_id": from_card_id, "to_card_id": to_card_id, "type": "relates_to", "label": ""}
    data.update(overrides)
    return data


COLUMNS = [
    {"id": "features", "name": "Features"},
    {"id": "bugs", "name": "Bug Reports"},
]


def card(card_id, **overrides):
    data = {
        "id": card_id,
        "title": "",
        "body": "",
        "external_id": "",
        "column_id": "features",
        "parent_id": None,
        "position": 0,
        "priority": "medium",
        "labels": [],
        "metadata": {},
        "session_history": [],
    }
    data.update(overrides)
    return data


class SearchLogicTest(unittest.TestCase):
    def test_parses_file_and_commit_fields(self):
        self.assertEqual(
            parse_search_query('file:"frontend/src/App.tsx" -commit:abc1234')[0].field,
            "file",
        )
        self.assertEqual(
            parse_search_query('file:"frontend/src/App.tsx" -commit:abc1234')[1].field,
            "commit",
        )

    def test_searches_specific_file_and_specific_commit(self):
        cards = [
            card("file", body='edited_files: ["frontend/src/App.tsx"]'),
            card("commit", body='git_commits: ["abc1234 Fix board search"]'),
            card("other", body='edited_files: ["backend/main.py"]\ngit_commits: ["def5678 Other"]'),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, 'file:"src/App.tsx"')], ["file"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "commit:abc1234")], ["commit"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, 'file:backend commit:def5678')], ["other"])

    def test_searches_worktree_by_structured_metadata_and_has(self):
        cards = [
            card("dev", metadata={"worktrees": ["/repo/dev"]}),
            card("qa", body='worktrees: ["/repo-qa"]'),
            card("none", body="no worktree info here"),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "worktree:repo/dev")], ["dev"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "worktree:repo-qa")], ["qa"])
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "has:worktree")),
            ["dev", "qa"],
        )

    def test_keeps_hierarchy_visible_for_matches(self):
        cards = [
            card("feature", title="Image pipeline"),
            card("requirement", parent_id="feature", body='edited_files: ["frontend/src/image.ts"]', position=1),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "file:image.ts")], ["feature", "requirement"])

    def test_searches_file_contents_with_rg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            target = project / "frontend/src/App.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("function renderSessionData() { return true; }\n")

            cards = [
                card("match", body=f'projects: ["{project}"]\nedited_files: ["frontend/src/App.tsx"]'),
                card("miss", body=f'projects: ["{project}"]\nedited_files: ["frontend/src/Missing.tsx"]'),
            ]

            self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, 'contains:"renderSessionData"')], ["match"])

    def test_contains_searches_all_card_fields(self):
        cards = [
            card("external", external_id="assistant:sid-1"),
            card("title", title="Render session data"),
            card("body", body="collapsed preview"),
            card("label", labels=["bug_report"]),
            card("priority", priority="critical"),
            card("metadata", metadata={"turn_id": "assistant-turn-1", "edited_files": ["frontend/src/Meta.tsx"]}),
            card("session", session_history=[{"session_id": "sid-123", "system": "codex", "action": "audit", "outcome": "success", "timestamp": "now"}]),
            card("file", body='edited_files: ["frontend/src/App.tsx"]'),
            card("commit", body='git_commits: ["abc1234 Fix search"]'),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "external_id:sid-1")], ["external"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:session")], ["title", "session"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:preview")], ["body"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:bug_report")], ["label"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:critical")], ["priority"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "metadata:assistant-turn-1")], ["metadata"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "file:Meta.tsx")], ["metadata"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:App.tsx")], ["file"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:abc1234")], ["commit"])


    def test_regex_value_matches_pattern_not_substring(self):
        cards = [
            card("fix", title="Fix login bug"),
            card("feat", title="Add login button"),
            card("unrelated", title="Refactor tests"),
        ]

        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "title:/^Fix.*bug$/")],
            ["fix"],
        )
        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "/login/")],
            ["fix", "feat"],
        )

    def test_regex_case_insensitive_flag(self):
        cards = [card("a", title="URGENT: fix now")]
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "title:/urgent/")], [])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "title:/urgent/i")], ["a"])

    def test_invalid_regex_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_search_query("title:/[unclosed/")

    def test_contains_supports_regex_against_referenced_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            target = project / "frontend/src/App.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("function renderSessionData() { return 42; }\n")

            cards = [
                card("match", body=f'projects: ["{project}"]\nedited_files: ["frontend/src/App.tsx"]'),
            ]

            self.assertEqual(
                [item["id"] for item in search_cards(cards, COLUMNS, r'contains:/render\w+Data/')],
                ["match"],
            )
            self.assertEqual(
                [item["id"] for item in search_cards(cards, COLUMNS, r'contains:"/return \d\d/"')],
                ["match"],
            )

    def test_edges_are_searchable_by_type_label_and_connected_card_title(self):
        cards = [
            card("blocked", title="Wire the toast"),
            card("blocker", title="Install extensions live"),
            card("unrelated", title="Something else"),
        ]
        edges = [edge("e1", "blocked", "blocker", type="blocked_by", label="needs live install first")]

        # type and label are properties of the edge itself, not directional —
        # both endpoints see them.
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "edge_type:blocked_by", edges=edges)),
            ["blocked", "blocker"],
        )
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "has:edge", edges=edges)),
            ["blocked", "blocker"],
        )
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "contains:needs live install", edges=edges)),
            ["blocked", "blocker"],
        )
        # the connected card's OWN title is direction-sensitive: searching
        # "toast" (blocked's own title) finds blocker (the OTHER endpoint),
        # not blocked itself — this is what lets `edge:X` answer "which
        # cards are connected to something matching X" without knowing ids.
        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "edge:toast", edges=edges)],
            ["blocker"],
        )
        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "edge:extensions", edges=edges)],
            ["blocked"],
        )
        self.assertEqual(search_cards(cards, COLUMNS, "has:edge"), [])

    def test_ai_search_ranks_matches_and_returns_reasoning(self):
        cards = [
            card("body", body="Fix the login search panel"),
            card("title", title="Login search ranking"),
            card("label", labels=["login"]),
            card("miss", title="Unrelated"),
        ]

        result = ai_search_cards(cards, COLUMNS, "login")

        self.assertIsNone(result["error"])
        self.assertEqual([item["id"] for item in result["results"]], ["title", "label", "body"])
        self.assertIn("Ranked", result["reasoning"])

    def test_ai_search_rejects_empty_query(self):
        result = ai_search_cards([card("a")], COLUMNS, "  ")

        self.assertEqual(result["results"], [])
        self.assertEqual(result["error"], "empty_query")


if __name__ == "__main__":
    unittest.main()
