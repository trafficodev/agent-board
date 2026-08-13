"""Background validation worker.

Runs the deterministic checks over every board on an interval and keeps the
board's own cards as the record of what they found. A finding becomes a card an
agent can pick up; a finding that stops reproducing closes its card again. The
worker only ever writes cards it owns — identified by a `validator:` external
id — so it can never touch work a human or agent filed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

from . import board_store as bs
from . import card_store as cs
from . import card_validation
from . import edge_store
from .models import AddNote, CreateCard, MoveCard

VALIDATOR_PREFIX = "validator:"
VALIDATOR_LABEL = "validator"
DEFAULT_INTERVAL_SECONDS = 900
MIN_INTERVAL_SECONDS = 30
RESOLVED_NOTE = (
    "Resolved by the validation worker: this check no longer reproduces against "
    "current board and repository state."
)


def is_enabled() -> bool:
    return (os.environ.get("AGENT_BOARD_VALIDATION_ENABLED") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def interval_seconds() -> int:
    raw = (os.environ.get("AGENT_BOARD_VALIDATION_INTERVAL_SECONDS") or "").strip()
    try:
        return max(MIN_INTERVAL_SECONDS, int(raw)) if raw else DEFAULT_INTERVAL_SECONDS
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS


def _intake_column_id(board) -> str | None:
    """Where new findings land: the board's first column."""
    if not board.columns:
        return None
    return min(board.columns, key=lambda c: c.position).id


def _terminal_column_id(board) -> str | None:
    if not board.columns:
        return None
    return max(board.columns, key=lambda c: c.position).id


def scan_and_apply(board_id: str, now: datetime | None = None) -> dict:
    """One full pass over one board: file what reproduces, close what does not.

    Returns a summary rather than logging it, so callers (the loop, a test, an
    operator hitting the endpoint) all see the same result shape.
    """
    board = bs.get_board(board_id)
    if not board:
        return {"board_id": board_id, "error": "board not found"}
    intake = _intake_column_id(board)
    terminal = _terminal_column_id(board)
    if not intake or not terminal:
        return {"board_id": board_id, "error": "board has no columns"}

    cards = cs.list_cards(board_id)
    findings = card_validation.scan_board(
        board,
        cards,
        now=now,
        edges=edge_store.list_edges(board_id),
    )
    existing = {
        card.external_id: card
        for card in cards
        if card.external_id.startswith(VALIDATOR_PREFIX)
    }

    filed, updated = 0, 0
    for finding in findings:
        prior = existing.get(finding.external_id)
        # An existing card keeps whatever column it has been moved to: the
        # worker owns the finding's content, a human owns its progress.
        column_id = prior.column_id if prior else intake
        if prior is not None and prior.column_id == terminal:
            # It reproduces again after being closed. Reopen at intake.
            column_id = intake
        card = cs.upsert_card(board_id, CreateCard(
            external_id=finding.external_id,
            title=finding.title,
            body=finding.body,
            column_id=column_id,
            priority=finding.priority,
            labels=list(finding.labels) or [VALIDATOR_LABEL],
            metadata={
                "validator_check": finding.check,
                "validator_subject_card_id": finding.card_id,
            },
        ))
        if card is None:
            continue
        if prior is None:
            filed += 1
        elif prior.updated_at != card.updated_at:
            updated += 1

    reproducing = {finding.external_id for finding in findings}
    resolved = 0
    for external_id, card in existing.items():
        if external_id in reproducing or card.column_id == terminal:
            continue
        cs.add_note(board_id, card.id, AddNote(kind="note", text=RESOLVED_NOTE))
        cs.move_card(board_id, card.id, MoveCard(column_id=terminal))
        resolved += 1

    return {
        "board_id": board_id,
        "findings": len(findings),
        "filed": filed,
        "updated": updated,
        "resolved": resolved,
    }


def scan_all_boards(now: datetime | None = None) -> list[dict]:
    results = []
    for board in bs.list_boards():
        try:
            results.append(scan_and_apply(board.id, now=now))
        except Exception as exc:  # one bad board must not stop the rest
            results.append({"board_id": board.id, "error": f"{type(exc).__name__}: {exc}"})
    return results


async def run_forever() -> None:
    """Scan on an interval until cancelled. Never raises into the caller: a
    failed pass is reported and the next one still happens."""
    while True:
        try:
            await asyncio.to_thread(scan_all_boards, datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sys.stderr.write(f"Card validation pass failed: {type(exc).__name__}: {exc}\n")
        await asyncio.sleep(interval_seconds())
