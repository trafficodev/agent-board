#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill previous instances
lsof -ti :8001 2>/dev/null | xargs kill 2>/dev/null || true
lsof -ti :5173 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5

# Backend
cd "$DIR/backend"
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8001 &
BACK_PID=$!

# Frontend
cd "$DIR/frontend"
npm run dev &
FRONT_PID=$!

echo "Agent Board running:"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
echo "  API docs: http://localhost:8001/docs"

wait
