import unittest
import tempfile
from pathlib import Path

from search_logic import parse_search_query, relevant_candidates, search_cards, sort_cards


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
        "semantics": {},
        "session_history": [],
    }
    data.update(overrides)
    return data


class SearchLogicTest(unittest.TestCase):
    def test_searches_typed_semantic_fields_and_presence(self):
        cards = [
            card(
                "requirement",
                semantics={
                    "kind": "requirement",
                    "catalog_lifecycle": "active",
                    "outcome": "Users can inspect coverage",
                    "acceptance_criteria": ["Coverage is derived"],
                    "ownership": {"component": "discovery"},
                    "evidence": [{"kind": "test", "locator": "test_search.py"}],
                    "decisions": [{"id": "d1", "text": "Use typed fields"}],
                },
            ),
            card("task", semantics={"kind": "task"}),
        ]

        for query in (
            "kind:requirement", "lifecycle:active", "outcome:coverage",
            "acceptance:derived", "owner:discovery", "evidence:test_search.py",
            "decision:typed", "has:evidence", "has:decision",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    [item["id"] for item in search_cards(cards, COLUMNS, query)],
                    ["requirement"],
                )
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

    def test_relevant_candidates_returns_a_ranked_compact_shortlist(self):
        """A generic keyword-relevance shortlist: a compact candidate
        projection (not full cards), ordered by the same keyword scoring
        `search_cards` uses."""
        cards = [
            card("body", body="Fix the login search panel", labels=["ui"], priority="high"),
            card("title", title="Login search ranking"),
            card("label", labels=["login"]),
            card("miss", title="Unrelated"),
        ]

        result = relevant_candidates(cards, COLUMNS, "login")

        self.assertIsNone(result["error"])
        self.assertEqual([item["id"] for item in result["candidates"]], ["title", "label", "body"])
        body_candidate = result["candidates"][2]
        self.assertEqual(body_candidate["title"], "")
        self.assertEqual(body_candidate["body_snippet"], "Fix the login search panel")
        self.assertEqual(body_candidate["labels"], ["ui"])
        self.assertEqual(body_candidate["priority"], "high")
        self.assertEqual(body_candidate["column"], "Features")

    def test_relevant_candidates_caps_the_shortlist(self):
        cards = [card(f"c{i}", title="login") for i in range(50)]

        result = relevant_candidates(cards, COLUMNS, "login", max_candidates=5)

        self.assertEqual(len(result["candidates"]), 5)

    def test_relevant_candidates_truncates_long_body_snippets(self):
        cards = [card("a", body="x" * 500)]

        result = relevant_candidates(cards, COLUMNS, "x")

        snippet = result["candidates"][0]["body_snippet"]
        self.assertEqual(len(snippet), 241)  # 240 chars + the truncation marker
        self.assertTrue(snippet.endswith("…"))

    def test_relevant_candidates_rejects_empty_query(self):
        result = relevant_candidates([card("a")], COLUMNS, "  ")

        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["error"], "empty_query")

    def test_and_or_grouping_matches_any_branch(self):
        cards = [
            card("bug", labels=["bug"], priority="high"),
            card("regression", labels=["regression"], priority="high"),
            card("bug_low", labels=["bug"], priority="low"),
            card("other", labels=["feature"], priority="high"),
        ]

        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "(label:bug OR label:regression) priority:high")),
            ["bug", "regression"],
        )

    def test_nested_or_grouping(self):
        cards = [
            card("bug", labels=["bug"]),
            card("regression", labels=["regression"]),
            card("hotfix", labels=["hotfix"]),
            card("other", labels=["feature"]),
        ]

        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "((label:bug OR label:regression) OR label:hotfix)")),
            ["bug", "hotfix", "regression"],
        )

    def test_negated_group_excludes_any_branch_match(self):
        cards = [
            card("bug", labels=["bug"]),
            card("regression", labels=["regression"]),
            card("other", labels=["feature"]),
        ]

        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "-(label:bug OR label:regression)")],
            ["other"],
        )

    def test_bare_or_outside_parens_is_literal_term_not_an_operator(self):
        cards = [
            card("all_three", title="foo or bar"),
            card("foo_only", title="foo"),
            card("bar_only", title="bar"),
        ]

        # No parens: "OR" is not an operator here, so this is an implicit AND
        # of three literal terms ("foo", "or", "bar") — only a card
        # containing all three substrings matches.
        self.assertEqual(
            [item["id"] for item in search_cards(cards, COLUMNS, "foo OR bar")],
            ["all_three"],
        )

    def test_date_range_and_comparison_filters(self):
        cards = [
            card("jan", created_at="2026-01-15T00:00:00Z", updated_at="2026-01-15T00:00:00Z"),
            card("feb", created_at="2026-02-15T00:00:00Z", updated_at="2026-02-15T00:00:00Z"),
            card("mar", created_at="2026-03-15T00:00:00Z", updated_at="2026-03-15T00:00:00Z"),
        ]

        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "created:>2026-01-31")),
            ["feb", "mar"],
        )
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "updated:<2026-02-01")),
            ["jan"],
        )
        # Inclusive range: both bounds themselves match.
        self.assertEqual(
            sorted(item["id"] for item in search_cards(cards, COLUMNS, "created:2026-01-15..2026-02-15")),
            ["feb", "jan"],
        )

    def test_numeric_comparison_position_and_sessions(self):
        cards = [
            card("a", position=1, session_history=[{"session_id": "1"}, {"session_id": "2"}, {"session_id": "3"}, {"session_id": "4"}]),
            card("b", position=10, session_history=[{"session_id": "1"}]),
            card("c", position=3, session_history=[]),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "position:<5")], ["a", "c"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "sessions:>3")], ["a"])
        # Negation composes with numeric comparison.
        self.assertEqual(sorted(item["id"] for item in search_cards(cards, COLUMNS, "-sessions:>3")), ["b", "c"])

    def test_regex_value_syntax_rejected_on_comparable_fields(self):
        with self.assertRaises(ValueError):
            parse_search_query("position:/1/")

    def test_invalid_date_operand_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_search_query("created:>not-a-date")

    def test_combines_grouping_numeric_and_legacy_syntax_in_one_query(self):
        cards = [
            card("bug_many_sessions", labels=["bug"], session_history=[{"session_id": "1"}, {"session_id": "2"}]),
            card("regression_many_sessions", labels=["regression"], session_history=[{"session_id": "1"}, {"session_id": "2"}]),
            card("bug_one_session", labels=["bug"], session_history=[{"session_id": "1"}]),
            card("bug_blocked_commit", labels=["bug"], session_history=[{"session_id": "1"}, {"session_id": "2"}], body='git_commits: ["abc1234 old"]'),
        ]

        self.assertEqual(
            sorted(
                item["id"]
                for item in search_cards(
                    cards, COLUMNS, "(label:bug OR label:regression) sessions:>1 -commit:abc1234",
                )
            ),
            ["bug_many_sessions", "regression_many_sessions"],
        )

    def test_sort_cards_supports_public_sort_modes(self):
        cards = [
            card("low", title="Zulu", position=0, priority="low", updated_at="2026-01-01T00:00:00Z", session_history=[]),
            card("critical", title="Alpha", position=1, priority="critical", updated_at="2026-01-03T00:00:00Z", session_history=[{"session_id": "1"}, {"session_id": "2"}]),
            card("high", title="Beta", position=2, priority="high", updated_at="2026-01-02T00:00:00Z", session_history=[{"session_id": "1"}]),
        ]

        self.assertEqual([item["id"] for item in sort_cards(cards, "board")], ["low", "critical", "high"])
        self.assertEqual([item["id"] for item in sort_cards(cards, "updated_desc")], ["critical", "high", "low"])
        self.assertEqual([item["id"] for item in sort_cards(cards, "priority_desc")], ["critical", "high", "low"])
        self.assertEqual([item["id"] for item in sort_cards(cards, "title_asc")], ["critical", "high", "low"])
        self.assertEqual([item["id"] for item in sort_cards(cards, "sessions_desc")], ["critical", "high", "low"])


if __name__ == "__main__":
    unittest.main()
