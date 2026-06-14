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

# Backend
cd "$DIR/backend"
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
BACK_PID=$!

# Frontend
cd "$DIR/frontend"
BACKEND_URL="$BACKEND_URL" npm run dev -- --port "$FRONTEND_PORT" &
FRONT_PID=$!

echo "Agent Board running:"
echo "  Backend:  http://localhost:${BACKEND_PORT}"
echo "  Frontend: http://localhost:${FRONTEND_PORT}"
echo "  API docs: http://localhost:${BACKEND_PORT}/docs"

wait
