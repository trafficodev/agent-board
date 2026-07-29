---
name: project-structure
description: Use for Agent Board repo orientation before locating backend stores, MCP tools, frontend board UI, canvas sync, search, or tests.
---

# Agent Board Project Structure

Agent Board is a SQLite-backed project board service with a Python backend/MCP surface and a Vite React frontend. Boards, cards, edges, events, and canvas projections are owned by backend stores; the frontend reflects API state. Legacy JSON is imported transactionally once at API or MCP startup.

## Routing

- `backend/paths.py`: resolves `AGENT_BOARD_HOME` and storage roots.
- `backend/db.py`: authoritative SQLite schema, connection/transaction owner, hydration, and legacy JSON import.
- `backend/models.py`: shared board, card, edge, and event dataclasses.
- `backend/board_store.py`, `backend/card_store.py`, `backend/edge_store.py`: domain persistence owners over SQLite.
- `backend/card_bulk.py`, `backend/card_history.py`, `backend/card_diff.py`: card mutation/history helpers.
- `backend/search_logic.py`: canonical card search, filtering, sorting, and AI-style ranked search behavior.
- `backend/main.py`: FastAPI API surface for boards, cards, edges, search, and UI-serving routes.
- `backend/mcp_tools/`: MCP wrappers over the backend store/search behavior.
- `backend/canvas_sync.py`: canvas projection/sync ownership.
- `frontend/src/App.tsx`: main board UI.
- `frontend/src/api.ts`: frontend API client.
- `frontend/src/boardLogic.js`: board grouping, ordering, and drag/drop helpers.
- `frontend/src/boardSearch.js`: frontend search parsing/matching kept aligned with backend `search_logic.py`.
- `frontend/src/types.ts`: frontend board/card type contracts.
- `run.sh`: local app startup entrypoint.

## Tests

- Backend tests live in `backend/test_*.py` and run with `pytest`.
- Frontend logic tests live in `frontend/test-*.mjs` and run with npm scripts from `frontend/`.
- Frontend checks/build run inside `frontend` with `npm run lint` and `npm run build`.

## Conventions

- Treat SQLite through the backend stores as the source of truth for persisted board/card data.
- Keep frontend types aligned with backend models.
- Keep board/card/canvas capabilities exposed consistently through MCP, CLI, SDK/client, and frontend surfaces when a manual UI equivalent exists.
- Boards tied to a project use normalized `remote_url`; `board_store.ensure_project_board(remote_url)` is the idempotent get-or-create shared by every worktree/clone of the same repo.
- Do not stage unrelated changes; this repo is used by concurrent agents.

## Keeping This Skill Current

Update this skill when material ownership, persistence, API, frontend routing, search semantics, test commands, or run facts change. Keep it compact and current-state only.
