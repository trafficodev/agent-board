---
name: use_agents_board
description: Use when an agent needs to inspect, query, import, sync, or modify Agent Board data through the SQLite-backed CLI, API, or MCP surfaces.
---

# Use Agent Board

Agent Board stores durable data in SQLite. Use the CLI, REST API, or MCP tools for writes; do not edit the database directly.

## Storage

Default database:

```bash
~/.agent-board/agent-board.sqlite3
```

Override root:

```bash
AGENT_BOARD_HOME=/path/to/home
```

Code owners:

```bash
/Users/ofekron/agent-board/backend/paths.py
/Users/ofekron/agent-board/backend/db.py
/Users/ofekron/agent-board/backend/board_store.py
/Users/ofekron/agent-board/backend/card_store.py
/Users/ofekron/agent-board/backend/edge_store.py
/Users/ofekron/agent-board/backend/canvas_sync.py
/Users/ofekron/agent-board/backend/search_logic.py
```

Legacy JSON is imported transactionally at startup and is not the live source of truth.

## CLI and MCP

Run the CLI with the backend virtual environment:

```bash
AB_PY=/Users/ofekron/agent-board/backend/.venv/bin/python
AB_CLI=/Users/ofekron/agent-board/backend/cli.py
"$AB_PY" "$AB_CLI" --help
```

Resolve the project board from its normalized Git remote instead of hard-coding a board ID:

```bash
REMOTE_URL=$(git remote get-url origin)
"$AB_PY" "$AB_CLI" ensure_project_board "$REMOTE_URL"
```

Common operations:

```bash
"$AB_PY" "$AB_CLI" list_cards BOARD_ID
"$AB_PY" "$AB_CLI" search_cards BOARD_ID --query 'label:requirement'
"$AB_PY" "$AB_CLI" get_card BOARD_ID CARD_ID
"$AB_PY" "$AB_CLI" bulk_cards BOARD_ID '[{"op":"create",...}]'
"$AB_PY" "$AB_CLI" move_card BOARD_ID CARD_ID COLUMN_ID
```

The MCP tools expose the same store behavior. Prefer `bulk_cards` for sparse atomic updates; avoid replacing whole cards when only one field changes.

## API Surface

Backend app:

```bash
/Users/ofekron/agent-board/backend/main.py
```

Useful endpoints:

```text
GET  /api/boards
POST /api/boards
GET  /api/boards/{board_id}
GET  /api/boards/{board_id}/cards
GET  /api/boards/{board_id}/cards/search?query=...
POST /api/boards/{board_id}/cards/bulk
GET  /api/boards/{board_id}/cards/{card_id}
PATCH /api/boards/{board_id}/cards/{card_id}
POST /api/boards/{board_id}/cards/{card_id}/move
POST /api/projects/ensure-board
POST /api/import/board
POST /api/boards/{board_id}/canvas-sync/enable
POST /api/boards/{board_id}/canvas-sync/sync
```

Import payload shape:

```json
{
  "name": "Board name",
  "description": "",
  "columns": ["Products", "Features", "Requirements", "Bug Reports"],
  "cards": [
    {
      "external_id": "req-0001",
      "title": "Requirement title",
      "body": "Searchable text",
      "column": "Requirements",
      "parent_external_id": "feat-0001",
      "priority": "medium",
      "labels": ["requirement"]
    }
  ]
}
```

`external_id` is persisted and is used during import to resolve parent links.

## Search Behavior

Search is implemented in `backend/search_logic.py` and mirrored in `frontend/src/boardSearch.js`.

Supported forms include:

```text
plain words
title:...
body:...
label:...
priority:...
file:...
commit:...
has:file
has:commit
contains:...
```

`contains:` uses backend search and can run `rg` through edited files listed on cards.

When adding searchable generated data, put it in the card body at minimum. If Agent Board has a structured metadata field in the current code, also store it there and ensure both backend and frontend search include it.

## Requirement Analysis Boards

Requirement-analysis imports should preserve hierarchy:

```text
Products -> Features -> Requirements -> Bug Reports
```

Requirement cards represent requirement threads. For these, include:

```text
thread_id
current requirement text
projects / project_cwds
edited_files
git_commits
event history with seq, ts, relation, session, text
```

This makes thread evolution visible and searchable in Agent Board.
