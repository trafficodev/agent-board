"""HTTP client for Agent Board API.

Auto-starts the backend server if it's not already running.
Any MCP tool or CLI should import from here instead of calling the API directly.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import json
import urllib.parse
from pathlib import Path

BASE_URL = os.environ.get("AGENT_BOARD_URL", "http://localhost:8001")
_API_PATH = Path(__file__).resolve().parent
_PROJECT_ROOT = _API_PATH.parent
_BACKEND_DIR = _API_PATH
_VENV_PYTHON = _BACKEND_DIR / ".venv" / "bin" / "python"
_PORT = int(BASE_URL.rsplit(":", 1)[-1].rstrip("/"))

_process: subprocess.Popen | None = None


def _is_running() -> bool:
    try:
        urllib.request.urlopen(f"{BASE_URL}/api/boards", timeout=2)
        return True
    except Exception:
        return False


def _start_server() -> None:
    global _process
    if _is_running():
        return

    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    _process = subprocess.Popen(
        [python, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", f"--port={_PORT}"],
        cwd=str(_BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 10s for the server to be ready
    for _ in range(50):
        if _is_running():
            return
        time.sleep(0.2)

    raise RuntimeError(f"Agent Board backend failed to start on {BASE_URL}")


def _ensure():
    _start_server()


def _req(method: str, path: str, body: dict | None = None) -> dict | list:
    _ensure()
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"{e.code} {e.reason}: {detail}") from e


# --- Public API ---

def list_boards() -> list[dict]:
    return _req("GET", "/api/boards")


def get_board(board_id: str) -> dict:
    return _req("GET", f"/api/boards/{board_id}")


def create_board(name: str, description: str = "", columns: list[str] | None = None) -> dict:
    return _req("POST", "/api/boards", {"name": name, "description": description, "columns": columns})


def update_board(
    board_id: str, name: str | None = None, description: str | None = None, remote_url: str | None = None,
) -> dict:
    body = {k: v for k, v in {"name": name, "description": description, "remote_url": remote_url}.items() if v is not None}
    return _req("PATCH", f"/api/boards/{board_id}", body)


def ensure_project_board(
    remote_url: str, name: str | None = None, description: str = "", columns: list[str] | None = None,
) -> dict:
    return _req("POST", "/api/projects/ensure-board", {
        "remote_url": remote_url, "name": name, "description": description, "columns": columns,
    })


def delete_board(board_id: str) -> dict:
    return _req("DELETE", f"/api/boards/{board_id}")


def import_board(name: str, columns: list[str], cards: list[dict], description: str = "") -> dict:
    return _req("POST", "/api/import/board", {
        "name": name,
        "description": description,
        "columns": columns,
        "cards": cards,
    })


def get_canvas_sync(board_id: str) -> dict:
    return _req("GET", f"/api/boards/{board_id}/canvas-sync")


def enable_canvas_sync(board_id: str, canvas_api_url: str | None = None) -> dict:
    body = {}
    if canvas_api_url is not None:
        body["canvas_api_url"] = canvas_api_url
    return _req("POST", f"/api/boards/{board_id}/canvas-sync/enable", body)


def disable_canvas_sync(board_id: str) -> dict:
    return _req("POST", f"/api/boards/{board_id}/canvas-sync/disable")


def sync_canvas(board_id: str) -> dict:
    return _req("POST", f"/api/boards/{board_id}/canvas-sync/sync")


def add_column(board_id: str, name: str, position: int | None = None) -> dict:
    return _req("POST", f"/api/boards/{board_id}/columns", {"name": name, "position": position})


def update_column(board_id: str, column_id: str, name: str | None = None, position: int | None = None) -> dict:
    body = {k: v for k, v in {"name": name, "position": position}.items() if v is not None}
    return _req("PATCH", f"/api/boards/{board_id}/columns/{column_id}", body)


def delete_column(board_id: str, column_id: str) -> dict:
    return _req("DELETE", f"/api/boards/{board_id}/columns/{column_id}")


def list_cards(board_id: str, **filters) -> list[dict]:
    params = urllib.parse.urlencode({k: v for k, v in filters.items() if v is not None})
    path = f"/api/boards/{board_id}/cards"
    if params:
        path += f"?{params}"
    return _req("GET", path)


def search_cards(board_id: str, query: str = "", priority: str | None = None, label: str | None = None) -> list[dict]:
    params = urllib.parse.urlencode({k: v for k, v in {"query": query, "priority": priority, "label": label}.items() if v})
    path = f"/api/boards/{board_id}/cards/search"
    if params:
        path += f"?{params}"
    return _req("GET", path)


def create_card(board_id: str, title: str, column_id: str, body: str = "",
                parent_id: str | None = None, priority: str = "medium",
                labels: list[str] | None = None, external_id: str = "",
                metadata: dict | None = None) -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards", {
        "external_id": external_id, "title": title, "body": body, "column_id": column_id,
        "parent_id": parent_id, "priority": priority, "labels": labels or [],
        "metadata": metadata or {},
    })


def get_card(board_id: str, card_id: str) -> dict:
    return _req("GET", f"/api/boards/{board_id}/cards/{card_id}")


def update_card(board_id: str, card_id: str, **fields) -> dict:
    return _req("PATCH", f"/api/boards/{board_id}/cards/{card_id}", fields)


def move_card(board_id: str, card_id: str, column_id: str, position: int | None = None) -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards/{card_id}/move", {"column_id": column_id, "position": position})


def delete_card(board_id: str, card_id: str) -> dict:
    return _req("DELETE", f"/api/boards/{board_id}/cards/{card_id}")


def add_session(board_id: str, card_id: str, session_id: str, system: str = "",
                action: str = "", outcome: str | None = None) -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards/{card_id}/sessions", {
        "session_id": session_id, "system": system, "action": action, "outcome": outcome,
    })


def get_events(board_id: str, limit: int = 100) -> list[dict]:
    return _req("GET", f"/api/boards/{board_id}/events?limit={limit}")


def list_edges(board_id: str, card_id: str | None = None, type: str | None = None) -> list[dict]:
    params = urllib.parse.urlencode({k: v for k, v in {"card_id": card_id, "type": type}.items() if v})
    path = f"/api/boards/{board_id}/edges"
    if params:
        path += f"?{params}"
    return _req("GET", path)


def create_edge(board_id: str, from_card_id: str, to_card_id: str,
                type: str = "relates_to", label: str = "") -> dict:
    return _req("POST", f"/api/boards/{board_id}/edges", {
        "from_card_id": from_card_id, "to_card_id": to_card_id, "type": type, "label": label,
    })


def delete_edge(board_id: str, edge_id: str) -> dict:
    return _req("DELETE", f"/api/boards/{board_id}/edges/{edge_id}")
