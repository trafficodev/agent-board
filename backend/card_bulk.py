"""Apply many card operations in one board transaction.

An agent planning work usually knows the whole shape of the change up front --
open five cards, close three, re-lane the rest -- but the single-card API makes
it pay a round-trip per card and leaves the board briefly in a half-applied
state. A batch is ordered, atomic, and lets later operations target cards the
earlier ones created.
"""

from models import (
    AddNote,
    BulkCards,
    BulkCardsResult,
    BulkCreate,
    BulkDelete,
    BulkMove,
    BulkNote,
    BulkOperationResult,
    BulkUpdate,
    CreateCard,
    MoveCard,
    UpdateCard,
)

import card_store

REF_PREFIX = "@"


class _Aborted(Exception):
    """Raised to unwind the transaction without writing anything."""

    def __init__(self, index: int, error: str):
        super().__init__(error)
        self.index = index
        self.error = error


def _resolve(refs: dict[str, str], value: str | None, index: int, field: str) -> str | None:
    """Turn an "@ref" into the id of the card that op created."""
    if not isinstance(value, str) or not value.startswith(REF_PREFIX):
        return value
    name = value[len(REF_PREFIX):]
    if name not in refs:
        raise _Aborted(index, f"{field} refers to unknown ref {value!r}")
    return refs[name]


def _apply_one(tx, board_id: str, op, index: int, refs: dict[str, str]) -> BulkOperationResult:
    if isinstance(op, BulkCreate):
        if op.ref and op.ref in refs:
            raise _Aborted(index, f"duplicate ref {op.ref!r}")
        fields = op.model_dump(exclude={"op", "ref"})
        fields["parent_id"] = _resolve(refs, fields["parent_id"], index, "parent_id")
        card = card_store.apply_create(tx, board_id, CreateCard(**fields))
        if not card:
            raise _Aborted(index, "create failed: unknown board or column")
        if op.ref:
            refs[op.ref] = card.id
        return BulkOperationResult(index=index, op="create", card_id=card.id, ref=op.ref)

    card_id = _resolve(refs, op.card_id, index, "card_id")

    if isinstance(op, BulkUpdate):
        # exclude_unset keeps "explicitly set to null" distinct from "omitted",
        # which is how update_card tells a re-parent from a no-op.
        fields = op.model_dump(exclude={"op", "card_id"}, exclude_unset=True)
        for field in ("parent_id", "column_id"):
            if field in fields:
                fields[field] = _resolve(refs, fields[field], index, field)
        if not card_store.apply_update(tx, board_id, card_id, UpdateCard(**fields)):
            raise _Aborted(
                index,
                f"update failed: no card {card_id}, unknown column, or parent would make a cycle",
            )
        return BulkOperationResult(index=index, op="update", card_id=card_id)

    if isinstance(op, BulkMove):
        column_id = _resolve(refs, op.column_id, index, "column_id")
        moved = card_store.apply_move(
            tx, board_id, card_id, MoveCard(column_id=column_id, position=op.position)
        )
        if not moved:
            raise _Aborted(index, f"move failed: no card {card_id} or unknown column")
        return BulkOperationResult(index=index, op="move", card_id=card_id)

    if isinstance(op, BulkDelete):
        if not card_store.apply_delete(tx, board_id, card_id):
            raise _Aborted(index, f"delete failed: no card {card_id}")
        return BulkOperationResult(index=index, op="delete", card_id=card_id)

    if isinstance(op, BulkNote):
        noted = card_store.apply_note(
            tx, board_id, card_id, AddNote(kind=op.kind, text=op.text)
        )
        if not noted:
            raise _Aborted(index, f"note failed: no card {card_id} or empty text")
        return BulkOperationResult(index=index, op="note", card_id=card_id)

    raise _Aborted(index, f"unsupported operation {type(op).__name__}")


def bulk_cards(board_id: str, data: BulkCards) -> BulkCardsResult:
    """Apply every operation, or none of them.

    A partially applied batch is worse than a rejected one: the agent cannot
    tell which half landed, and the board is left in a state nobody asked for.
    So the first failure unwinds the transaction, and the index that failed is
    reported so the caller can fix that one operation and resend.
    """
    results: list[BulkOperationResult] = []
    refs: dict[str, str] = {}
    try:
        with card_store.board_transaction(board_id) as tx:
            for index, op in enumerate(data.operations):
                results.append(_apply_one(tx, board_id, op, index, refs))
    except _Aborted as aborted:
        return BulkCardsResult(applied=False, failed_index=aborted.index, error=aborted.error)
    return BulkCardsResult(applied=True, results=results)
