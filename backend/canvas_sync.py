from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import board_store as bs
import card_store as cs
from paths import ensure_lock_path, home


DEFAULT_CANVAS_API_URL = "http://localhost:8002/api"
SYNC_FILE = "canvas_sync.json"


def sync_path() -> Path:
    return home() / SYNC_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_syncs() -> dict[str, dict[str, Any]]:
    path = sync_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_syncs(syncs: dict[str, dict[str, Any]]) -> None:
    path = sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "w") as stream:
        stream.write(json.dumps(syncs, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _acquire_sync_lock():
    lock_file = open(ensure_lock_path("canvas-sync"), "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def _release_sync_lock(lock_file) -> None:
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


def _configured_canvas_api_url() -> str:
    return os.environ.get("AGENT_CANVAS_API_URL", DEFAULT_CANVAS_API_URL)


def _validate_canvas_api_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise ValueError("canvas_api_url must use http")
    if parsed.username or parsed.password:
        raise ValueError("canvas_api_url must not include credentials")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("canvas_api_url must point to localhost")
    return value.rstrip("/")


def get_sync_status(board_id: str) -> dict[str, Any]:
    lock = _acquire_sync_lock()
    try:
        sync = _read_syncs().get(board_id, {})
    finally:
        _release_sync_lock(lock)
    return _status_from_sync(sync)


def enable_sync(board_id: str, canvas_api_url: str | None = None) -> dict[str, Any]:
    api_url = _validate_canvas_api_url(canvas_api_url or _configured_canvas_api_url())
    board_lock = bs._with_lock(board_id)
    sync_lock = _acquire_sync_lock()
    try:
        syncs = _read_syncs()
        current = syncs.get(board_id, {})
        syncs[board_id] = {
            **current,
            "enabled": True,
            "canvas_api_url": api_url,
            "last_error": None,
        }
        _write_syncs(syncs)
    finally:
        _release_sync_lock(sync_lock)
        bs._release_lock(board_lock)
    return sync_board(board_id)


def disable_sync(board_id: str) -> dict[str, Any]:
    board_lock = bs._with_lock(board_id)
    sync_lock = _acquire_sync_lock()
    try:
        syncs = _read_syncs()
        current = syncs.get(board_id, {})
        syncs[board_id] = {**current, "enabled": False}
        _write_syncs(syncs)
    finally:
        _release_sync_lock(sync_lock)
        bs._release_lock(board_lock)
    return get_sync_status(board_id)


def sync_if_enabled(board_id: str) -> dict[str, Any] | None:
    if not get_sync_status(board_id)["enabled"]:
        return None
    return sync_board(board_id)


def sync_board(board_id: str) -> dict[str, Any]:
    board_lock = bs._with_lock(board_id)
    sync_lock = _acquire_sync_lock()
    try:
        syncs = _read_syncs()
        current = syncs.get(board_id, {})
        if not current.get("enabled"):
            return _status_from_sync(current)

        api_url = _validate_canvas_api_url(current.get("canvas_api_url") or _configured_canvas_api_url())
        payload = _build_graph_payload(bs.get_board(board_id), cs._read_cards(board_id))
        canvas_board_id = current.get("canvas_board_id")
        try:
            if canvas_board_id:
                response = _request_json("PUT", f"{api_url}/import/graph/{canvas_board_id}", payload)
            else:
                response = _request_json("POST", f"{api_url}/import/graph", payload)
                canvas_board_id = response.get("id")
            syncs[board_id] = {
                **current,
                "enabled": True,
                "canvas_api_url": api_url,
                "canvas_board_id": canvas_board_id,
                "last_synced_at": _now(),
                "last_error": None,
            }
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            syncs[board_id] = {
                **current,
                "enabled": True,
                "canvas_api_url": api_url,
                "last_error": str(exc),
            }
        _write_syncs(syncs)
    finally:
        _release_sync_lock(sync_lock)
        bs._release_lock(board_lock)
    return get_sync_status(board_id)


def build_graph_payload(board_id: str) -> dict[str, Any]:
    board_lock = bs._with_lock(board_id)
    try:
        return _build_graph_payload(bs.get_board(board_id), cs._read_cards(board_id))
    finally:
        bs._release_lock(board_lock)


def _build_graph_payload(board, cards) -> dict[str, Any]:
    if not board:
        raise ValueError("Board not found")

    columns = sorted(board.columns, key=lambda col: col.position)
    column_index = {col.id: index for index, col in enumerate(columns)}
    column_names = {col.id: col.name for col in columns}
    column_positions: dict[str, int] = {}
    nodes = []
    edges = []
    for card in cards:
        position = column_positions.get(card.column_id, 0)
        column_positions[card.column_id] = position + 1
        labels = ", ".join(card.labels)
        body_parts = [
            f"Column: {column_names.get(card.column_id, 'Unknown')}",
            f"Priority: {card.priority}",
        ]
        if labels:
            body_parts.append(f"Labels: {labels}")
        if card.body:
            body_parts.append(card.body)
        nodes.append({
            "external_id": card.id,
            "label": card.title,
            "body": "\n".join(body_parts),
            "kind": "card",
            "x": 80 + column_index.get(card.column_id, 0) * 340,
            "y": 80 + position * 210,
            "width": 280,
            "height": 160,
            "color": _priority_color(card.priority),
        })
        if card.parent_id:
            edges.append({"from_id": card.parent_id, "to_id": card.id, "label": "subtask"})

    return {
        "name": board.name,
        "description": board.description,
        "nodes": nodes,
        "edges": edges,
    }


def _status_from_sync(sync: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(sync.get("enabled")),
        "canvas_board_id": sync.get("canvas_board_id"),
        "canvas_api_url": sync.get("canvas_api_url") or _configured_canvas_api_url(),
        "last_synced_at": sync.get("last_synced_at"),
        "last_error": sync.get("last_error"),
    }


def _priority_color(priority: str) -> str:
    colors = {
        "critical": "#FFD6D6",
        "high": "#FFE4C2",
        "medium": "#DDEBFF",
        "low": "#DFF3E3",
    }
    return colors.get(priority, "#DDEBFF")


def _request_json(method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
