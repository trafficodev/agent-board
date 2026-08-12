"""End-to-end smoke test for the installed `agent-board` CLI.

Regression guard for the packaging change that moved the CLI from
`backend/cli.py` to `src/agent_board/cli.py`. The old installer generated a
wrapper that hard-coded `.../backend/cli.py`, so every `agent-board <cmd>`
broke once the file moved. The fix is to ship `agent-board` as a console entry
point (`[project.scripts]` in pyproject) that imports `agent_board.cli:main` —
which resolves regardless of where the source lives.

This test installs the package into a throwaway virtualenv exactly the way a
user would (via uv, the project's toolchain), then runs the *installed*
`agent-board list_boards` command and asserts it returns a JSON list. It
exercises the real entry point, not the source tree, so a reintroduced
hard-coded path or a missing/renamed entry point fails here.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = platform.system() == "Windows"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _venv_bin(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if IS_WINDOWS else "bin")


def _kill_listener(port: int) -> None:
    """Best-effort teardown of the backend the CLI auto-starts on `port`."""
    if IS_WINDOWS or shutil.which("lsof") is None:
        return
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
    )
    for pid in result.stdout.split():
        subprocess.run(["kill", pid], capture_output=True)


@pytest.mark.slow
def test_installed_cli_list_boards(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to install the package for the smoke test")

    venv_dir = tmp_path / "venv"
    subprocess.run([uv, "venv", str(venv_dir)], check=True, capture_output=True)

    # Install this checkout the way a user would: builds the wheel from
    # pyproject and drops the `agent-board` console script into the venv.
    venv_python = _venv_bin(venv_dir) / ("python.exe" if IS_WINDOWS else "python")
    subprocess.run(
        [uv, "pip", "install", "--python", str(venv_python), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )

    agent_board = _venv_bin(venv_dir) / ("agent-board.exe" if IS_WINDOWS else "agent-board")
    assert agent_board.exists(), (
        f"console script not installed at {agent_board}; "
        "check [project.scripts] in pyproject.toml"
    )

    port = _free_port()
    env = {
        **os.environ,
        "AGENT_BOARD_HOME": str(tmp_path / "data"),  # isolated, empty data dir
        "AGENT_BOARD_URL": f"http://127.0.0.1:{port}",  # auto-start here
        "PATH": os.pathsep.join([str(_venv_bin(venv_dir)), os.environ.get("PATH", "")]),
    }

    try:
        result = subprocess.run(
            [str(agent_board), "list_boards"],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    finally:
        _kill_listener(port)

    assert result.returncode == 0, (
        f"`agent-board list_boards` failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    boards = json.loads(result.stdout)
    assert isinstance(boards, list), f"expected a JSON list, got: {result.stdout!r}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
