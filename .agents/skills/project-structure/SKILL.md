---
name: project-structure
description: Use for the agent-board project, a FastAPI backend plus Vite React agents board app; check this before locating UI components, backend APIs, persisted board/card state, run commands, logs, or project conventions.
---

# Project Structure

agent-board is a local board application for coordinating agents. The frontend is a Vite React/TypeScript app under `frontend/src`; the backend is a Python FastAPI service under `backend` that owns board/card persistence and integration endpoints.

## Routing

- UI shell and styling: `frontend/src/App.tsx`, `frontend/src/App.css`, `frontend/src/index.css`
- Board UI: `frontend/src/components/BoardView.tsx`
- Card UI: `frontend/src/components/CardComponent.tsx`
- Frontend API/types: `frontend/src/api.ts`, `frontend/src/types.ts`
- Backend HTTP app: `backend/main.py`
- Durable board/card state: `backend/board_store.py`, `backend/card_store.py`, `backend/models.py`
- Canvas/MCP/client integrations: `backend/canvas_sync.py`, `backend/client.py`, `backend/mcp_server.py` (generic stdio runner, launched with a group name), `backend/mcp_tools/` (boards/canvas/cards/sessions tool groups — one MCP server per group)
- State/path helpers: `backend/paths.py`
- Run entrypoint: `run.sh`

## Commands

- Frontend checks/build: run inside `frontend` with `npm run lint` and `npm run build`
- Backend dependencies: `backend/requirements.txt`
- Local app startup: `./run.sh`

## Conventions

- Treat backend stores as the source of truth for persisted board/card data.
- Keep frontend types aligned with backend models.
- Every Agent Board capability must be exposed consistently through MCP, CLI, SDK/client, and frontend surfaces. Do not add a board/card/canvas capability to only one consumer path.
- Do not stage unrelated changes; this repo is used by concurrent agents.

## Keeping This Skill Current

Agents must update this skill when material project facts change: major directories, run commands, API ownership, persistence locations, integration contracts, or core conventions. Keep it compact and current-state only.
