import os
from pathlib import Path


def home() -> Path:
    """Agent Board data directory. Honors AGENT_BOARD_HOME env var."""
    p = os.environ.get("AGENT_BOARD_HOME")
    if p:
        return Path(p)
    return Path.home() / ".agent-board"


def boards_dir() -> Path:
    d = home() / "boards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def board_path(board_id: str) -> Path:
    return boards_dir() / f"{board_id}.json"


def events_path(board_id: str) -> Path:
    d = home() / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{board_id}.jsonl"


def edges_path(board_id: str) -> Path:
    return boards_dir() / f"{board_id}_edges.json"


def ensure_lock_path(key: str) -> Path:
    """Lock file for a cross-board 'ensure by key' operation (e.g. remote URL)."""
    d = home() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.lock"
