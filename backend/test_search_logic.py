import unittest
import tempfile
from pathlib import Path

from search_logic import parse_search_query, search_cards


COLUMNS = [
    {"id": "features", "name": "Features"},
    {"id": "bugs", "name": "Bug Reports"},
]


def card(card_id, **overrides):
    data = {
        "id": card_id,
        "title": "",
        "body": "",
        "column_id": "features",
        "parent_id": None,
        "position": 0,
        "priority": "medium",
        "labels": [],
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
            card("title", title="Render session data"),
            card("body", body="collapsed preview"),
            card("label", labels=["bug_report"]),
            card("priority", priority="critical"),
            card("session", session_history=[{"session_id": "sid-123", "system": "codex", "action": "audit", "outcome": "success", "timestamp": "now"}]),
            card("file", body='edited_files: ["frontend/src/App.tsx"]'),
            card("commit", body='git_commits: ["abc1234 Fix search"]'),
        ]

        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:session")], ["title", "session"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:preview")], ["body"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:bug_report")], ["label"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:critical")], ["priority"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:App.tsx")], ["file"])
        self.assertEqual([item["id"] for item in search_cards(cards, COLUMNS, "contains:abc1234")], ["commit"])


if __name__ == "__main__":
    unittest.main()
