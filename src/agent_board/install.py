"""Register / deregister Agent Board's MCP server with agent hosts.

Console script: `agent-board-install` (install | uninstall | list).

This assumes the package is installed (uv / pipx / pip), which provides the
`agent-board-mcp` command on PATH. It only does the part package managers
can't: writing the server entry into each host's config.

Two kinds of hosts are supported:
  * CLI hosts registered via their own `mcp add/remove` command:
      claude, codex, gemini
  * JSON-config hosts merged directly into their config file:
      claude-desktop, cursor, windsurf

Any host whose CLI/config isn't present is skipped, not failed. Install is
idempotent (existing registrations are refreshed).

Usage:
  agent-board-install                 # install into all detected hosts
  agent-board-install --list          # show detected hosts
  agent-board-install uninstall       # remove from all detected hosts
  agent-board-install --provider claude --provider cursor
  agent-board-install --all           # every known host
  agent-board-install --config-file ~/other/mcp.json   # any other host
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


# --- host config locations -------------------------------------------------

def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))


def _claude_desktop_config() -> Path:
    if IS_WINDOWS:
        return _appdata() / "Claude/claude_desktop_config.json"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


# CLI hosts: provider -> (add argv template, remove argv template).
# {name} and {cmd} are filled in per run.
CLI_HOSTS = {
    "claude": (
        ["claude", "mcp", "add", "-s", "user", "{name}", "--", "{cmd}"],
        ["claude", "mcp", "remove", "-s", "user", "{name}"],
    ),
    "codex": (
        ["codex", "mcp", "add", "{name}", "--", "{cmd}"],
        ["codex", "mcp", "remove", "{name}"],
    ),
    "gemini": (
        ["gemini", "mcp", "add", "-s", "user", "{name}", "{cmd}"],
        ["gemini", "mcp", "remove", "-s", "user", "{name}"],
    ),
}

# JSON-config hosts: provider -> (config path, key holding the server map).
JSON_HOSTS = {
    "claude-desktop": (_claude_desktop_config(), "mcpServers"),
    "cursor": (Path.home() / ".cursor/mcp.json", "mcpServers"),
    "windsurf": (Path.home() / ".codeium/windsurf/mcp_config.json", "mcpServers"),
}

ALL_PROVIDERS = [*CLI_HOSTS, *JSON_HOSTS]


def mcp_command() -> str:
    """Absolute path to the installed agent-board-mcp, or the bare name."""
    return shutil.which(MCP_COMMAND) or MCP_COMMAND


def _warn_stale_cli_wrapper() -> None:
    """Warn if a stale, hard-coded `agent-board` wrapper shadows PATH.

    Earlier installers generated a wrapper that exec'd a hard-coded
    `.../backend/cli.py`. The CLI now lives at `agent_board/cli.py` and ships as
    a console entry point, so that old wrapper points at a file that no longer
    exists and every `agent-board <cmd>` fails. Package managers usually replace
    it on upgrade, but one left earlier on PATH would still win. Detect it and
    print the fix; never touch a file a package manager may own.
    """
    found = shutil.which("agent-board")
    if not found:
        return
    try:
        text = Path(found).read_text(errors="ignore")
    except OSError:
        return
    # A working install is a console script that imports the package; the stale
    # wrapper hard-codes the pre-repackage cli.py path instead.
    if "agent_board" in text or "backend/cli.py" not in text:
        return
    print(f"! The 'agent-board' command on your PATH is a stale wrapper: {found}")
    print("  It points at the pre-packaging backend/cli.py path, which no longer exists.")
    print("  Replace it by reinstalling the package, e.g.:")
    print("    uv tool install --force .   (or: pipx install --force . / pip install --force-reinstall .)")
    print(f"  or just delete {found} and reinstall.\n")


def server_spec(cmd: str) -> dict:
    return {"command": cmd}


# --- detection -------------------------------------------------------------

def detected(provider: str) -> bool:
    if provider in CLI_HOSTS:
        return shutil.which(provider) is not None
    path, _ = JSON_HOSTS[provider]
    return path.exists()


# --- CLI-host registration -------------------------------------------------

def _fill(template: list[str], name: str, cmd: str) -> list[str]:
    return [part.format(name=name, cmd=cmd) for part in template]


def _cli_install(provider: str, name: str, cmd: str) -> str:
    add_t, remove_t = CLI_HOSTS[provider]
    subprocess.run(_fill(remove_t, name, cmd), capture_output=True)  # refresh, ignore errors
    result = subprocess.run(_fill(add_t, name, cmd), capture_output=True, text=True)
    if result.returncode != 0:
        return f"FAILED: {(result.stderr or result.stdout).strip()}"
    return "ok"


def _cli_uninstall(provider: str, name: str) -> str:
    _, remove_t = CLI_HOSTS[provider]
    result = subprocess.run(_fill(remove_t, name, ""), capture_output=True, text=True)
    return "ok" if result.returncode == 0 else "not registered"


# --- JSON-host registration ------------------------------------------------

def _json_install(path: Path, key: str, name: str, spec: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            config = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as e:
            return f"FAILED: {path} is not valid JSON ({e})"
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    else:
        config = {}
    servers = config.setdefault(key, {})
    action = "updated" if name in servers else "ok"
    servers[name] = spec
    path.write_text(json.dumps(config, indent=2) + "\n")
    return action


def _json_uninstall(path: Path, key: str, name: str) -> str:
    if not path.exists():
        return "not registered"
    try:
        config = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError as e:
        return f"FAILED: {path} is not valid JSON ({e})"
    servers = config.get(key, {})
    if name not in servers:
        return "not registered"
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    del servers[name]
    path.write_text(json.dumps(config, indent=2) + "\n")
    return "ok"


# --- orchestration ---------------------------------------------------------

def _apply(provider: str, action: str, name: str, cmd: str) -> str:
    if provider in CLI_HOSTS:
        return _cli_install(provider, name, cmd) if action == "install" else _cli_uninstall(provider, name)
    path, key = JSON_HOSTS[provider]
    if action == "install":
        return _json_install(path, key, name, server_spec(cmd))
    return _json_uninstall(path, key, name)


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-board-install",
        description="Register the Agent Board MCP server with agent hosts.")
    ap.add_argument("action", nargs="?", default="install", choices=["install", "uninstall"],
                    help="install (default) or uninstall.")
    ap.add_argument("--provider", action="append", choices=ALL_PROVIDERS, default=[],
                    help="Host to target (repeatable). Default: all detected hosts.")
    ap.add_argument("--all", action="store_true", help="Target every known host, even undetected ones.")
    ap.add_argument("--config-file", type=Path, default=None,
                    help="Also target an arbitrary MCP host config JSON.")
    ap.add_argument("--json-key", default="mcpServers", help="Top-level key for --config-file.")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Server name to register (default: {DEFAULT_NAME}).")
    ap.add_argument("--list", action="store_true", help="List known hosts and whether they were detected, then exit.")
    args = ap.parse_args()

    if args.list:
        print("Known MCP hosts:")
        for p in ALL_PROVIDERS:
            kind = "cli " if p in CLI_HOSTS else "json"
            print(f"  {'[detected]' if detected(p) else '[   -    ]'} ({kind}) {p}")
        return 0

    if args.action == "install":
        _warn_stale_cli_wrapper()

    cmd = mcp_command()
    if args.action == "install" and shutil.which(MCP_COMMAND) is None:
        print(f"! '{MCP_COMMAND}' is not on PATH. Install the package first, e.g.:")
        print("    uv tool install .    (or: pipx install . / pip install .)")
        print(f"  Continuing with command '{cmd}'.\n")

    targets = list(dict.fromkeys(args.provider))
    if args.all:
        targets = list(ALL_PROVIDERS)
    elif not targets and not args.config_file:
        targets = [p for p in ALL_PROVIDERS if detected(p)]
        if not targets:
            print("No agent hosts detected. Use --all, --provider, or --config-file.")

    failures = 0
    for provider in targets:
        if not args.all and not detected(provider):
            print(f"[{provider}] skipped -- not detected")
            continue
        status = _apply(provider, args.action, args.name, cmd)
        print(f"[{provider}] {args.action}: {status}")
        failures += status.startswith("FAILED")

    if args.config_file:
        path = args.config_file.expanduser()
        if args.action == "install":
            status = _json_install(path, args.json_key, args.name, server_spec(cmd))
        else:
            status = _json_uninstall(path, args.json_key, args.name)
        print(f"[{path}] {args.action}: {status}")
        failures += status.startswith("FAILED")

    print("\nDone. Restart the host app (or reload the MCP server) to pick it up.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
