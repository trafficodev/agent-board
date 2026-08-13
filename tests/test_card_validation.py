import os
import subprocess
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["AGENT_BOARD_HOME"] = tempfile.mkdtemp(prefix="agent-board-validation-test-")

from agent_board import board_store
from agent_board import card_store
from agent_board import card_validation
from agent_board import db
from agent_board import edge_store
from agent_board import validation_worker
from agent_board.models import AddNote, AnswerNote, CardSemantics, CreateCard, Edge, MoveCard, UpdateCard


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="agent-board-validation-repo-"))
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "tracked.py").write_text("x = 1\n")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-m", "first")
    return repo


class CardValidationTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board(
            "V", "", ["Open Items", "In Progress", "In Testing", "Done"]
        )
        self.open_id = self.board.columns[0].id
        self.progress_id = self.board.columns[1].id
        self.testing_id = self.board.columns[2].id
        self.done_id = self.board.columns[3].id

    def _scan(self, now=None):
        cards = card_store.list_cards(self.board.id)
        board = board_store.get_board(self.board.id)
        return card_validation.scan_board(
            board, cards, now=now, edges=edge_store.list_edges(self.board.id)
        )

    def _checks(self, now=None):
        return {f.check for f in self._scan(now=now)}

    def _card(self, **kwargs):
        kwargs.setdefault("title", "subject")
        kwargs.setdefault("column_id", self.open_id)
        return card_store.create_card(self.board.id, CreateCard(**kwargs))

    def test_invalid_semantic_hierarchy_is_reported(self):
        area = self._card(semantics=CardSemantics(
            kind="product_area", catalog_lifecycle="active"
        ))
        task = self._card(semantics=CardSemantics(kind="task"))
        with db.transaction() as conn:
            conn.execute("UPDATE cards SET parent_id=? WHERE id=?", (area.id, task.id))
        self.assertEqual(
            [finding.card_id for finding in self._scan() if finding.check == "semantic_hierarchy"],
            [task.id],
        )

    def test_legacy_relationship_and_dependency_cycle_are_reported(self):
        first = self._card(semantics=CardSemantics(kind="task"))
        second = self._card(semantics=CardSemantics(kind="task"))
        with db.transaction() as conn:
            db.write_edge(conn, Edge(
                board_id=self.board.id,
                from_card_id=first.id,
                to_card_id=second.id,
                type="custom_relation",
            ))
            db.write_edge(conn, Edge(
                board_id=self.board.id,
                from_card_id=first.id,
                to_card_id=second.id,
                type="depends_on",
            ))
            db.write_edge(conn, Edge(
                board_id=self.board.id,
                from_card_id=second.id,
                to_card_id=first.id,
                type="depends_on",
            ))
        findings = [f for f in self._scan() if f.check == "relationship_integrity"]
        self.assertTrue(any("legacy relationship type" in finding.body for finding in findings))
        self.assertTrue(any("cycle" in finding.title for finding in findings))

    # --- git-backed checks ---

    def test_missing_commit_is_reported_and_real_commit_is_not(self):
        repo = _make_repo()
        real = _git(repo, "rev-parse", "HEAD")
        self._card(metadata={"worktrees": [str(repo)], "git_commits": [real]})
        self.assertNotIn("commit_missing", self._checks())

        self._card(metadata={
            "worktrees": [str(repo)],
            "git_commits": ["0123456789abcdef0123456789abcdef01234567"],
        })
        self.assertIn("commit_missing", self._checks())

    def test_commit_on_no_branch_is_reported_as_unreachable(self):
        repo = _make_repo()
        on_branch = _git(repo, "rev-parse", "HEAD")
        # A commit created then abandoned: reachable by hash, contained by nothing.
        _git(repo, "checkout", "--detach")
        (repo / "dangling.py").write_text("y = 2\n")
        _git(repo, "add", "dangling.py")
        _git(repo, "commit", "-m", "dangling")
        dangling = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "main")

        card = self._card(metadata={"worktrees": [str(repo)], "git_commits": [on_branch]})
        self.assertNotIn("commit_unreachable", self._checks())

        card_store.update_card(self.board.id, card.id, UpdateCard(
            metadata={"worktrees": [str(repo)], "git_commits": [dangling]},
        ))
        findings = [f for f in self._scan() if f.check == "commit_unreachable"]
        self.assertEqual(len(findings), 1)
        self.assertIn(dangling, findings[0].title)
        # It exists, so it must not also be reported as missing.
        self.assertNotIn("commit_missing", self._checks())

    def test_missing_files_reported_against_the_cards_own_repo(self):
        repo = _make_repo()
        self._card(metadata={"worktrees": [str(repo)], "files": ["tracked.py"]})
        self.assertNotIn("missing_files", self._checks())

        self._card(metadata={"worktrees": [str(repo)], "files": ["tracked.py", "gone.py"]})
        findings = [f for f in self._scan() if f.check == "missing_files"]
        self.assertEqual(len(findings), 1)
        self.assertIn("gone.py", findings[0].body)
        self.assertNotIn("- tracked.py", findings[0].body)

    def test_no_repo_means_no_git_findings_rather_than_false_ones(self):
        """A card whose worktree is gone cannot have its commits checked, and
        must not be reported as citing missing commits because of it."""
        self._card(metadata={
            "worktrees": ["/nonexistent/checkout"],
            "git_commits": ["0123456789abcdef0123456789abcdef01234567"],
            "files": ["whatever.py"],
        })
        checks = self._checks()
        self.assertIn("stale_worktree", checks)
        self.assertNotIn("commit_missing", checks)
        self.assertNotIn("missing_files", checks)

    def test_annotated_worktree_paths_resolve(self):
        repo = _make_repo()
        real = _git(repo, "rev-parse", "HEAD")
        self._card(metadata={"worktrees": [f"{repo} (dev)"], "git_commits": [real]})
        checks = self._checks()
        self.assertNotIn("stale_worktree", checks)
        self.assertNotIn("commit_missing", checks)

    # --- board-shape checks ---

    def test_parent_behind_every_child_is_reported(self):
        parent = self._card(title="parent", column_id=self.progress_id)
        self._card(title="a", column_id=self.testing_id, parent_id=parent.id)
        self._card(title="b", column_id=self.testing_id, parent_id=parent.id)
        findings = [f for f in self._scan() if f.check == "parent_behind_children"]
        self.assertEqual([f.card_id for f in findings], [parent.id])

        # One child falling back to the parent's column clears it.
        child = card_store.list_cards(self.board.id, parent_id=parent.id)[0]
        card_store.move_card(self.board.id, child.id, MoveCard(column_id=self.progress_id))
        self.assertNotIn("parent_behind_children", self._checks())

    def test_parent_closed_before_children_is_reported(self):
        parent = self._card(title="parent", column_id=self.done_id)
        self._card(title="child", column_id=self.open_id, parent_id=parent.id)
        findings = [f for f in self._scan() if f.check == "parent_closed_before_children"]
        self.assertEqual([f.card_id for f in findings], [parent.id])

    def test_dangling_card_reference_is_reported_but_real_ids_are_not(self):
        target = self._card(title="target")
        self._card(title="cites real", body=f"see {target.id} for context")
        self.assertNotIn("dangling_card_reference", self._checks())

        self._card(title="cites ghost", body="superseded by deadbeef1234")
        findings = [f for f in self._scan() if f.check == "dangling_card_reference"]
        self.assertEqual(len(findings), 1)
        self.assertIn("deadbeef1234", findings[0].body)

    def test_declared_commit_hash_is_not_mistaken_for_a_card_reference(self):
        self._card(
            title="commit in prose",
            body="fixed in 0123456789ab",
            metadata={"git_commits": ["0123456789ab"]},
        )
        self.assertNotIn("dangling_card_reference", self._checks())

    def test_malformed_labels_are_reported(self):
        self._card(title="ok", labels=["infra", "testing"])
        self.assertNotIn("malformed_labels", self._checks())

        self._card(title="broken", labels=["infra,routines,testing,blocked"])
        findings = [f for f in self._scan() if f.check == "malformed_labels"]
        self.assertEqual(len(findings), 1)

    def test_stale_open_question_reported_only_after_the_cutoff(self):
        card = self._card(title="asks")
        card_store.add_note(self.board.id, card.id, AddNote(kind="question", text="which?"))
        now = datetime.now(timezone.utc)
        self.assertNotIn("stale_open_question", self._checks(now=now))

        later = now + card_validation.STALE_QUESTION_AGE + timedelta(days=1)
        self.assertIn("stale_open_question", self._checks(now=later))

    def test_answered_question_never_goes_stale(self):
        card = self._card(title="asks")
        updated = card_store.add_note(
            self.board.id, card.id, AddNote(kind="question", text="which?")
        )
        card_store.answer_note(
            self.board.id, card.id, updated.notes[-1].id, AnswerNote(answer="that one")
        )
        later = datetime.now(timezone.utc) + card_validation.STALE_QUESTION_AGE + timedelta(days=1)
        self.assertNotIn("stale_open_question", self._checks(now=later))


class ValidationWorkerTest(unittest.TestCase):
    def setUp(self):
        self.board = board_store.create_board(
            "W", "", ["Open Items", "In Progress", "Done"]
        )
        self.open_id = self.board.columns[0].id
        self.progress_id = self.board.columns[1].id
        self.done_id = self.board.columns[2].id

    def _validator_cards(self):
        return [
            c for c in card_store.list_cards(self.board.id)
            if c.external_id.startswith(validation_worker.VALIDATOR_PREFIX)
        ]

    def _break_labels(self):
        return card_store.create_card(self.board.id, CreateCard(
            title="subject", column_id=self.open_id, labels=["a,b,c"],
        ))

    def test_finding_becomes_one_card_and_rescanning_does_not_duplicate(self):
        self._break_labels()
        first = validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(first["filed"], 1)
        self.assertEqual(len(self._validator_cards()), 1)

        second = validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(second["filed"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(len(self._validator_cards()), 1)

    def test_filed_card_records_its_check_and_subject(self):
        subject = self._break_labels()
        validation_worker.scan_and_apply(self.board.id)
        card = self._validator_cards()[0]
        self.assertEqual(card.metadata["validator_check"], "malformed_labels")
        self.assertEqual(card.metadata["validator_subject_card_id"], subject.id)
        self.assertEqual(card.column_id, self.open_id)

    def test_fixing_the_subject_closes_the_filed_card_with_a_note(self):
        subject = self._break_labels()
        validation_worker.scan_and_apply(self.board.id)
        filed = self._validator_cards()[0]

        card_store.update_card(self.board.id, subject.id, UpdateCard(labels=["a", "b", "c"]))
        result = validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(result["resolved"], 1)

        closed = card_store.get_card(self.board.id, filed.id)
        self.assertEqual(closed.column_id, self.done_id)
        self.assertIn(validation_worker.RESOLVED_NOTE, [n.text for n in closed.notes])

    def test_progress_on_a_filed_card_is_not_undone_by_the_next_scan(self):
        self._break_labels()
        validation_worker.scan_and_apply(self.board.id)
        filed = self._validator_cards()[0]
        card_store.move_card(self.board.id, filed.id, MoveCard(column_id=self.progress_id))

        validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(
            card_store.get_card(self.board.id, filed.id).column_id, self.progress_id
        )

    def test_a_finding_that_returns_reopens_its_closed_card(self):
        subject = self._break_labels()
        validation_worker.scan_and_apply(self.board.id)
        filed = self._validator_cards()[0]
        card_store.update_card(self.board.id, subject.id, UpdateCard(labels=["a", "b"]))
        validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(card_store.get_card(self.board.id, filed.id).column_id, self.done_id)

        card_store.update_card(self.board.id, subject.id, UpdateCard(labels=["a,b"]))
        validation_worker.scan_and_apply(self.board.id)
        reopened = card_store.get_card(self.board.id, filed.id)
        self.assertEqual(reopened.column_id, self.open_id)
        self.assertEqual(len(self._validator_cards()), 1)

    def test_worker_never_reports_on_its_own_cards(self):
        """Validator cards carry a `validator` label and cite the subject's id;
        neither may become a finding, or the worker feeds on itself."""
        self._break_labels()
        validation_worker.scan_and_apply(self.board.id)
        before = len(self._validator_cards())
        for _ in range(3):
            validation_worker.scan_and_apply(self.board.id)
        self.assertEqual(len(self._validator_cards()), before)

    def test_interval_is_clamped_and_falls_back_on_garbage(self):
        with unittest.mock.patch.dict(
            os.environ, {"AGENT_BOARD_VALIDATION_INTERVAL_SECONDS": "1"}
        ):
            self.assertEqual(
                validation_worker.interval_seconds(),
                validation_worker.MIN_INTERVAL_SECONDS,
            )
        with unittest.mock.patch.dict(
            os.environ, {"AGENT_BOARD_VALIDATION_INTERVAL_SECONDS": "not-a-number"}
        ):
            self.assertEqual(
                validation_worker.interval_seconds(),
                validation_worker.DEFAULT_INTERVAL_SECONDS,
            )

    def test_disabled_by_env(self):
        with unittest.mock.patch.dict(os.environ, {"AGENT_BOARD_VALIDATION_ENABLED": "0"}):
            self.assertFalse(validation_worker.is_enabled())
        with unittest.mock.patch.dict(os.environ, {"AGENT_BOARD_VALIDATION_ENABLED": "1"}):
            self.assertTrue(validation_worker.is_enabled())


if __name__ == "__main__":
    unittest.main()
