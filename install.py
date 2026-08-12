#!/usr/bin/env python3
"""Install the Agent Board MCP server into one or more agent hosts.

This is provider-agnostic. MCP hosts (Claude Code, Claude Desktop, Cursor,
Windsurf, ...) all register stdio servers with the same shape:

    "mcpServers": {
        "agent-board": {"command": "<python>", "args": ["<mcp_server.py>"]}
    }

so registering with a new host is mostly a matter of knowing which JSON file
to merge that block into.

What it does:
  1. Creates backend/.venv and installs the backend deps + the `mcp` package.
  2. Writes the agent-board server entry into each selected host's config
     (existing config is preserved and backed up to <file>.bak).

Usage:
  python3 install.py                 # set up venv + install into all detected hosts
  python3 install.py --list          # show which hosts were detected
  python3 install.py --provider claude-code
  python3 install.py --provider claude-code --provider cursor
  python3 install.py --all           # every known host, detected or not
  python3 install.py --config-file ~/some/other/mcp.json   # any other MCP host
  python3 install.py --skip-venv     # only (re)register, don't touch the venv
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = REPO_ROOT / "backend"
VENV_DIR = BACKEND_DIR / ".venv"
MCP_SERVER = BACKEND_DIR / "mcp_server.py"
DEFAULT_NAME = "agent-board"

IS_WINDOWS = platform.system() == "Windows"


MIN_PYTHON = (3, 10)  # the `mcp` package requires >= 3.10


def venv_python() -> Path:
    """Path to the interpreter inside backend/.venv for the current OS."""
    return VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _version_of(python: str) -> tuple[int, int] | None:
    """Return (major, minor) for an interpreter, or None if it can't be run."""
    try:
        out = subprocess.run(
            [python, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True, check=True).stdout.split()
        return (int(out[0]), int(out[1]))
    except Exception:
        return None


def find_base_python(override: str | None) -> str:
    """Pick an interpreter >= MIN_PYTHON to build the venv with.

    Order: explicit --python, the interpreter running this script, then common
    versioned names on PATH (newest first).
    """
    candidates = []
    if override:
        candidates.append(override)
    candidates.append(sys.executable)
    candidates += [f"python3.{m}" for m in range(20, MIN_PYTHON[1] - 1, -1)]
    candidates += ["python3", "python"]

    for name in candidates:
        resolved = shutil.which(name) or (name if Path(name).exists() else None)
        if not resolved:
            continue
        ver = _version_of(resolved)
        if ver and ver >= MIN_PYTHON:
            return resolved

    raise SystemExit(
        f"No Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} found (needed by the `mcp` package).\n"
        f"Install one (e.g. `brew install python@3.12`) or pass --python /path/to/python.")


# --- Host registry ---------------------------------------------------------
#
# Each entry maps a provider key to the JSON config file it registers into and
# the top-level key that holds the server map. `claude-code` is special-cased
# to prefer the `claude` CLI, which owns its config schema.

def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))


def _claude_desktop_config() -> Path:
    if IS_WINDOWS:
        return _appdata() / "Claude/claude_desktop_config.json"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


# provider key -> (config path, json key holding the server map)
JSON_HOSTS: dict[str, tuple[Path, str]] = {
    "claude-desktop": (_claude_desktop_config(), "mcpServers"),
    "cursor": (Path.home() / ".cursor/mcp.json", "mcpServers"),
    "windsurf": (Path.home() / ".codeium/windsurf/mcp_config.json", "mcpServers"),
}

ALL_PROVIDERS = ["claude-code", *JSON_HOSTS.keys()]


def server_spec() -> dict:
    """The MCP server entry every host receives."""
    return {"command": str(venv_python()), "args": [str(MCP_SERVER)]}


# --- venv setup ------------------------------------------------------------

def setup_venv(base_python: str) -> None:
    # Rebuild the venv if it's missing or its interpreter is too old.
    existing = _version_of(str(venv_python())) if venv_python().exists() else None
    if existing and existing < MIN_PYTHON:
        print(f"Existing venv is Python {existing[0]}.{existing[1]} (< "
              f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}); rebuilding.")
        shutil.rmtree(VENV_DIR)

    if not VENV_DIR.exists():
        print(f"Creating virtualenv at {VENV_DIR} (using {base_python}) ...")
        subprocess.run([base_python, "-m", "venv", str(VENV_DIR)], check=True)
    else:
        print(f"Reusing existing virtualenv at {VENV_DIR}")

    py = str(venv_python())
    print("Installing dependencies (backend + mcp) ...")
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=True)
    subprocess.run([py, "-m", "pip", "install", "--quiet",
                    "-r", str(BACKEND_DIR / "requirements.txt"),
                    "-r", str(BACKEND_DIR / "requirements-mcp.txt")], check=True)
    print("Dependencies installed.")


# --- registration ----------------------------------------------------------

def detected(provider: str) -> bool:
    """Is this host actually present on the machine?"""
    if provider == "claude-code":
        return shutil.which("claude") is not None or (Path.home() / ".claude.json").exists()
    path, _ = JSON_HOSTS[provider]
    return path.exists()


def register_json(path: Path, key: str, name: str, spec: dict) -> None:
    """Merge one server entry into a host's JSON config, preserving the rest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            config = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as e:
            raise SystemExit(f"  ! {path} is not valid JSON ({e}); left untouched.")
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    else:
        config = {}

    servers = config.setdefault(key, {})
    action = "Updated" if name in servers else "Added"
    servers[name] = spec
    path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  {action} '{name}' in {path}")


def register_claude_code(name: str, spec: dict) -> None:
    """Register with Claude Code via its own CLI (owns the config schema)."""
    claude = shutil.which("claude")
    if not claude:
        # Fall back to user-scope config file.
        register_json(Path.home() / ".claude.json", "mcpServers", name, spec)
        return
    # Idempotent: drop any existing entry, then add fresh.
    subprocess.run([claude, "mcp", "remove", name, "-s", "user"],
                   capture_output=True)
    proc = subprocess.run(
        [claude, "mcp", "add-json", name, json.dumps(spec), "-s", "user"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"  ! claude mcp add-json failed: {proc.stderr.strip() or proc.stdout.strip()}")
    print(f"  Registered '{name}' with Claude Code (user scope)")


def register(provider: str, name: str, spec: dict) -> None:
    if provider == "claude-code":
        register_claude_code(name, spec)
    else:
        path, key = JSON_HOSTS[provider]
        register_json(path, key, name, spec)


# --- CLI -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Install the Agent Board MCP server into agent hosts.")
    ap.add_argument("--provider", action="append", choices=ALL_PROVIDERS, default=[],
                    help="Host to install into (repeatable). Default: all detected hosts.")
    ap.add_argument("--all", action="store_true", help="Install into every known host, even undetected ones.")
    ap.add_argument("--config-file", type=Path, default=None,
                    help="Register into an arbitrary MCP host config JSON (escape hatch for other providers).")
    ap.add_argument("--json-key", default="mcpServers", help="Top-level key for --config-file (default: mcpServers).")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Server name to register (default: {DEFAULT_NAME}).")
    ap.add_argument("--python", default=None, help="Base interpreter to build the venv with (must be >= 3.10).")
    ap.add_argument("--skip-venv", action="store_true", help="Don't create/update the venv, only register.")
    ap.add_argument("--list", action="store_true", help="List known hosts and whether they were detected, then exit.")
    args = ap.parse_args()

    if args.list:
        print("Known MCP hosts:")
        for p in ALL_PROVIDERS:
            print(f"  {'[detected]' if detected(p) else '[   -    ]'} {p}")
        return

    if not MCP_SERVER.exists():
        raise SystemExit(f"Cannot find {MCP_SERVER}; run this from the agent-board repo.")

    if not args.skip_venv:
        setup_venv(find_base_python(args.python))

    spec = server_spec()

    # Decide targets.
    targets = list(dict.fromkeys(args.provider))  # de-dupe, keep order
    if args.all:
        targets = ALL_PROVIDERS
    elif not targets and not args.config_file:
        targets = [p for p in ALL_PROVIDERS if detected(p)]
        if not targets:
            print("No agent hosts detected. Use --all, --provider, or --config-file.")

    print("\nRegistering MCP server:")
    print(f"  command: {spec['command']}")
    print(f"  args:    {spec['args']}\n")

    for provider in targets:
        print(f"- {provider}:")
        register(provider, args.name, spec)

    if args.config_file:
        print(f"- {args.config_file}:")
        register_json(args.config_file.expanduser(), args.json_key, args.name, spec)

    print("\nDone. Restart the host app (or reload the MCP server) to pick it up.")


if __name__ == "__main__":
    main()
