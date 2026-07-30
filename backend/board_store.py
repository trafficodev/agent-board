"""Board + Column persistence, in SQLite."""

import fcntl
import hashlib
import re

import db
from models import Board, Column, Event, _now, _uid
from paths import ensure_lock_path

_SCP_LIKE_RE = re.compile(r"^[\w.-]+@([\w.-]+):(.+)$")
_URL_LIKE_RE = re.compile(r"^\w+://(?:[^@/]+@)?([^/]+)/(.+)$")


class ProjectRemoteConflictError(ValueError):
    pass


def _append_event(board_id: str, event: Event) -> None:
    event.board_id = event.board_id or board_id
    with db.transaction() as conn:
        db.write_event(conn, event)


def _columns_of(conn, board_id: str) -> list[Column]:
    return [
        Column(id=row["id"], board_id=row["board_id"], name=row["name"], position=row["position"])
        for row in conn.execute(
            "SELECT * FROM columns WHERE board_id=? ORDER BY position", (board_id,)
        )
    ]


def _aliases_of(conn, board_id: str) -> list[str]:
    return [
        row["remote_url"]
        for row in conn.execute(
            "SELECT remote_url FROM board_remotes WHERE board_id=? AND is_primary=0"
            " ORDER BY remote_url",
            (board_id,),
        )
    ]


def _acquire_remote_lock(normalized_remote_url: str):
    """A lock keyed by one remote URL, held across operations that span more
    than a single database transaction.

    Board writes no longer need a lock -- the write transaction is the lock --
    but consolidation prepares a manifest in one transaction and applies it in
    another, and nothing may link that remote to a different board in between.
    """
    lock_path = ensure_lock_path(hashlib.sha256(normalized_remote_url.encode()).hexdigest()[:24])
    lock_file = open(lock_path, "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def _release_remote_lock(lock_file) -> None:
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


def _board_from(conn, row) -> Board:
    return db.board_from_row(row, _columns_of(conn, row["id"]), _aliases_of(conn, row["id"]))


# --- Boards ---

def list_boards() -> list[Board]:
    conn = db.connect()
    rows = conn.execute("SELECT * FROM boards ORDER BY updated_at DESC").fetchall()
    return [_board_from(conn, row) for row in rows]


def get_board(board_id: str) -> Board | None:
    conn = db.connect()
    row = conn.execute("SELECT * FROM boards WHERE id=?", (board_id,)).fetchone()
    return _board_from(conn, row) if row else None


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
    with db.transaction() as conn:
        _save_board(conn, board)
        db.write_event(conn, Event(type="board_created", detail=name, board_id=board.id))
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
        candidate = url.lower().rstrip("/")
        # A git remote always has host/path structure. A bare token (e.g. a
        # board id passed here by mistake instead of an actual remote) has
        # none, and would otherwise be silently accepted as its own project
        # identity -- creating a phantom board that every future mistaken
        # call keeps finding and reusing instead of failing loudly.
        return candidate if "/" in candidate else ""
    host, path = match.group(1), match.group(2)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


def get_board_by_remote_url(remote_url: str) -> Board | None:
    """One indexed lookup. Every remote a board answers to -- its own plus each
    alias -- is a row in board_remotes, so this never walks the board list."""
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        return None
    conn = db.connect()
    row = conn.execute(
        "SELECT board_id FROM board_remotes WHERE remote_url=?", (normalized,)
    ).fetchone()
    return get_board(row["board_id"]) if row else None


def ensure_project_board(
    remote_url: str, name: str | None = None, description: str = "", column_names: list[str] | None = None,
) -> Board:
    """Idempotent get-or-create keyed by normalized remote URL.

    The whole check-then-create runs in one write transaction, so two callers
    racing on the same remote cannot both find nothing and both create a board.
    """
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        raise ValueError("remote_url is required")

    with db.transaction():
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


def link_project_remote(board_id: str, remote_url: str) -> Board | None:
    normalized = normalize_remote_url(remote_url)
    if not normalized:
        raise ValueError("remote_url is required")

    with db.transaction() as conn:
        owner = get_board_by_remote_url(normalized)
        if owner:
            if owner.id != board_id:
                raise ProjectRemoteConflictError("remote_url is already linked to another board")
            return owner
        board = get_board(board_id)
        if not board:
            return None
        conn.execute(
            "INSERT INTO board_remotes (board_id, remote_url, is_primary) VALUES (?,?,0)",
            (board_id, normalized),
        )
        board.remote_aliases.append(normalized)
        board.updated_at = _now()
        conn.execute(
            "UPDATE boards SET updated_at=? WHERE id=?",
            (board.updated_at.isoformat(), board_id),
        )
        return board


def _update_board_under_lock(board_id: str, kwargs: dict) -> Board | None:
    with db.transaction() as conn:
        board = get_board(board_id)
        if not board:
            return None
        if "remote_url" in kwargs:
            # The alias being promoted to the board's own remote stops being an
            # alias, or the board would answer to it twice.
            board.remote_aliases = [
                alias for alias in board.remote_aliases
                if normalize_remote_url(alias) != kwargs["remote_url"]
            ]
        for k, v in kwargs.items():
            if v is not None:
                setattr(board, k, v)
        board.updated_at = _now()
        _save_board(conn, board)
        return board


def update_board(board_id: str, **kwargs) -> Board | None:
    if kwargs.get("remote_url") is None:
        return _update_board_under_lock(board_id, kwargs)

    normalized = normalize_remote_url(kwargs["remote_url"])
    if not normalized:
        raise ValueError("remote_url is required")
    kwargs["remote_url"] = normalized

    with db.transaction():
        owner = get_board_by_remote_url(normalized)
        if owner and owner.id != board_id:
            raise ProjectRemoteConflictError("remote_url is already linked to another board")
        return _update_board_under_lock(board_id, kwargs)


def delete_board(board_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM boards WHERE id=?", (board_id,))
        if not cursor.rowcount:
            return False
        # Columns, cards and edges follow the board by foreign key. The journal
        # deliberately has no key to the board -- it outlives cards -- so it is
        # cleared here, matching the old delete of the board's events file.
        conn.execute("DELETE FROM events WHERE board_id=?", (board_id,))
        return True


# --- Columns ---

def add_column(board_id: str, name: str, position: int | None = None) -> Column | None:
    with db.transaction() as conn:
        board = get_board(board_id)
        if not board:
            return None
        pos = position if position is not None else len(board.columns)
        col = Column(board_id=board_id, name=name, position=pos)
        board.columns.insert(pos, col)
        for i, c in enumerate(board.columns):
            c.position = i
        board.updated_at = _now()
        _save_board(conn, board)
        db.write_event(conn, Event(type="column_added", detail=name, board_id=board_id))
        return col


def update_column(board_id: str, column_id: str, **kwargs) -> Column | None:
    with db.transaction() as conn:
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
        _save_board(conn, board)
        return col


def delete_column(board_id: str, column_id: str) -> bool:
    with db.transaction() as conn:
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
        conn.execute("DELETE FROM columns WHERE id=?", (column_id,))
        _save_board(conn, board)
        db.write_event(conn, Event(type="column_removed", detail=column_id, board_id=board_id))
        # Cards in a removed column go with it; they have nowhere to live.
        conn.execute(
            "DELETE FROM cards WHERE board_id=? AND column_id=?", (board_id, column_id)
        )
        return True


def _save_board(conn, board: Board) -> None:
    conn.execute(
        """INSERT INTO boards (id, name, description, remote_url, created_at, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, description=excluded.description,
               remote_url=excluded.remote_url, updated_at=excluded.updated_at""",
        (
            board.id, board.name, board.description, board.remote_url,
            board.created_at.isoformat(), board.updated_at.isoformat(),
        ),
    )
    for column in board.columns:
        conn.execute(
            """INSERT INTO columns (id, board_id, name, position) VALUES (?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, position=excluded.position""",
            (column.id, board.id, column.name, column.position),
        )
    # The remote set is small and fully derived from the board, so it is
    # rewritten rather than diffed -- it cannot drift that way. Rows are stored
    # normalized: a board created with a raw `git@host:a/b.git` has to be found
    # by a lookup for `host/a/b`, and normalizing on write is what makes that
    # one index hit instead of a scan that normalizes every candidate.
    conn.execute("DELETE FROM board_remotes WHERE board_id=?", (board.id,))
    remotes = [(board.remote_url, 1)] if board.remote_url else []
    remotes += [(alias, 0) for alias in board.remote_aliases]
    for url, primary in remotes:
        normalized = normalize_remote_url(url)
        if not normalized:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO board_remotes (board_id, remote_url, is_primary)"
            " VALUES (?,?,?)",
            (board.id, normalized, primary),
        )
