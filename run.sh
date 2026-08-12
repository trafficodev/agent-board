#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8011}"
FRONTEND_PORT="${FRONTEND_PORT:-5177}"
BACKEND_URL="${BACKEND_URL:-http://localhost:${BACKEND_PORT}}"

# Kill previous instances on the configured ports
lsof -ti :"$BACKEND_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti :"$FRONTEND_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5

cd "$DIR"

# Backend (FastAPI). Prefer `uv run`, which creates/syncs the environment from
# pyproject.toml automatically; fall back to the active Python (needs
# `pip install -e .` to have been run).
if command -v uv >/dev/null 2>&1; then
    uv run uvicorn agent_board.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
else
    python3 -m uvicorn agent_board.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
fi

# Frontend
cd "$DIR/frontend"
BACKEND_URL="$BACKEND_URL" npm run dev -- --port "$FRONTEND_PORT" &

echo "Agent Board running:"
echo "  Backend:  http://localhost:${BACKEND_PORT}"
echo "  Frontend: http://localhost:${FRONTEND_PORT}"
echo "  API docs: http://localhost:${BACKEND_PORT}/docs"

wait
