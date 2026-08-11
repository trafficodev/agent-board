"""Deterministic card checks.

Everything here is a pure function of board state plus the filesystem/git facts
a card claims. No model, no judgement, no heuristics: a check either proves a
card's own metadata contradicts reality, or it stays silent. What to do about a
finding is left to whoever picks up the card the worker files.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import edge_store
from card_semantics import valid_semantic_parent
from card_store import is_open_question
from models import Board, Card, Edge, RelationshipType

# Card ids are 12-char hex. Bodies and notes reference other cards by bare id,
# so the same shape is what we look for when hunting dangling references.
_CARD_ID_RE = re.compile(r"\b[0-9a-f]{12}\b")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

GIT_TIMEOUT_SECONDS = 15
STALE_QUESTION_AGE = timedelta(days=7)


@dataclass(frozen=True)
class Finding:
    """One proven inconsistency about one card."""

    check: str
    card_id: str
    title: str
    body: str
    priority: str = "medium"
    labels: tuple[str, ...] = ()

    @property
    def external_id(self) -> str:
        """Stable identity: one filed card per (check, subject) forever, so a
        finding that survives a hundred scans stays one card."""
        return f"validator:{self.check}:{self.card_id}"


@dataclass
class ScanContext:
    """Board state plus the resolved repository, read once per scan."""

    board: Board
    cards: list[Card]
    edges: list[Edge]
    now: datetime
    _repo_cache: dict[str, Path | None] = field(default_factory=dict)

    @property
    def column_position(self) -> dict[str, int]:
        return {column.id: column.position for column in self.board.columns}

    @property
    def column_name(self) -> dict[str, str]:
        return {column.id: column.name for column in self.board.columns}

    def repo_for(self, card: Card) -> Path | None:
        """The git checkout a card's own metadata points at, or None.

        Only paths the card itself recorded are ever touched, they must already
        exist, and they must actually be a git repository. Nothing is derived
        from card text.
        """
        worktrees = card.metadata.get("worktrees")
        if not isinstance(worktrees, list):
            return None
        for entry in worktrees:
            if not isinstance(entry, str) or not entry.strip():
                continue
            # Recorded worktrees are sometimes annotated: "/path/to/repo (dev)".
            raw = entry.split(" (")[0].strip()
            if raw in self._repo_cache:
                repo = self._repo_cache[raw]
            else:
                repo = _resolve_repo(raw)
                self._repo_cache[raw] = repo
            if repo:
                return repo
        return None


def _resolve_repo(raw: str) -> Path | None:
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir():
        return None
    if _git(path, "rev-parse", "--git-dir") is None:
        return None
    return path


def _git(repo: Path, *args: str) -> str | None:
    """Run one read-only git command. Returns stdout, or None if git failed.

    Arguments are passed as a list and the repository is a validated directory,
    so nothing here is shell-interpreted.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _commits_of(card: Card) -> list[str]:
    raw = card.metadata.get("git_commits")
    if not isinstance(raw, list):
        return []
    return [c.strip() for c in raw if isinstance(c, str) and _SHA_RE.match(c.strip())]


# --- Checks ---
#
# Each takes the scan context and returns findings. Add one here and the worker
# picks it up; there is no second registration site to keep in sync.


def check_commit_missing(ctx: ScanContext) -> list[Finding]:
    """A card cites a commit that does not exist in its own repository."""
    findings = []
    for card in ctx.cards:
        commits = _commits_of(card)
        if not commits:
            continue
        repo = ctx.repo_for(card)
        if not repo:
            continue
        missing = [c for c in commits if _git(repo, "cat-file", "-e", f"{c}^{{commit}}") is None]
        if not missing:
            continue
        findings.append(Finding(
            check="commit_missing",
            card_id=card.id,
            title=f"Card {card.id} cites commits that do not exist: {', '.join(missing)}",
            body=(
                f"Card {card.id} ({card.title!r}) records git_commits "
                f"{', '.join(missing)} in its metadata, but none of them resolve to a "
                f"commit in {repo}.\n\n"
                "A card pinned to a non-existent commit has no traceability: nobody can "
                "verify what it claims was done. Either correct the hashes to the commits "
                "that actually carry the work, or state that the work is unlanded."
            ),
            priority="high",
            labels=("validator", "traceability"),
        ))
    return findings


def check_commit_unreachable(ctx: ScanContext) -> list[Finding]:
    """A card cites a commit that exists but is on no branch — a dangling
    local commit that any gc can take, and that no branch delivers."""
    findings = []
    for card in ctx.cards:
        commits = _commits_of(card)
        if not commits:
            continue
        repo = ctx.repo_for(card)
        if not repo:
            continue
        dangling = []
        for commit in commits:
            if _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is None:
                continue  # missing entirely — the other check owns that
            contains = _git(repo, "branch", "-a", "--contains", commit)
            if contains is not None and not contains.strip():
                dangling.append(commit)
        if not dangling:
            continue
        findings.append(Finding(
            check="commit_unreachable",
            card_id=card.id,
            title=f"Card {card.id} cites commits contained by no branch: {', '.join(dangling)}",
            body=(
                f"Card {card.id} ({card.title!r}) records git_commits "
                f"{', '.join(dangling)}. They exist in {repo} but no local or remote "
                "branch contains them, so they are dangling: unreachable, undelivered, "
                "and collectable by git gc.\n\n"
                "Find the commit that actually carries this work on a branch and correct "
                "the card, or land the dangling work."
            ),
            priority="high",
            labels=("validator", "traceability"),
        ))
    return findings


def check_missing_files(ctx: ScanContext) -> list[Finding]:
    """A card lists touched files that are not in its repository."""
    findings = []
    for card in ctx.cards:
        files = card.metadata.get("files")
        if not isinstance(files, list):
            continue
        repo = ctx.repo_for(card)
        if not repo:
            continue
        missing = [
            f for f in files
            if isinstance(f, str) and f.strip() and not (repo / f.strip()).exists()
        ]
        if not missing:
            continue
        findings.append(Finding(
            check="missing_files",
            card_id=card.id,
            title=f"Card {card.id} lists {len(missing)} file(s) absent from its repository",
            body=(
                f"Card {card.id} ({card.title!r}) lists these files in its metadata, but "
                f"they do not exist under {repo}:\n\n"
                + "\n".join(f"- {f}" for f in missing)
                + "\n\nEither the paths are stale (renamed or deleted since), or they name "
                "files in a different repository than the one the card points at. Both "
                "make the card's file list misleading to the next reader."
            ),
            labels=("validator", "traceability"),
        ))
    return findings


def check_stale_worktree(ctx: ScanContext) -> list[Finding]:
    """A card points at a worktree that no longer exists on disk."""
    findings = []
    for card in ctx.cards:
        worktrees = card.metadata.get("worktrees")
        if not isinstance(worktrees, list):
            continue
        gone = [
            w for w in worktrees
            if isinstance(w, str) and w.strip() and not Path(w.split(" (")[0].strip()).is_dir()
        ]
        if not gone:
            continue
        findings.append(Finding(
            check="stale_worktree",
            card_id=card.id,
            title=f"Card {card.id} points at {len(gone)} worktree(s) that no longer exist",
            body=(
                f"Card {card.id} ({card.title!r}) records these worktrees, none of which "
                "is a directory any more:\n\n"
                + "\n".join(f"- {w}" for w in gone)
                + "\n\nIf the work was committed, record the commit and drop the path. If "
                "it was not, the work is gone and the card is back to unstarted — say so."
            ),
            priority="low",
            labels=("validator", "traceability"),
        ))
    return findings


def check_parent_behind_children(ctx: ScanContext) -> list[Finding]:
    """A parent sits in an earlier column than every one of its children."""
    positions = ctx.column_position
    names = ctx.column_name
    by_parent: dict[str, list[Card]] = {}
    for card in ctx.cards:
        if card.parent_id:
            by_parent.setdefault(card.parent_id, []).append(card)

    findings = []
    for card in ctx.cards:
        children = by_parent.get(card.id) or []
        if not children:
            continue
        parent_pos = positions.get(card.column_id)
        child_positions = [positions.get(c.column_id) for c in children]
        if parent_pos is None or any(p is None for p in child_positions):
            continue
        if parent_pos >= min(child_positions):
            continue
        findings.append(Finding(
            check="parent_behind_children",
            card_id=card.id,
            title=f"Card {card.id} is in an earlier column than every one of its children",
            body=(
                f"Card {card.id} ({card.title!r}) sits in {names.get(card.column_id)!r} "
                f"while all {len(children)} of its children have moved further:\n\n"
                + "\n".join(
                    f"- {c.id} ({names.get(c.column_id)}) {c.title!r}" for c in children
                )
                + "\n\nA parent cannot be less advanced than every child it owns. Either "
                "move the parent forward, or state what parent-level work is genuinely "
                "still outstanding."
            ),
            labels=("validator", "board-hygiene"),
        ))
    return findings


def check_parent_closed_before_children(ctx: ScanContext) -> list[Finding]:
    """A parent is in the terminal column while children are not."""
    if not ctx.board.columns:
        return []
    terminal = max(ctx.board.columns, key=lambda c: c.position)
    names = ctx.column_name
    by_parent: dict[str, list[Card]] = {}
    for card in ctx.cards:
        if card.parent_id:
            by_parent.setdefault(card.parent_id, []).append(card)

    findings = []
    for card in ctx.cards:
        if card.column_id != terminal.id:
            continue
        open_children = [c for c in by_parent.get(card.id, []) if c.column_id != terminal.id]
        if not open_children:
            continue
        findings.append(Finding(
            check="parent_closed_before_children",
            card_id=card.id,
            title=f"Card {card.id} is closed while {len(open_children)} child(ren) are open",
            body=(
                f"Card {card.id} ({card.title!r}) is in {terminal.name!r}, but these "
                "children are not:\n\n"
                + "\n".join(
                    f"- {c.id} ({names.get(c.column_id)}) {c.title!r}" for c in open_children
                )
                + "\n\nClosing a parent asserts its whole scope is done. Either the "
                "children are out of scope and should be reparented, or the parent "
                "closed early."
            ),
            priority="high",
            labels=("validator", "board-hygiene"),
        ))
    return findings


def check_dangling_card_reference(ctx: ScanContext) -> list[Finding]:
    """A card's text cites a card id that does not exist on this board."""
    known = {card.id for card in ctx.cards}
    findings = []
    for card in ctx.cards:
        text = "\n".join([card.body, *(note.text for note in card.notes)])
        cited = {
            token for token in _CARD_ID_RE.findall(text)
            if token not in known and token != card.id
        }
        # Commit hashes and other hex blobs share the shape, so anything the
        # card itself declares as a commit is not a card reference.
        cited -= {c[:12] for c in _commits_of(card)}
        if not cited:
            continue
        findings.append(Finding(
            check="dangling_card_reference",
            card_id=card.id,
            title=f"Card {card.id} references {len(cited)} id(s) that are not cards here",
            body=(
                f"Card {card.id} ({card.title!r}) cites these 12-hex ids in its body or "
                "notes, and none of them is a card on this board:\n\n"
                + "\n".join(f"- {c}" for c in sorted(cited))
                + "\n\nThey may be cards deleted since, cards on another board, or ids "
                "that were never cards. A reference the reader cannot follow is worse "
                "than none: replace each with a real card id or with words."
            ),
            priority="low",
            labels=("validator", "board-hygiene"),
        ))
    return findings


def check_malformed_labels(ctx: ScanContext) -> list[Finding]:
    """A single label holding a delimited list, which no label filter matches."""
    findings = []
    for card in ctx.cards:
        broken = [
            label for label in card.labels
            if isinstance(label, str) and ("," in label or len(label.split()) > 3)
        ]
        if not broken:
            continue
        findings.append(Finding(
            check="malformed_labels",
            card_id=card.id,
            title=f"Card {card.id} has {len(broken)} label(s) stored as a delimited list",
            body=(
                f"Card {card.id} ({card.title!r}) stores these as single labels:\n\n"
                + "\n".join(f"- {label!r}" for label in broken)
                + "\n\nA label holding a comma-separated list matches no label filter, so "
                "the card is invisible to every one of the labels it meant to carry. "
                "Split them into separate labels."
            ),
            labels=("validator", "board-hygiene"),
        ))
    return findings


def check_stale_open_question(ctx: ScanContext) -> list[Finding]:
    """A question nobody has answered for a long time."""
    cutoff = ctx.now - STALE_QUESTION_AGE
    findings = []
    for card in ctx.cards:
        stale = [
            note for note in card.notes
            if is_open_question(note) and note.created_at < cutoff
        ]
        if not stale:
            continue
        days = STALE_QUESTION_AGE.days
        findings.append(Finding(
            check="stale_open_question",
            card_id=card.id,
            title=f"Card {card.id} has {len(stale)} question(s) unanswered for over {days} days",
            body=(
                f"Card {card.id} ({card.title!r}) is waiting on answers that have not "
                f"come in {days}+ days:\n\n"
                + "\n".join(f"- {note.text[:300]}" for note in stale)
                + "\n\nAn unanswered question that old is usually either obsolete or "
                "genuinely blocking. Re-ask it with current context, answer it from what "
                "is now known, or withdraw it."
            ),
            labels=("validator", "board-hygiene"),
        ))
    return findings


def check_semantic_hierarchy(ctx: ScanContext) -> list[Finding]:
    cards = {card.id: card for card in ctx.cards}
    findings = []
    for card in ctx.cards:
        if not card.semantics or not card.semantics.kind:
            continue
        parent = cards.get(card.parent_id) if card.parent_id else None
        parent_semantics = parent.semantics if parent else None
        if valid_semantic_parent(card.semantics, parent_semantics):
            continue
        findings.append(Finding(
            check="semantic_hierarchy",
            card_id=card.id,
            title=f"Card {card.id} has an invalid semantic parent",
            body=f"{card.title!r} does not follow the Product Area → Feature → Requirement hierarchy.",
            labels=("validator", "board-hygiene", "semantics"),
        ))
    return findings


def check_relationship_integrity(ctx: ScanContext) -> list[Finding]:
    cards = {card.id: card for card in ctx.cards}
    allowed = set(get_args(RelationshipType))
    findings = []
    seen: set[tuple[str, str, str]] = set()
    dependency_edges: list[Edge] = []
    for edge in ctx.edges:
        key = (edge.from_card_id, edge.to_card_id, edge.type)
        source = cards.get(edge.from_card_id)
        target = cards.get(edge.to_card_id)
        reasons = []
        if edge.type not in allowed:
            reasons.append(f"legacy relationship type {edge.type!r}")
        if not source or not target:
            reasons.append("missing endpoint")
        elif source.id == target.id:
            reasons.append("self relationship")
        elif edge.type in allowed and not edge_store.direction_is_valid(edge.type, source, target):
            reasons.append("invalid direction for card kinds")
        elif edge.type in {"defines", "parent_of"} and target.parent_id != source.id:
            reasons.append("relationship contradicts parent hierarchy")
        if key in seen:
            reasons.append("duplicate relationship")
        seen.add(key)
        if edge.type == "depends_on" and source and target:
            dependency_edges.append(edge)
        if not reasons:
            continue
        findings.append(Finding(
            check="relationship_integrity",
            card_id=edge.from_card_id,
            title=f"Relationship {edge.id} is invalid",
            body="; ".join(reasons),
            labels=("validator", "board-hygiene", "relationships"),
        ))

    adjacency: dict[str, set[str]] = {}
    for edge in dependency_edges:
        adjacency.setdefault(edge.from_card_id, set()).add(edge.to_card_id)
    for edge in dependency_edges:
        pending = [edge.to_card_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == edge.from_card_id:
                findings.append(Finding(
                    check="relationship_integrity",
                    card_id=edge.from_card_id,
                    title=f"Dependency relationship {edge.id} participates in a cycle",
                    body="depends_on relationships must remain acyclic.",
                    labels=("validator", "board-hygiene", "relationships"),
                ))
                break
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
    return findings


CHECKS = (
    check_commit_missing,
    check_commit_unreachable,
    check_missing_files,
    check_stale_worktree,
    check_parent_behind_children,
    check_parent_closed_before_children,
    check_dangling_card_reference,
    check_malformed_labels,
    check_stale_open_question,
    check_semantic_hierarchy,
    check_relationship_integrity,
)


def scan_board(
    board: Board,
    cards: list[Card],
    now: datetime | None = None,
    edges: list[Edge] | None = None,
) -> list[Finding]:
    """Every finding for one board. Validator-filed cards are never themselves
    scanned, so the worker cannot chase its own tail."""
    subjects = [c for c in cards if not c.external_id.startswith("validator:")]
    subject_ids = {card.id for card in subjects}
    ctx = ScanContext(
        board=board,
        cards=subjects,
        edges=[
            edge for edge in (edges or [])
            if edge.from_card_id in subject_ids and edge.to_card_id in subject_ids
        ],
        now=now or datetime.now(timezone.utc),
    )
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(ctx))
    return findings
