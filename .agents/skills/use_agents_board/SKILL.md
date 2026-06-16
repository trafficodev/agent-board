---
name: use_agents_board
description: Use when an agent needs to inspect, query, import, sync, or modify Agent Board data. Covers the JSON database files under ~/.agent-board, grep/jq search patterns, REST endpoints, and the board/card/canvas-sync persistence model.
---

# Use Agent Board

Agent Board stores data as grepable JSON files, not SQL.

## Storage

Default root:

```bash
~/.agent-board
```

Override root:

```bash
AGENT_BOARD_HOME=/path/to/home
```

Files:

```bash
~/.agent-board/boards/<board_id>.json
~/.agent-board/boards/<board_id>_cards.json
~/.agent-board/events/<board_id>.jsonl
~/.agent-board/canvas_sync.json
```

Code owners:

```bash
/Users/ofekron/agent-board/backend/paths.py
/Users/ofekron/agent-board/backend/board_store.py
/Users/ofekron/agent-board/backend/card_store.py
/Users/ofekron/agent-board/backend/canvas_sync.py
/Users/ofekron/agent-board/backend/search_logic.py
```

## Grepability

Yes: board/card/event files are plain JSON/JSONL and can be searched with `rg`.

Use `rg` for quick text search:

```bash
rg "req-0007|latest event|git_commits|edited_files" ~/.agent-board
```

Use `jq` for structured card queries:

```bash
jq '.[] | select((.labels // [])[]? == "requirement") | {id,title,parent_id,body}' ~/.agent-board/boards/*_cards.json
```

Find cards mentioning a file or commit:

```bash
rg "frontend/src/App.tsx|abc1234" ~/.agent-board/boards/*_cards.json
```

Find a board by name:

```bash
jq -r 'select(.name | test("Better Claude"; "i")) | .id + " " + .name' ~/.agent-board/boards/*.json
```

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

`external_id` is used during import to resolve parent links. Check the current code before assuming it is persisted on the card.

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
