"""Board + Column persistence. One JSON file per board."""

import json
from pathlib import Path

from models import Board, Column, Event, _now, _uid
from paths import board_path, boards_dir, events_path


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


# --- Boards ---

def list_boards() -> list[Board]:
    boards = []
    for p in boards_dir().glob("*.json"):
        if p.name.endswith("_cards.json"):
            continue
        data = _read_json(p)
        if data:
            try:
                boards.append(Board.model_validate(data))
            except Exception:
                pass  # skip corrupt files, don't brick the whole list
    return sorted(boards, key=lambda b: b.updated_at, reverse=True)


def get_board(board_id: str) -> Board | None:
    data = _read_json(board_path(board_id))
    return Board.model_validate(data) if data else None


def create_board(name: str, description: str = "", column_names: list[str] | None = None) -> Board:
    names = column_names or ["Backlog", "In Progress", "Review", "Done"]
    board = Board(
        name=name,
        description=description,
        columns=[Column(name=n, position=i) for i, n in enumerate(names)],
    )
    for col in board.columns:
        col.board_id = board.id
    _save_board(board)
    _append_event(board.id, Event(type="board_created", detail=name))
    return board


def update_board(board_id: str, **kwargs) -> Board | None:
    board = get_board(board_id)
    if not board:
        return None
    for k, v in kwargs.items():
        if v is not None:
            setattr(board, k, v)
    board.updated_at = _now()
    _save_board(board)
    return board


def delete_board(board_id: str) -> bool:
    p = board_path(board_id)
    if p.exists():
        p.unlink()
        ep = events_path(board_id)
        if ep.exists():
            ep.unlink()
        # Delete associated cards file
        cp = p.parent / f"{board_id}_cards.json"
        if cp.exists():
            cp.unlink()
        return True
    return False


# --- Columns ---

def add_column(board_id: str, name: str, position: int | None = None) -> Column | None:
    board = get_board(board_id)
    if not board:
        return None
    pos = position if position is not None else len(board.columns)
    col = Column(board_id=board_id, name=name, position=pos)
    board.columns.insert(pos, col)
    # reindex positions
    for i, c in enumerate(board.columns):
        c.position = i
    board.updated_at = _now()
    _save_board(board)
    _append_event(board_id, Event(type="column_added", detail=name))
    return col


def update_column(board_id: str, column_id: str, **kwargs) -> Column | None:
    board = get_board(board_id)
    if not board:
        return None
    col = next((c for c in board.columns if c.id == column_id), None)
    if not col:
        return None
    for k, v in kwargs.items():
        if v is not None:
            setattr(col, k, v)
    # reindex if position changed — remove-then-insert, not sort
    if "position" in kwargs and kwargs["position"] is not None:
        board.columns.remove(col)
        pos = min(kwargs["position"], len(board.columns))
        board.columns.insert(pos, col)
        for i, c in enumerate(board.columns):
            c.position = i
    board.updated_at = _now()
    _save_board(board)
    return col


def delete_column(board_id: str, column_id: str) -> bool:
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
    return True


def _save_board(board: Board) -> None:
    _write_json(board_path(board.id), board.model_dump(mode="json"))
