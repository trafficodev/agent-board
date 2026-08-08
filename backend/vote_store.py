"""Append-only votes for deterministic card change items."""

from collections.abc import Iterable

import db
from models import CardChangeItem, ChangeContext, ChangeVoteEvent, ChangeVoteSummary


def set_vote(
    board_id: str,
    card_id: str,
    target_id: str,
    direction: int,
    context: ChangeContext,
) -> ChangeVoteEvent:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not context.voteable:
        raise ValueError("vote identity and reviewed commit are required")

    with db.transaction() as conn:
        from card_history import card_change_items

        target = next(
            (
                item
                for item in card_change_items(
                    board_id,
                    card_id,
                    include_votes=False,
                )
                if item.id == target_id
            ),
            None,
        )
        if target is None:
            raise ValueError("change item does not exist or is not voteable")

        vote = ChangeVoteEvent(
            target_id=target.id,
            event_id=target.event_id,
            provider=context.provider,
            native_session_id=context.native_session_id,
            reviewed_commit_sha=context.reviewed_commit_sha,
            direction=direction,
        )
        db.write_change_vote(conn, vote)
    return vote


def vote_audit(
    target_id: str,
    offset: int = 0,
    limit: int = 200,
) -> list[ChangeVoteEvent]:
    if offset < 0 or limit <= 0:
        return []
    rows = db.connect().execute(
        "SELECT * FROM change_votes WHERE target_id=? ORDER BY seq DESC LIMIT ? OFFSET ?",
        (target_id, limit, offset),
    ).fetchall()
    return [db.change_vote_from_row(row) for row in rows]


def vote_summaries(
    target_ids: Iterable[str],
    context: ChangeContext | None = None,
) -> dict[str, ChangeVoteSummary]:
    ids = list(dict.fromkeys(target_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = db.connect().execute(
        f"""WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY target_id, provider, native_session_id
                    ORDER BY seq DESC
                ) AS voter_rank
                FROM change_votes
                WHERE target_id IN ({placeholders})
            )
            SELECT * FROM ranked WHERE voter_rank=1""",
        ids,
    ).fetchall()
    summaries = {target_id: ChangeVoteSummary() for target_id in ids}
    for row in rows:
        vote = db.change_vote_from_row(row)
        summary = summaries[vote.target_id]
        if vote.direction == 1:
            summary.up += 1
        else:
            summary.down += 1
        if (
            context is not None
            and vote.provider == context.provider
            and vote.native_session_id == context.native_session_id
        ):
            summary.current = vote.direction
    return summaries


def attach_vote_summaries(
    items: list[CardChangeItem],
    context: ChangeContext | None = None,
) -> list[CardChangeItem]:
    summaries = vote_summaries((item.id for item in items), context)
    return [
        item.model_copy(update={"votes": summaries[item.id]})
        for item in items
    ]
