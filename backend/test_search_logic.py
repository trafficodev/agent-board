import unittest

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


if __name__ == "__main__":
    unittest.main()
