# Agent Board

A kanban board for AI agents, with an **MCP server** and a **CLI**, backed by a
FastAPI service and a React UI. Agents create/move/search cards over MCP; humans
watch and steer from the browser.

## Install

Agent Board is a standard Python package. Use any of these — each puts the
`agent-board`, `agent-board-mcp`, and `agent-board-install` commands on your PATH,
cross-platform:

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

After installing, wire the MCP server into your host(s):

```bash
agent-board-install            # all detected hosts
agent-board-install --list     # see which hosts were detected
agent-board-install --provider claude-code --provider cursor
```

Supported out of the box: **Claude Code**, **Claude Desktop**, **Cursor**,
**Windsurf**, plus `--config-file <path>` for any other MCP host. It registers
the portable `agent-board-mcp` command, so no absolute paths leak into configs.

Or register manually — the entry is just:

```json
{ "mcpServers": { "agent-board": { "command": "agent-board-mcp" } } }
```

## CLI

`agent-board` mirrors the MCP tools 1:1 and prints JSON:

```bash
agent-board list_boards
agent-board create_board "My Board" --columns Backlog "In Progress" Done
agent-board create_card <board_id> "Fix the bug" <column_id> --priority high
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
