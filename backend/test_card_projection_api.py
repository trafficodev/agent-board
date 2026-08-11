import base64
import hashlib
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


TEST_HOME = tempfile.TemporaryDirectory(prefix="agent-board-card-projection-test-")
os.environ["AGENT_BOARD_HOME"] = TEST_HOME.name

import board_store
import card_projection
import card_store
import main
import paths
from models import CreateCard, UpdateCard


class CardProjectionApiTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board("Progressive", "", ["Open"])
        self.column_id = self.board.columns[0].id
        self.client = TestClient(main.app)
        self.path = f"/api/boards/{self.board.id}/cards"

    def create_card(self, title, *, body="", parent_id=None, priority="medium", labels=None):
        return card_store.create_card(
            self.board.id,
            CreateCard(
                title=title,
                body=body,
                column_id=self.column_id,
                parent_id=parent_id,
                priority=priority,
                labels=labels or [],
            ),
        )

    def test_list_defaults_to_bounded_compact_envelope_and_full_is_explicit(self):
        card = self.create_card(
            "Compact", body="x" * 300, priority="high", labels=["api"]
        )

        compact = self.client.get(self.path).json()

        self.assertEqual(set(compact), {"items", "next_cursor", "has_more"})
        self.assertEqual(
            set(compact["items"][0]),
            {
                "id", "title", "column_id", "parent_id", "priority", "labels",
                "body_snippet", "has_more_body",
            },
        )
        self.assertTrue(compact["items"][0]["has_more_body"])
        self.assertNotIn("body", compact["items"][0])

        full = self.client.get(self.path, params={"detail": "full"}).json()
        self.assertIsInstance(full, list)
        self.assertEqual(full[0]["id"], card.id)
        self.assertEqual(full[0]["body"], "x" * 300)
        self.assertIn("metadata", full[0])

        fetched = self.client.get(f"{self.path}/{card.id}").json()
        self.assertEqual(fetched["body"], "x" * 300)

    def test_list_cursor_traverses_each_result_once_and_is_request_bound(self):
        cards = [self.create_card(f"Card {index}") for index in range(5)]

        first = self.client.get(self.path, params={"limit": 2}).json()
        second = self.client.get(
            self.path, params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()
        third = self.client.get(
            self.path, params={"limit": 2, "cursor": second["next_cursor"]}
        ).json()

        traversed = [item["id"] for page in (first, second, third) for item in page["items"]]
        self.assertEqual(traversed, [card.id for card in cards])
        self.assertEqual(len(traversed), len(set(traversed)))
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_cursor"])

        changed_limit = self.client.get(
            self.path, params={"limit": 3, "cursor": first["next_cursor"]}
        )
        self.assertEqual(changed_limit.status_code, 400)
        self.assertEqual(changed_limit.json()["detail"], "invalid_cursor")

    def test_cursor_rejects_changed_ordered_results(self):
        first_card = self.create_card("First")
        self.create_card("Second")
        first = self.client.get(self.path, params={"limit": 1}).json()

        card_store.update_card(
            self.board.id, first_card.id, UpdateCard(title="Changed")
        )
        resumed = self.client.get(
            self.path, params={"limit": 1, "cursor": first["next_cursor"]}
        )

        self.assertEqual(resumed.status_code, 400)
        self.assertEqual(resumed.json()["detail"], "stale_cursor")

    def test_cursor_rejects_payload_forged_with_public_checksum(self):
        self.create_card("First")
        second = self.create_card("Second")
        cursor = self.client.get(self.path, params={"limit": 1}).json()["next_cursor"]
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        payload = json.loads(decoded[card_projection._CURSOR_SIGNATURE_BYTES:])
        payload["after"] = [self.column_id, 1, second.id]
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        forged = base64.urlsafe_b64encode(
            hashlib.sha256(encoded).digest()[:12] + encoded
        ).decode().rstrip("=")

        response = self.client.get(
            self.path, params={"limit": 1, "cursor": forged}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "invalid_cursor")

    def test_cursor_secret_is_private_and_survives_secret_reload(self):
        self.create_card("First")
        self.create_card("Second")
        first = self.client.get(self.path, params={"limit": 1}).json()
        secret_path = paths.cursor_secret_path()

        self.assertEqual(secret_path.stat().st_mode & 0o777, 0o600)
        card_projection._load_cursor_secret.cache_clear()
        resumed = self.client.get(
            self.path, params={"limit": 1, "cursor": first["next_cursor"]}
        )

        self.assertEqual(resumed.status_code, 200)

    def test_malformed_cursors_are_rejected(self):
        self.create_card("First")
        self.create_card("Second")

        for cursor in ("not!base64", "eA", "e30"):
            with self.subTest(cursor=cursor):
                response = self.client.get(
                    self.path, params={"limit": 1, "cursor": cursor}
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "invalid_cursor")

    def test_full_detail_rejects_paging_controls(self):
        self.create_card("Full")

        for endpoint, extra in (
            (self.path, {}),
            (f"{self.path}/search", {"query": "Full"}),
        ):
            with self.subTest(endpoint=endpoint, control="limit"):
                response = self.client.get(
                    endpoint, params={"detail": "full", "limit": 1, **extra}
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["detail"], "full_detail_cannot_paginate"
                )
            with self.subTest(endpoint=endpoint, control="cursor"):
                response = self.client.get(
                    endpoint,
                    params={"detail": "full", "cursor": "opaque", **extra},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["detail"], "full_detail_cannot_paginate"
                )

    def test_search_cursor_detects_changed_external_contains_results(self):
        with tempfile.TemporaryDirectory(prefix="agent-board-contains-") as project:
            source = os.path.join(project, "source.txt")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("needle")
            self.create_card(
                "External",
                body="edited_files: [source.txt]",
                labels=["external"],
            )
            external = card_store.list_cards(self.board.id)[0]
            card_store.update_card(
                self.board.id,
                external.id,
                UpdateCard(metadata={
                    "projects": [project],
                    "edited_files": ["source.txt"],
                }),
            )
            self.create_card("Internal", body="needle")
            first = self.client.get(
                f"{self.path}/search", params={"query": "contains:needle", "limit": 1}
            ).json()

            with open(source, "w", encoding="utf-8") as handle:
                handle.write("absent")
            resumed = self.client.get(
                f"{self.path}/search",
                params={
                    "query": "contains:needle",
                    "limit": 1,
                    "cursor": first["next_cursor"],
                },
            )

        self.assertEqual(resumed.status_code, 400)
        self.assertEqual(resumed.json()["detail"], "stale_cursor")

    def test_search_flattens_hierarchy_with_roles_before_paging(self):
        parent = self.create_card("Parent")
        match = self.create_card("Needle", parent_id=parent.id)
        child = self.create_card("Child", parent_id=match.id)
        self.create_card("Unrelated")

        pages = []
        cursor = None
        while True:
            params = {"query": "Needle", "limit": 1}
            if cursor:
                params["cursor"] = cursor
            page = self.client.get(f"{self.path}/search", params=params).json()
            self.assertLessEqual(len(page["items"]), 1)
            pages.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break

        by_id = {item["id"]: item for item in pages}
        self.assertEqual(set(by_id), {parent.id, match.id, child.id})
        self.assertEqual(by_id[parent.id]["relation_roles"], ["ancestor"])
        self.assertEqual(by_id[match.id]["relation_roles"], ["direct_match"])
        self.assertEqual(by_id[child.id]["relation_roles"], ["descendant"])

        full = self.client.get(
            f"{self.path}/search", params={"query": "Needle", "detail": "full"}
        ).json()
        self.assertIsInstance(full, list)
        self.assertNotIn("relation_roles", full[0])

    def test_relevant_candidates_preserve_ranked_context_across_pages(self):
        cards = [self.create_card(f"Needle {index}") for index in range(5)]
        traversed = []
        cursor = None

        while True:
            params = {"query": "needle", "limit": 2, "max_candidates": 5}
            if cursor:
                params["cursor"] = cursor
            page = self.client.get(
                f"{self.path}/relevant-candidates", params=params
            ).json()
            self.assertLessEqual(len(page["items"]), 2)
            self.assertIsNone(page["error"])
            traversed.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break

        self.assertEqual([item["id"] for item in traversed], [card.id for card in cards])
        self.assertEqual(len({item["id"] for item in traversed}), 5)
        self.assertTrue(all(item["relevance_score"] > 0 for item in traversed))
        self.assertTrue(all(item["column"] == "Open" for item in traversed))
        self.assertTrue(all("body_snippet" in item for item in traversed))

        first = self.client.get(
            f"{self.path}/relevant-candidates",
            params={"query": "needle", "limit": 1, "max_candidates": 5},
        ).json()
        rebound = self.client.get(
            f"{self.path}/relevant-candidates",
            params={
                "query": "needle",
                "limit": 1,
                "max_candidates": 4,
                "cursor": first["next_cursor"],
            },
        )
        self.assertEqual(rebound.status_code, 400)
        self.assertEqual(rebound.json()["detail"], "invalid_cursor")

    def test_relevant_candidates_keep_empty_query_error_in_envelope(self):
        self.create_card("Card")

        result = self.client.get(f"{self.path}/relevant-candidates").json()

        self.assertEqual(result, {
            "items": [],
            "next_cursor": None,
            "has_more": False,
            "error": "empty_query",
        })

    def test_relevant_candidates_report_stale_cursor_as_bad_request(self):
        first_card = self.create_card("Needle first")
        self.create_card("Needle second")
        first = self.client.get(
            f"{self.path}/relevant-candidates",
            params={"query": "needle", "limit": 1},
        ).json()
        card_store.update_card(
            self.board.id, first_card.id, UpdateCard(title="Changed")
        )

        response = self.client.get(
            f"{self.path}/relevant-candidates",
            params={"query": "needle", "limit": 1, "cursor": first["next_cursor"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "stale_cursor")


if __name__ == "__main__":
    unittest.main()
