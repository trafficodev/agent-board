"""Register the Agent Board MCP server with one or more agent hosts.

Console script: `agent-board-install`.

This assumes the package is already installed (via uv / pipx / pip), which
provides the `agent-board-mcp` command on PATH. This tool only does the part
package managers can't: writing the server entry into each host's config.

MCP hosts (Claude Code, Claude Desktop, Cursor, Windsurf, ...) all register
stdio servers with the same shape:

    "mcpServers": {
        "agent-board": {"command": "agent-board-mcp"}
    }

so adding a new host is mostly a matter of knowing which JSON file to merge
that block into.

Usage:
  agent-board-install                 # install into all detected hosts
  agent-board-install --list          # show which hosts were detected
  agent-board-install --provider claude-code --provider cursor
  agent-board-install --all           # every known host, detected or not
  agent-board-install --config-file ~/some/other/mcp.json   # any other host
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

DEFAULT_NAME = "agent-board"
MCP_COMMAND = "agent-board-mcp"  # console script provided by this package

IS_WINDOWS = platform.system() == "Windows"


# --- Host registry ---------------------------------------------------------

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
    """The MCP server entry every host receives.

    Prefer the absolute path to the installed `agent-board-mcp` so hosts that
    don't inherit the user's PATH can still launch it; fall back to the bare
    command name.
    """
    return {"command": shutil.which(MCP_COMMAND) or MCP_COMMAND}


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
        register_json(Path.home() / ".claude.json", "mcpServers", name, spec)
        return
    subprocess.run([claude, "mcp", "remove", name, "-s", "user"], capture_output=True)
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
    ap = argparse.ArgumentParser(
        prog="agent-board-install",
        description="Register the Agent Board MCP server with agent hosts.")
    ap.add_argument("--provider", action="append", choices=ALL_PROVIDERS, default=[],
                    help="Host to install into (repeatable). Default: all detected hosts.")
    ap.add_argument("--all", action="store_true", help="Install into every known host, even undetected ones.")
    ap.add_argument("--config-file", type=Path, default=None,
                    help="Register into an arbitrary MCP host config JSON (escape hatch for other providers).")
    ap.add_argument("--json-key", default="mcpServers", help="Top-level key for --config-file (default: mcpServers).")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Server name to register (default: {DEFAULT_NAME}).")
    ap.add_argument("--list", action="store_true", help="List known hosts and whether they were detected, then exit.")
    args = ap.parse_args()

    if args.list:
        print("Known MCP hosts:")
        for p in ALL_PROVIDERS:
            print(f"  {'[detected]' if detected(p) else '[   -    ]'} {p}")
        return

    spec = server_spec()
    if shutil.which(MCP_COMMAND) is None:
        print(f"! '{MCP_COMMAND}' is not on PATH. Install the package first, e.g.:")
        print("    uv tool install .    (or: pipx install . / pip install .)")
        print(f"  Registering '{spec['command']}' anyway; hosts will need it on their PATH.\n")

    # Decide targets.
    targets = list(dict.fromkeys(args.provider))  # de-dupe, keep order
    if args.all:
        targets = ALL_PROVIDERS
    elif not targets and not args.config_file:
        targets = [p for p in ALL_PROVIDERS if detected(p)]
        if not targets:
            print("No agent hosts detected. Use --all, --provider, or --config-file.")

    print("Registering MCP server:")
    print(f"  command: {spec['command']}\n")

    for provider in targets:
        print(f"- {provider}:")
        register(provider, args.name, spec)

    if args.config_file:
        print(f"- {args.config_file}:")
        register_json(args.config_file.expanduser(), args.json_key, args.name, spec)

    print("\nDone. Restart the host app (or reload the MCP server) to pick it up.")


if __name__ == "__main__":
    main()
