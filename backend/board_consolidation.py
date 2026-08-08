import fcntl
import json
import os
import re
import uuid
from pathlib import Path

import board_store as bs
import canvas_sync
import card_history
import card_store as cs
import edge_store as es
import db
from card_diff import CHANGELOG_PROJECTION_VERSION, canonical_document, snapshot_of
from models import Board, Card, Column, Edge, Event, _now
from paths import home

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_MANIFESTS_DIR = "consolidations"
_API_LOCK = "api-maintenance.lock"


class ConsolidationConflictError(ValueError):
    pass


def api_lock_path() -> Path:
    path = home() / "locks" / _API_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def pending_manifest_paths() -> list[Path]:
    directory = home() / _MANIFESTS_DIR
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


def has_pending_consolidation() -> bool:
    return bool(pending_manifest_paths())


def _manifest_path(source_board_id: str, target_board_id: str) -> Path:
    directory = home() / _MANIFESTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{source_board_id}-{target_board_id}.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _atomic_write_json(path: Path, payload) -> None:
    _atomic_write(path, json.dumps(payload, indent=2, default=str))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalized_remotes(board: Board) -> set[str]:
    return {
        normalized
        for remote in [board.remote_url, *board.remote_aliases]
        if (normalized := bs.normalize_remote_url(remote))
    }


def _acquire_remote_locks(remotes: set[str]) -> list:
    return [bs._acquire_remote_lock(remote) for remote in sorted(remotes)]


def _release_locks(locks: list, release) -> None:
    for lock in reversed(locks):
        release(lock)


def _column_map(source: Board, target: Board) -> tuple[dict[str, str], Board]:
    target = target.model_copy(deep=True)
    target_by_name: dict[str, Column] = {}
    for column in target.columns:
        key = column.name.strip().casefold()
        if key in target_by_name:
            raise ConsolidationConflictError(f"target board has duplicate column name: {column.name}")
        target_by_name[key] = column

    mapping: dict[str, str] = {}
    used_ids = {column.id for column in target.columns}
    for source_column in sorted(source.columns, key=lambda column: column.position):
        key = source_column.name.strip().casefold()
        target_column = target_by_name.get(key)
        if target_column is None:
            target_column = source_column.model_copy(deep=True)
            if target_column.id in used_ids:
                target_column.id = Column(name=source_column.name).id
            target_column.board_id = target.id
            target_column.position = len(target.columns)
            target.columns.append(target_column)
            target_by_name[key] = target_column
            used_ids.add(target_column.id)
        mapping[source_column.id] = target_column.id
    return mapping, target


def _map_column_value(value, column_ids: dict[str, str]):
    if isinstance(value, str):
        return column_ids.get(value, value)
    return value


def _transform_cards(
    source_cards: list[Card],
    target_cards: list[Card],
    target_board_id: str,
    column_ids: dict[str, str],
) -> list[Card]:
    target_by_id = {card.id: card for card in target_cards}
    source_ids = {card.id for card in source_cards}

    next_position: dict[str, int] = {}
    for card in target_cards:
        next_position[card.column_id] = max(next_position.get(card.column_id, 0), card.position + 1)

    transformed: list[Card] = []
    for source_card in sorted(source_cards, key=lambda card: (card.column_id, card.position)):
        card = source_card.model_copy(deep=True)
        card.board_id = target_board_id
        card.column_id = column_ids[card.column_id]
        for entry in card.session_history:
            for change in entry.changes:
                if change.field == "column_id":
                    change.before = _map_column_value(change.before, column_ids)
                    change.after = _map_column_value(change.after, column_ids)
                if change.field == "board_id":
                    if change.before == source_card.board_id:
                        change.before = target_board_id
                    if change.after == source_card.board_id:
                        change.after = target_board_id
        existing = target_by_id.get(card.id)
        if existing:
            if existing.model_dump(mode="json") != card.model_dump(mode="json"):
                raise ConsolidationConflictError(f"card id collision: {card.id}")
            continue
        card.position = next_position.get(card.column_id, 0)
        next_position[card.column_id] = card.position + 1
        transformed.append(card)

    combined_ids = set(target_by_id) | source_ids
    dangling_parent = next(
        (
            card.parent_id
            for card in [*target_cards, *transformed]
            if card.parent_id and card.parent_id not in combined_ids
        ),
        None,
    )
    if dangling_parent:
        raise ConsolidationConflictError(f"dangling parent card: {dangling_parent}")
    return [*target_cards, *transformed]


def _transform_edges(
    source_edges: list[Edge],
    target_edges: list[Edge],
    target_board_id: str,
    card_ids: set[str],
) -> list[Edge]:
    target_by_id = {edge.id: edge for edge in target_edges}

    transformed = []
    for source_edge in source_edges:
        edge = source_edge.model_copy(deep=True)
        edge.board_id = target_board_id
        if edge.from_card_id not in card_ids or edge.to_card_id not in card_ids:
            raise ConsolidationConflictError(f"dangling edge: {edge.id}")
        existing = target_by_id.get(edge.id)
        if existing:
            if existing.model_dump(mode="json") != edge.model_dump(mode="json"):
                raise ConsolidationConflictError(f"edge id collision: {edge.id}")
            continue
        transformed.append(edge)
    return [*target_edges, *transformed]


def _transform_snapshot(snapshot: dict | None, source_board_id: str, target_board_id: str, column_ids: dict[str, str]):
    if snapshot is None:
        return None
    transformed = dict(snapshot)
    transformed["column_id"] = _map_column_value(transformed.get("column_id"), column_ids)
    if transformed.get("board_id") == source_board_id:
        transformed["board_id"] = target_board_id
    return transformed


def _transform_events(
    source_events: list[Event],
    target_events: list[Event],
    source_board_id: str,
    target_board_id: str,
    column_ids: dict[str, str],
) -> list[Event]:
    target_by_id = {event.id: event for event in target_events}

    transformed = []
    for source_event in source_events:
        event = source_event.model_copy(deep=True)
        event.board_id = target_board_id
        event.snapshot = _transform_snapshot(
            event.snapshot,
            source_board_id,
            target_board_id,
            column_ids,
        )
        existing = target_by_id.get(event.id)
        if existing:
            if existing.model_dump(mode="json") != event.model_dump(mode="json"):
                raise ConsolidationConflictError(f"event id collision: {event.id}")
            continue
        transformed.append(event)
    transformed.append(Event(
        type="board_consolidated",
        detail=f"{source_board_id} → {target_board_id}",
        board_id=target_board_id,
        action="consolidated",
    ))
    return [*target_events, *transformed]


def _build_manifest(source: Board, target: Board) -> dict:
    syncs = canvas_sync._read_syncs()
    if any((syncs.get(board_id) or {}).get("enabled") for board_id in (source.id, target.id)):
        raise ConsolidationConflictError("board canvas sync must be disabled")

    column_ids, target_with_columns = _column_map(source, target)
    source_cards = cs._read_cards(source.id)
    target_cards = cs._read_cards(target.id)
    cards = _transform_cards(source_cards, target_cards, target.id, column_ids)
    edges = _transform_edges(
        es.read_edges(source.id),
        es.read_edges(target.id),
        target.id,
        {card.id for card in cards},
    )
    events = _transform_events(
        card_history.read_events(source.id),
        card_history.read_events(target.id),
        source.id,
        target.id,
        column_ids,
    )
    source_card_ids = {card.id for card in source_cards}
    events.extend(
        Event(
            type="card_consolidated",
            detail=card.title,
            board_id=target.id,
            card_id=card.id,
            system="system",
            action="consolidated",
            snapshot=snapshot_of(card),
            projection_version=CHANGELOG_PROJECTION_VERSION,
            document=canonical_document(card),
            provider="system",
            voteable=False,
            cutover_state="authored",
        )
        for card in cards
        if card.id in source_card_ids
    )

    source_remotes = _normalized_remotes(source)
    target_remotes = _normalized_remotes(target)
    merged_remotes = source_remotes | target_remotes
    for board in bs.list_boards():
        if board.id in {source.id, target.id}:
            continue
        overlap = merged_remotes & _normalized_remotes(board)
        if overlap:
            raise ConsolidationConflictError(
                f"remote is owned by another board: {sorted(overlap)[0]}"
            )
    aliases = sorted(merged_remotes - {bs.normalize_remote_url(target.remote_url)})
    final_target = target_with_columns.model_copy(deep=True)
    final_target.remote_aliases = aliases
    final_target.updated_at = _now()
    pre_alias_target = final_target.model_copy(deep=True)
    pre_alias_target.remote_aliases = list(target.remote_aliases)

    Board.model_validate(final_target.model_dump())
    for card in cards:
        Card.model_validate(card.model_dump())
    for edge in edges:
        Edge.model_validate(edge.model_dump())
    for event in events:
        Event.model_validate(event.model_dump())

    return {
        "version": 1,
        "source_board_id": source.id,
        "target_board_id": target.id,
        "remote_locks": sorted(merged_remotes),
        "pre_alias_target": pre_alias_target.model_dump(mode="json"),
        "final_target": final_target.model_dump(mode="json"),
        "cards": [card.model_dump(mode="json") for card in cards],
        "edges": [edge.model_dump(mode="json") for edge in edges],
        "events": [event.model_dump(mode="json") for event in events],
    }


def _apply_manifest(path: Path, manifest: dict) -> None:
    """Land a prepared consolidation.

    The manifest stays a durable write-ahead record on disk: it is what lets a
    crash between preparing and applying be finished on the next startup rather
    than leaving two half-merged boards. The apply itself is now one database
    transaction, so the intermediate states the old file-by-file rewrite could
    be interrupted in no longer exist.
    """
    source_id = manifest["source_board_id"]
    target_id = manifest["target_board_id"]

    syncs = canvas_sync._read_syncs()
    if any((syncs.get(board_id) or {}).get("enabled") for board_id in (source_id, target_id)):
        raise ConsolidationConflictError("board canvas sync must be disabled")

    with db.transaction() as conn:
        bs._save_board(conn, Board.model_validate(manifest["pre_alias_target"]))

        # The target's cards, edges and journal are replaced wholesale by the
        # merged set the manifest carries.
        conn.execute("DELETE FROM cards WHERE board_id=?", (target_id,))
        conn.execute("DELETE FROM edges WHERE board_id=?", (target_id,))
        conn.execute("DELETE FROM events WHERE board_id=?", (target_id,))
        # Source events retain their immutable IDs after moving to the target.
        # Release those globally unique IDs inside this transaction before the
        # merged journal is inserted.
        conn.execute("DELETE FROM events WHERE board_id=?", (source_id,))
        for entry in manifest["cards"]:
            db.write_card(conn, Card.model_validate(entry))
        for entry in manifest["edges"]:
            db.write_edge(conn, Edge.model_validate(entry))
        for entry in manifest["events"]:
            db.write_event(conn, Event.model_validate(entry))

        # The source board goes; its cards and edges follow by foreign key.
        conn.execute("DELETE FROM boards WHERE id=?", (source_id,))

        # Aliases are added last, so a crash before this point leaves the
        # target not yet answering for the source's remotes and the manifest
        # still pending -- recovery re-runs the whole apply.
        bs._save_board(conn, Board.model_validate(manifest["final_target"]))

    syncs.pop(source_id, None)
    canvas_sync._write_syncs(syncs)

    path.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _apply_with_locks(path: Path, manifest: dict) -> Board:
    remote_locks = _acquire_remote_locks(set(manifest["remote_locks"]))
    canvas_lock = canvas_sync._acquire_sync_lock()
    try:
        _apply_manifest(path, manifest)
        board = bs.get_board(manifest["target_board_id"])
        if board is None:
            raise RuntimeError("consolidation target missing after apply")
        return board
    finally:
        canvas_sync._release_sync_lock(canvas_lock)
        _release_locks(remote_locks, bs._release_remote_lock)


def recover_pending_consolidations() -> None:
    for path in pending_manifest_paths():
        manifest = json.loads(path.read_text())
        _apply_with_locks(path, manifest)


def consolidate_project_board(source_board_id: str, target_board_id: str) -> Board:
    if not _ID_RE.fullmatch(source_board_id) or not _ID_RE.fullmatch(target_board_id):
        raise ValueError("board ids must be 12 lowercase hexadecimal characters")
    if source_board_id == target_board_id:
        raise ValueError("source and target boards must differ")

    existing_manifest = _manifest_path(source_board_id, target_board_id)
    other_manifests = [
        path for path in pending_manifest_paths()
        if path != existing_manifest
    ]
    if other_manifests:
        raise ConsolidationConflictError("another board consolidation requires recovery")
    if existing_manifest.exists():
        return _apply_with_locks(existing_manifest, json.loads(existing_manifest.read_text()))

    expected_remotes: set[str] = set()
    while True:
        source = bs.get_board(source_board_id)
        target = bs.get_board(target_board_id)
        if source is None or target is None:
            raise KeyError("source or target board not found")
        expected_remotes |= _normalized_remotes(source) | _normalized_remotes(target)
        remote_locks = _acquire_remote_locks(expected_remotes)
        source = bs.get_board(source_board_id)
        target = bs.get_board(target_board_id)
        if source is None or target is None:
            _release_locks(remote_locks, bs._release_remote_lock)
            raise KeyError("source or target board not found")
        current_remotes = _normalized_remotes(source) | _normalized_remotes(target)
        if current_remotes <= expected_remotes:
            break
        expected_remotes |= current_remotes
        _release_locks(remote_locks, bs._release_remote_lock)

    canvas_lock = canvas_sync._acquire_sync_lock()
    try:
        # Build and apply share one write transaction. They cannot be split:
        # a card added to the source in between would be absent from the
        # manifest and would then go with the source board, silently losing
        # work. BEGIN IMMEDIATE holds every other writer off for the span.
        with db.transaction():
            manifest = _build_manifest(source, target)
            _atomic_write_json(existing_manifest, manifest)
            try:
                _apply_manifest(existing_manifest, manifest)
            except Exception:
                _apply_manifest(existing_manifest, manifest)
        result = bs.get_board(target_board_id)
        if result is None:
            raise RuntimeError("consolidation target missing after apply")
        return result
    finally:
        canvas_sync._release_sync_lock(canvas_lock)
        _release_locks(remote_locks, bs._release_remote_lock)
