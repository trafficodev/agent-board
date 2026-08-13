# Agent Board

A kanban board for AI agents, with an **MCP server** and a **CLI**, backed by a
FastAPI + SQLite service and a React UI. Agents create/move/search cards over
MCP; humans watch and steer from the browser.

## Install

Agent Board is a standard Python package. Use any of these — each puts the
`agent-board`, `agent-board-mcp`, and `agent-board-install` commands on your
PATH, cross-platform:

```bash
# uv (recommended — also fetches a suitable Python)
uv tool install git+https://github.com/ofekron/agent-board

# or pipx
pipx install git+https://github.com/ofekron/agent-board

# or pip (once published to PyPI)
pip install agent-board
```

From a local checkout, replace the URL with `.` (e.g. `uv tool install .`).
Requires Python ≥ 3.10.

## Register the MCP server with your agent host

```bash
agent-board-install            # all detected hosts
agent-board-install --list     # see which hosts were detected
agent-board-install --provider claude --provider cursor
agent-board-install uninstall  # remove
```

Supported hosts: **claude**, **codex**, **gemini** (via each CLI's `mcp add`),
and **claude-desktop**, **cursor**, **windsurf** (via their JSON config), plus
`--config-file <path>` for any other MCP host. It registers the portable
`agent-board-mcp` command, so no absolute paths leak into configs.

Manual registration — the entry is just:

```json
{ "mcpServers": { "agent-board": { "command": "agent-board-mcp" } } }
```

## CLI

`agent-board` mirrors the MCP tools and prints JSON:

```bash
agent-board list_boards
agent-board -h                 # all subcommands
```

Data lives in `~/.agent-board`. Point at a specific backend with
`AGENT_BOARD_URL` (defaults to `http://localhost:8001`, auto-starting one if
none is running).

## Run the web UI (development)

The React UI is a separate dev tool (needs Node):

```bash
./run.sh                       # backend on :8011, frontend on :5177
```

`run.sh` uses `uv run` for the backend, so no manual venv is needed.

## Tests

```bash
uv run --with pytest pytest
```
