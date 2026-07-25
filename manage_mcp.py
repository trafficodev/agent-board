#!/usr/bin/env python3
"""Register / deregister Agent Board's MCP tool groups with every installed
provider (Claude, Codex, Gemini), so they stay in sync automatically instead
of needing manual `claude mcp add` / `codex mcp add` / `gemini mcp add` runs.

Usage:
    python3 manage_mcp.py install
    python3 manage_mcp.py uninstall

Each provider is skipped (not failed) if its CLI isn't on PATH — this repo
doesn't require every provider to be installed. Install is idempotent: it
removes any existing registration for the same name first, so re-running
after moving the repo or rebuilding the venv refreshes the command path
instead of erroring on "already exists".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / "backend" / ".venv" / "bin" / "python3"
MCP_SERVER = REPO_ROOT / "backend" / "mcp_server.py"

# Keep this in sync with mcp_tools.GROUPS -- import it directly rather than
# hardcoding the group names a second time.
sys.path.insert(0, str(REPO_ROOT / "backend"))
from mcp_tools import GROUPS  # noqa: E402

GROUP_NAMES = sorted(GROUPS)


def _server_name(group: str) -> str:
    return f"agent-board-{group}"


def _check_venv() -> None:
    if not VENV_PYTHON.is_file():
        raise SystemExit(
            f"error: {VENV_PYTHON} does not exist. Set up the backend venv first "
            f"(cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)."
        )
    resolved = VENV_PYTHON.resolve()
    if not resolved.is_file():
        raise SystemExit(
            f"error: {VENV_PYTHON} is a broken symlink (target {resolved} does not exist). "
            f"Recreate the venv: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        )
    probe = subprocess.run([str(VENV_PYTHON), "-c", "import mcp"], capture_output=True)
    if probe.returncode != 0:
        raise SystemExit(
            f"error: {VENV_PYTHON} can't import the `mcp` package "
            f"({probe.stderr.decode().strip()}). Run: cd backend && .venv/bin/pip install -r requirements.txt"
        )


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def _install_claude(group: str) -> str:
    name = _server_name(group)
    _run(["claude", "mcp", "remove", "-s", "user", name])  # idempotent: ignore "not found"
    result = _run(["claude", "mcp", "add", "-s", "user", name, "--", str(VENV_PYTHON), str(MCP_SERVER), group])
    return "ok" if result.returncode == 0 else f"FAILED: {result.stderr.strip() or result.stdout.strip()}"


def _install_codex(group: str) -> str:
    name = _server_name(group)
    _run(["codex", "mcp", "remove", name])
    result = _run(["codex", "mcp", "add", name, "--", str(VENV_PYTHON), str(MCP_SERVER), group])
    return "ok" if result.returncode == 0 else f"FAILED: {result.stderr.strip() or result.stdout.strip()}"


def _install_gemini(group: str) -> str:
    name = _server_name(group)
    _run(["gemini", "mcp", "remove", "-s", "user", name])
    result = _run(["gemini", "mcp", "add", "-s", "user", name, str(VENV_PYTHON), str(MCP_SERVER), group])
    return "ok" if result.returncode == 0 else f"FAILED: {result.stderr.strip() or result.stdout.strip()}"


def _uninstall_claude(group: str) -> str:
    result = _run(["claude", "mcp", "remove", "-s", "user", _server_name(group)])
    return "ok" if result.returncode == 0 else "not registered"


def _uninstall_codex(group: str) -> str:
    result = _run(["codex", "mcp", "remove", _server_name(group)])
    return "ok" if result.returncode == 0 else "not registered"


def _uninstall_gemini(group: str) -> str:
    result = _run(["gemini", "mcp", "remove", "-s", "user", _server_name(group)])
    return "ok" if result.returncode == 0 else "not registered"


PROVIDERS = {
    "claude": (_install_claude, _uninstall_claude),
    "codex": (_install_codex, _uninstall_codex),
    "gemini": (_install_gemini, _uninstall_gemini),
}


def _run_for_all(action: str) -> int:
    if action == "install":
        _check_venv()
    failures = 0
    for provider, (install_fn, uninstall_fn) in PROVIDERS.items():
        if not shutil.which(provider):
            print(f"[{provider}] skipped -- CLI not on PATH")
            continue
        fn = install_fn if action == "install" else uninstall_fn
        for group in GROUP_NAMES:
            status = fn(group)
            print(f"[{provider}] {action} agent-board-{group}: {status}")
            if status.startswith("FAILED"):
                failures += 1
    return 1 if failures else 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        print("Usage: manage_mcp.py <install|uninstall>", file=sys.stderr)
        return 1
    return _run_for_all(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
