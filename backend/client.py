"""HTTP client for Agent Board API.

Auto-starts a local backend server only when the configured endpoint is not
already healthy. Any MCP tool or CLI should import from here instead of calling
the API directly.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

BASE_URL = os.environ.get("AGENT_BOARD_URL", "http://localhost:8001")
SESSION_HEADER = "X-Better-Agent-Session"
_API_PATH = Path(__file__).resolve().parent
_BACKEND_DIR = _API_PATH
_STARTUP_ATTEMPTS = 50
_STARTUP_INTERVAL_SECONDS = 0.2
_STOP_TIMEOUT_SECONDS = 2
_SERVER_ENV_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "AGENT_BOARD_HOME",
)

_process: subprocess.Popen | None = None
_start_lock = threading.Lock()


class AgentBoardUnavailableError(RuntimeError):
    pass


def _normalized_base_url() -> str:
    return BASE_URL.rstrip("/")


def _is_running() -> bool:
    try:
        with urllib.request.urlopen(f"{_normalized_base_url()}/api/boards", timeout=2) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _local_server_port() -> int:
    try:
        parsed = urlsplit(BASE_URL)
        port = parsed.port
    except ValueError as exc:
        raise AgentBoardUnavailableError(f"Invalid AGENT_BOARD_URL: {BASE_URL}") from exc

    if (
        parsed.scheme != "http"
        or (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise AgentBoardUnavailableError(
            f"Agent Board backend is unavailable at {BASE_URL}; auto-start requires "
            "http://localhost:<port> or http://127.0.0.1:<port>"
        )
    return port


def _python_executable() -> str:
    candidates = (
        _BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        _BACKEND_DIR / ".venv" / "bin" / "python",
    )
    return str(next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable)))


def _server_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _SERVER_ENV_KEYS if key in os.environ}


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_STOP_TIMEOUT_SECONDS)
    except OSError:
        return


def ensure_server() -> None:
    global _process
    if _is_running():
        return

    with _start_lock:
        if _is_running():
            return

        port = _local_server_port()
        if not (_BACKEND_DIR / "main.py").is_file():
            raise AgentBoardUnavailableError(f"Agent Board backend is unavailable at {_BACKEND_DIR}")

        try:
            process = subprocess.Popen(
                [
                    _python_executable(),
                    "-m",
                    "uvicorn",
                    "main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=str(_BACKEND_DIR),
                env=_server_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise AgentBoardUnavailableError(
                f"Agent Board backend could not start at {BASE_URL}: {exc}"
            ) from exc

        _process = process
        for _ in range(_STARTUP_ATTEMPTS):
            if _is_running():
                if process.poll() is not None and _process is process:
                    _process = None
                return
            if process.poll() is not None:
                break
            time.sleep(_STARTUP_INTERVAL_SECONDS)

        _stop_process(process)
        if _process is process:
            _process = None
        if _is_running():
            return
        raise AgentBoardUnavailableError(f"Agent Board backend failed to start at {BASE_URL}")


def _req(method: str, path: str, body: dict | None = None) -> dict | list:
    ensure_server()
    url = f"{_normalized_base_url()}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    # The MCP server knows which Better Agent session is calling; the API
    # server is shared and does not. Forward the identity so card history is
    # attributed to the session that actually made the change.
    session_id = (
        os.environ.get("BETTER_AGENT_APP_SESSION_ID")
        or os.environ.get("BETTER_CLAUDE_APP_SESSION_ID")
        or ""
    ).strip()
    if session_id:
        headers[SESSION_HEADER] = session_id
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"{e.code} {e.reason}: {detail}") from e
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise AgentBoardUnavailableError(f"Agent Board backend is unavailable at {BASE_URL}: {exc}") from exc


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


def link_project_remote(board_id: str, remote_url: str) -> dict:
    return _req("POST", f"/api/boards/{board_id}/project-remotes", {"remote_url": remote_url})


def consolidate_project_board(source_board_id: str, target_board_id: str) -> dict:
    return _req("POST", "/api/projects/consolidate-board", {
        "source_board_id": source_board_id,
        "target_board_id": target_board_id,
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


def search_cards(
    board_id: str, query: str = "", priority: str | None = None, label: str | None = None, sort: str | None = None,
) -> list[dict]:
    params = urllib.parse.urlencode({k: v for k, v in {"query": query, "priority": priority, "label": label, "sort": sort}.items() if v})
    path = f"/api/boards/{board_id}/cards/search"
    if params:
        path += f"?{params}"
    return _req("GET", path)


def ai_search_cards(
    board_id: str, query: str, priority: str | None = None, label: str | None = None, max_results: int | None = None,
) -> dict:
    params = urllib.parse.urlencode({
        k: v
        for k, v in {"query": query, "priority": priority, "label": label, "max_results": max_results}.items()
        if v is not None and v != ""
    })
    path = f"/api/boards/{board_id}/cards/ai-search"
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


def bulk_cards(board_id: str, operations: list[dict]) -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards/bulk", {"operations": operations})


def update_card(board_id: str, card_id: str, **fields) -> dict:
    return _req("PATCH", f"/api/boards/{board_id}/cards/{card_id}", fields)


def add_card_note(board_id: str, card_id: str, text: str, kind: str = "note") -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards/{card_id}/notes", {"kind": kind, "text": text})


def answer_card_question(board_id: str, card_id: str, note_id: str, answer: str, answered_by: str = "") -> dict:
    return _req(
        "POST",
        f"/api/boards/{board_id}/cards/{card_id}/notes/{note_id}/answer",
        {"answer": answer, "answered_by": answered_by},
    )


def open_questions(board_id: str = "") -> dict:
    suffix = f"?board_id={board_id}" if board_id else ""
    return _req("GET", f"/api/questions/open{suffix}")


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


def get_card_history(board_id: str, card_id: str, at: str = "") -> dict:
    suffix = f"?at={urllib.parse.quote(at)}" if at else ""
    return _req("GET", f"/api/boards/{board_id}/cards/{card_id}/history{suffix}")


def revert_card(board_id: str, card_id: str, version: int) -> dict:
    return _req("POST", f"/api/boards/{board_id}/cards/{card_id}/revert", {"version": version})


def get_session_activity(session_id: str, board_id: str = "", limit: int = 200) -> dict:
    query = f"?limit={limit}" + (f"&board_id={board_id}" if board_id else "")
    return _req("GET", f"/api/sessions/{urllib.parse.quote(session_id)}/activity{query}")


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
