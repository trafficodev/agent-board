#!/usr/bin/env python3
"""Register / deregister Agent Board's MCP server with installed providers.

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

sys.path.insert(0, str(REPO_ROOT / "backend"))
from mcp_tools import GROUPS  # noqa: E402

GROUP_NAMES = sorted(GROUPS)
SERVER_NAME = "agent-board"


def _legacy_server_name(group: str) -> str:
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


def _result_detail(result: subprocess.CompletedProcess) -> str:
    return (result.stderr.strip() or result.stdout.strip()).lower()


def _is_missing(result: subprocess.CompletedProcess) -> bool:
    detail = _result_detail(result)
    return any(marker in detail for marker in ("not found", "does not exist", "no mcp server"))


def _is_already_registered(result: subprocess.CompletedProcess) -> bool:
    detail = _result_detail(result)
    return "already" in detail and any(
        marker in detail for marker in ("exist", "registered", "configured")
    )


def _remove_legacy(provider: str, remove_args: list[str]) -> list[str]:
    failures = []
    for group in GROUP_NAMES:
        name = _legacy_server_name(group)
        result = _run([provider, "mcp", "remove", *remove_args, name])
        if result.returncode != 0 and not _is_missing(result):
            failures.append(f"{name}: {_result_detail(result)}")
    return failures


def _finish_install(
    provider: str,
    remove_args: list[str],
    result: subprocess.CompletedProcess,
) -> str:
    if result.returncode != 0 and not _is_already_registered(result):
        return f"FAILED: {_result_detail(result)}"
    cleanup_failures = _remove_legacy(provider, remove_args)
    if cleanup_failures:
        return f"FAILED cleanup: {'; '.join(cleanup_failures)}"
    return "ok"


def _install_claude() -> str:
    result = _run(["claude", "mcp", "add", "-s", "user", SERVER_NAME, "--", str(VENV_PYTHON), str(MCP_SERVER)])
    return _finish_install("claude", ["-s", "user"], result)


def _install_codex() -> str:
    result = _run(["codex", "mcp", "add", SERVER_NAME, "--", str(VENV_PYTHON), str(MCP_SERVER)])
    return _finish_install("codex", [], result)


def _install_gemini() -> str:
    result = _run(["gemini", "mcp", "add", "-s", "user", SERVER_NAME, str(VENV_PYTHON), str(MCP_SERVER)])
    return _finish_install("gemini", ["-s", "user"], result)


def _uninstall_claude() -> str:
    _remove_legacy("claude", ["-s", "user"])
    result = _run(["claude", "mcp", "remove", "-s", "user", SERVER_NAME])
    return "ok" if result.returncode == 0 else "not registered"


def _uninstall_codex() -> str:
    _remove_legacy("codex", [])
    result = _run(["codex", "mcp", "remove", SERVER_NAME])
    return "ok" if result.returncode == 0 else "not registered"


def _uninstall_gemini() -> str:
    _remove_legacy("gemini", ["-s", "user"])
    result = _run(["gemini", "mcp", "remove", "-s", "user", SERVER_NAME])
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
        status = fn()
        print(f"[{provider}] {action} {SERVER_NAME}: {status}")
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
