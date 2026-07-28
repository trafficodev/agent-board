"""Board + Column persistence. One JSON file per board."""

import fcntl
import hashlib
import json
import re
from pathlib import Path

from models import Board, Column, Event, _now, _uid
from paths import board_path, boards_dir, ensure_lock_path, events_path

_SCP_LIKE_RE = re.compile(r"^[\w.-]+@([\w.-]+):(.+)$")
_URL_LIKE_RE = re.compile(r"^\w+://(?:[^@/]+@)?([^/]+)/(.+)$")


class ProjectRemoteConflictError(ValueError):
    pass


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _append_event(board_id: str, event: Event) -> None:
    p = events_path(board_id)
    with open(p, "a") as f:
        f.write(event.model_dump_json() + "\n")


def _with_lock(board_id: str):
    """Context manager: acquires a per-board file lock for the duration."""
    lock_path = board_path(board_id).parent / f"{board_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def _release_lock(lock_file):
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


# --- Boards ---

def list_boards() -> list[Board]:
    boards = []
    for p in boards_dir().glob("*.json"):
        if p.name.endswith("_cards.json") or p.name.endswith(".lock"):
            continue
        data = _read_json(p)
        if data:
            try:
                boards.append(Board.model_validate(data))
            except Exception:
                pass
    return sorted(boards, key=lambda b: b.updated_at, reverse=True)


def get_board(board_id: str) -> Board | None:
    data = _read_json(board_path(board_id))
    return Board.model_validate(data) if data else None


def create_board(
    name: str, description: str = "", column_names: list[str] | None = None, remote_url: str = "",
) -> Board:
    names = column_names or ["Backlog", "In Progress", "Review", "Done"]
    board = Board(
        name=name,
        description=description,
        remote_url=remote_url,
        columns=[Column(name=n, position=i) for i, n in enumerate(names)],
    )
    for col in board.columns:
        col.board_id = board.id
    _save_board(board)
    _append_event(board.id, Event(type="board_created", detail=name))
    return board


def normalize_remote_url(url: str) -> str:
    """Collapse the scp-like, ssh://, https://, and .git-suffixed forms of the
    same git remote to one identity string, e.g. `git@github.com:a/b.git`,
    `https://github.com/a/b.git`, and `https://github.com/a/b` all become
    `github.com/a/b`. Boards are keyed by this so every worktree/clone of the
    same repo (same remote) shares one project board."""
    url = url.strip()
    if not url:
        return ""
    match = _SCP_LIKE_RE.match(url) or _URL_LIKE_RE.match(url)
    if not match:
        return url.lower().rstrip("/")
    host, path = match.group(1), match.group(2)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


def get_board_by_remote_url(remote_url: str) -> Board | None:
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        return None
    for board in list_boards():
        project_remotes = [board.remote_url, *board.remote_aliases]
        if any(normalize_remote_url(remote) == normalized for remote in project_remotes if remote):
            return board
    return None


def _acquire_remote_lock(normalized_remote_url: str):
    lock_path = ensure_lock_path(hashlib.sha256(normalized_remote_url.encode()).hexdigest()[:24])
    lock_file = open(lock_path, "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def _release_remote_lock(lock_file) -> None:
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


def ensure_project_board(
    remote_url: str, name: str | None = None, description: str = "", column_names: list[str] | None = None,
) -> Board:
    """Idempotent get-or-create keyed by normalized remote URL."""
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        raise ValueError("remote_url is required")

    lock_file = _acquire_remote_lock(normalized)
    try:
        existing = get_board_by_remote_url(normalized)
        if existing:
            return existing
        default_name = normalized.rsplit("/", 1)[-1]
        return create_board(
            name=name or default_name,
            description=description,
            column_names=column_names or ["Open Items", "In Progress", "In Testing", "Done"],
            remote_url=normalized,
        )
    finally:
        _release_remote_lock(lock_file)


def link_project_remote(board_id: str, remote_url: str) -> Board | None:
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        raise ValueError("remote_url is required")

    remote_lock = _acquire_remote_lock(normalized)
    try:
        owner = get_board_by_remote_url(normalized)
        if owner:
            if owner.id != board_id:
                raise ProjectRemoteConflictError("remote_url is already linked to another board")
            return owner

        board_lock = _with_lock(board_id)
        try:
            board = get_board(board_id)
            if not board:
                return None
            board.remote_aliases.append(normalized)
            board.updated_at = _now()
            _save_board(board)
            return board
        finally:
            _release_lock(board_lock)
    finally:
        _release_remote_lock(remote_lock)


def _update_board_under_lock(board_id: str, kwargs: dict) -> Board | None:
    lock = _with_lock(board_id)
    try:
        board = get_board(board_id)
        if not board:
            return None
        if "remote_url" in kwargs:
            board.remote_aliases = [
                alias for alias in board.remote_aliases
                if normalize_remote_url(alias) != kwargs["remote_url"]
            ]
        for k, v in kwargs.items():
            if v is not None:
                setattr(board, k, v)
        board.updated_at = _now()
        _save_board(board)
        return board
    finally:
        _release_lock(lock)


def update_board(board_id: str, **kwargs) -> Board | None:
    if kwargs.get("remote_url") is None:
        return _update_board_under_lock(board_id, kwargs)

    normalized = normalize_remote_url(kwargs["remote_url"])
    if not normalized:
        raise ValueError("remote_url is required")
    kwargs["remote_url"] = normalized

    remote_lock = _acquire_remote_lock(normalized)
    try:
        owner = get_board_by_remote_url(normalized)
        if owner and owner.id != board_id:
            raise ProjectRemoteConflictError("remote_url is already linked to another board")
        return _update_board_under_lock(board_id, kwargs)
    finally:
        _release_remote_lock(remote_lock)


def delete_board(board_id: str) -> bool:
    p = board_path(board_id)
    if p.exists():
        p.unlink()
        ep = events_path(board_id)
        if ep.exists():
            ep.unlink()
        cp = p.parent / f"{board_id}_cards.json"
        if cp.exists():
            cp.unlink()
        qp = p.parent / f"{board_id}_open_questions.json"
        if qp.exists():
            qp.unlink()
        lp = p.parent / f"{board_id}.lock"
        if lp.exists():
            lp.unlink()
        return True
    return False


# --- Columns ---

def add_column(board_id: str, name: str, position: int | None = None) -> Column | None:
    lock = _with_lock(board_id)
    try:
        board = get_board(board_id)
        if not board:
            return None
        pos = position if position is not None else len(board.columns)
        col = Column(board_id=board_id, name=name, position=pos)
        board.columns.insert(pos, col)
        for i, c in enumerate(board.columns):
            c.position = i
        board.updated_at = _now()
        _save_board(board)
        _append_event(board_id, Event(type="column_added", detail=name))
        return col
    finally:
        _release_lock(lock)


def update_column(board_id: str, column_id: str, **kwargs) -> Column | None:
    lock = _with_lock(board_id)
    try:
        board = get_board(board_id)
        if not board:
            return None
        col = next((c for c in board.columns if c.id == column_id), None)
        if not col:
            return None
        for k, v in kwargs.items():
            if v is not None:
                setattr(col, k, v)
        if "position" in kwargs and kwargs["position"] is not None:
            board.columns.remove(col)
            pos = min(kwargs["position"], len(board.columns))
            board.columns.insert(pos, col)
            for i, c in enumerate(board.columns):
                c.position = i
        board.updated_at = _now()
        _save_board(board)
        return col
    finally:
        _release_lock(lock)


def delete_column(board_id: str, column_id: str) -> bool:
    lock = _with_lock(board_id)
    try:
        board = get_board(board_id)
        if not board:
            return False
        before = len(board.columns)
        board.columns = [c for c in board.columns if c.id != column_id]
        if len(board.columns) == before:
            return False
        for i, c in enumerate(board.columns):
            c.position = i
        board.updated_at = _now()
        _save_board(board)
        _append_event(board_id, Event(type="column_removed", detail=column_id))
        # Delete orphaned cards that belonged to this column
        from card_store import _read_cards, _write_cards
        cards = _read_cards(board_id)
        cards = [c for c in cards if c.column_id != column_id]
        _write_cards(board_id, cards)
        return True
    finally:
        _release_lock(lock)


def _save_board(board: Board) -> None:
    _write_json(board_path(board.id), board.model_dump(mode="json"))
