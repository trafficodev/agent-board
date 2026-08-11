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
import edge_store
from card_semantics import CardEvidence, CardSemantics
from models import CreateCard, CreateEdge, UpdateCard


class CardProjectionApiTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board("Progressive", "", ["Open"])
        self.column_id = self.board.columns[0].id
        self.client = TestClient(main.app)
        self.path = f"/api/boards/{self.board.id}/cards"

    def test_explicit_partial_test_evidence_does_not_claim_verified_coverage(self):
        coverage = card_projection.coverage_extras(
            [{
                "id": "requirement",
                "semantics": {
                    "kind": "requirement",
                    "evidence": [{"kind": "test", "locator": "test.py", "state": "partial"}],
                },
            }],
            [],
        )
        self.assertEqual(coverage["requirement"]["coverage"], "partial")

    def test_explicit_uncovered_evidence_remains_uncovered(self):
        coverage = card_projection.coverage_extras(
            [{
                "id": "requirement",
                "semantics": {
                    "kind": "requirement",
                    "evidence": [{"kind": "test", "locator": "test.py", "state": "uncovered"}],
                },
            }],
            [],
        )
        self.assertEqual(coverage["requirement"]["coverage"], "uncovered")

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

    def test_list_defaults_to_compact_envelope_and_include_star_expands(self):
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

        full = self.client.get(self.path, params={"include": "*"}).json()
        self.assertEqual(full["items"][0]["id"], card.id)
        self.assertEqual(full["items"][0]["body"], "x" * 300)
        self.assertIn("metadata", full["items"][0])

        fetched = self.client.get(f"{self.path}/{card.id}").json()
        self.assertEqual(fetched["body"], "x" * 300)

    def test_projection_fields_are_model_derived_and_endpoint_scoped(self):
        self.assertEqual(
            card_projection.CARD_FIELDS, tuple(card_projection.Card.model_fields)
        )
        self.assertIn(
            "relation_roles", card_projection.resolve_card_fields("search_cards")
        )
        self.assertIn(
            "relevance_score",
            card_projection.resolve_card_fields("relevant_candidates"),
        )

        for endpoint, wrong_field in (
            ("list_cards", "relation_roles"),
            ("search_cards", "relevance_score"),
            ("relevant_candidates", "relation_roles"),
        ):
            with self.subTest(endpoint=endpoint, wrong_field=wrong_field):
                with self.assertRaisesRegex(ValueError, "invalid_card_fields"):
                    card_projection.resolve_card_fields(endpoint, include=wrong_field)

    def test_projection_normalizes_include_exclude_and_wildcards(self):
        selected = card_projection.resolve_card_fields(
            "list_cards",
            include=[" body, metadata ", "body", "created_at"],
            exclude=[" labels ", "body"],
        )

        self.assertEqual(selected.count("metadata"), 1)
        self.assertIn("created_at", selected)
        self.assertNotIn("body", selected)
        self.assertNotIn("labels", selected)
        self.assertEqual(selected[0], "id")
        self.assertEqual(
            card_projection.resolve_card_fields("list_cards", include="*"),
            (*card_projection.CARD_FIELDS, *card_projection.DERIVED_FIELDS),
        )
        self.assertEqual(
            card_projection.resolve_card_fields(
                "relevant_candidates", include="*", exclude="*"
            ),
            ("id",),
        )
        self.assertIn(
            "id", card_projection.resolve_card_fields("list_cards", exclude="id")
        )

    def test_coverage_is_derived_for_requirements_and_catalog_rollups(self):
        area = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Area",
                column_id=self.column_id,
                semantics=CardSemantics(kind="product_area", catalog_lifecycle="active"),
            ),
        )
        feature = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Feature",
                column_id=self.column_id,
                parent_id=area.id,
                semantics=CardSemantics(kind="feature", catalog_lifecycle="active"),
            ),
        )
        requirement = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Requirement",
                column_id=self.column_id,
                parent_id=feature.id,
                semantics=CardSemantics(
                    kind="requirement",
                    catalog_lifecycle="active",
                    outcome="Coverage is visible",
                    acceptance_criteria=["Coverage is derived"],
                    evidence=[CardEvidence(kind="source", locator="backend/main.py")],
                ),
            ),
        )

        before = self.client.get(
            self.path,
            params={"limit": 100, "include": "coverage"},
        ).json()["items"]
        before_by_id = {item["id"]: item for item in before}
        self.assertEqual(before_by_id[requirement.id]["coverage"], "implemented")
        self.assertEqual(before_by_id[feature.id]["coverage"], "implemented")
        self.assertEqual(before_by_id[area.id]["coverage"], "implemented")

        test_card = card_store.create_card(
            self.board.id,
            CreateCard(
                title="Evidence",
                column_id=self.column_id,
                semantics=CardSemantics(kind="test"),
            ),
        )
        edge_store.create_edge(
            self.board.id,
            CreateEdge(
                from_card_id=test_card.id,
                to_card_id=requirement.id,
                type="verifies",
            ),
        )
        after = self.client.get(
            self.path,
            params={"limit": 100, "include": "coverage"},
        ).json()["items"]
        after_by_id = {item["id"]: item for item in after}
        self.assertEqual(after_by_id[requirement.id]["coverage"], "verified")
        self.assertEqual(after_by_id[feature.id]["coverage"], "verified")
        self.assertEqual(after_by_id[area.id]["coverage"], "verified")

    def test_projection_rejects_unknown_fields_and_malformed_wildcards(self):
        for values in ("unknown", "title,*suffix", ["title", 7]):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "invalid_card_fields"):
                    card_projection.resolve_card_fields("list_cards", include=values)

    def test_project_card_never_leaks_undeclared_values(self):
        projected = card_projection.project_card(
            {"id": "card", "title": "Title", "secret": "not declared"},
            card_projection.resolve_card_fields("relevant_candidates", exclude="*"),
            extras={
                "id": "overridden",
                "relevance_score": 1.0,
                "secret_extra": "not declared",
            },
        )

        self.assertEqual(projected, {"id": "card"})

    def test_cursor_binds_canonical_selected_fields(self):
        cards = [
            self.create_card(f"Card {index}").model_dump(mode="json")
            for index in range(2)
        ]
        arguments = {
            "cards": cards,
            "board_id": self.board.id,
            "endpoint": "list_cards",
            "request": {},
            "limit": 1,
            "cursor": None,
            "sort_key": lambda card: (card["position"], card["id"]),
        }
        first = card_projection.page_cards(
            **arguments,
            selected_fields=card_projection.resolve_card_fields(
                "list_cards", include=["metadata, body", "body"]
            ),
        )
        arguments["cursor"] = first["next_cursor"]

        resumed = card_projection.page_cards(
            **arguments,
            selected_fields=card_projection.resolve_card_fields(
                "list_cards", include=[" body ", "metadata"]
            ),
        )
        self.assertEqual(len(resumed["items"]), 1)
        self.assertEqual(
            set(resumed["items"][0]),
            set(card_projection.resolve_card_fields(
                "list_cards", include=["body", "metadata"]
            )),
        )
        with self.assertRaises(card_projection.InvalidCursor):
            card_projection.page_cards(
                **arguments,
                selected_fields=card_projection.resolve_card_fields(
                    "list_cards", include=["metadata", "created_at"]
                ),
            )

        arguments["cursor"] = None
        id_only = card_projection.resolve_card_fields("list_cards", exclude="*")
        first = card_projection.page_cards(
            **arguments, selected_fields=id_only
        )
        changed_cards = [dict(card) for card in cards]
        changed_cards[0]["metadata"] = {"changed": True}
        arguments["cards"] = changed_cards
        arguments["cursor"] = first["next_cursor"]
        with self.assertRaises(card_projection.StaleCursor):
            card_projection.page_cards(
                **arguments, selected_fields=id_only
            )

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

    def test_rest_accepts_repeated_and_comma_delimited_projection_values(self):
        self.create_card("Projected", body="complete", labels=["api"])

        response = self.client.get(
            self.path,
            params=[
                ("include", "body,metadata"),
                ("include", "created_at"),
                ("exclude", "labels,body_snippet"),
            ],
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertIn("body", item)
        self.assertIn("metadata", item)
        self.assertIn("created_at", item)
        self.assertNotIn("labels", item)
        self.assertNotIn("body_snippet", item)

    def test_rest_rejects_endpoint_extras_from_other_discovery_apis(self):
        self.create_card("Projected")

        for endpoint, field in (
            (self.path, "relation_roles"),
            (f"{self.path}/search", "relevance_score"),
            (f"{self.path}/relevant-candidates", "relation_roles"),
        ):
            with self.subTest(endpoint=endpoint, field=field):
                response = self.client.get(endpoint, params={"include": field})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["detail"], f"invalid_card_fields:{field}"
                )

    def test_rest_exclude_star_returns_id_only_on_every_discovery_endpoint(self):
        card = self.create_card("Needle")

        for endpoint, params in (
            (self.path, {"exclude": "*"}),
            (f"{self.path}/search", {"query": "Needle", "exclude": "*"}),
            (
                f"{self.path}/relevant-candidates",
                {"query": "Needle", "exclude": "*"},
            ),
        ):
            with self.subTest(endpoint=endpoint):
                result = self.client.get(endpoint, params=params).json()
                self.assertEqual(result["items"], [{"id": card.id}])

    def test_rest_can_exclude_computed_fields_without_changing_envelope(self):
        self.create_card("Needle")

        search = self.client.get(
            f"{self.path}/search",
            params={"query": "Needle", "exclude": "relation_roles"},
        ).json()
        relevant = self.client.get(
            f"{self.path}/relevant-candidates",
            params={
                "query": "Needle",
                "exclude": "relevance_score,column",
            },
        ).json()

        self.assertNotIn("relation_roles", search["items"][0])
        self.assertNotIn("relevance_score", relevant["items"][0])
        self.assertNotIn("column", relevant["items"][0])
        self.assertIn("error", relevant)

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

        expanded = self.client.get(
            f"{self.path}/search", params={"query": "Needle", "include": "*"}
        ).json()
        self.assertIsInstance(expanded, dict)
        self.assertIn("relation_roles", expanded["items"][0])

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
