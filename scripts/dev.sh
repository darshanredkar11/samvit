#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

cleanup() {
  echo ""
  echo "Shutting down..."
  [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "==> Starting backend (auto-reload on port 8765)..."
source .venv/bin/activate
export SAMVIT_ADMIN_DEV_MODE=true
samvit serve --reload --host 127.0.0.1 --port 8765 &
BACKEND_PID=$!

echo "==> Starting admin UI (Vite HMR on port 5173)..."
cd admin-ui
npm run dev &
FRONTEND_PID=$!

echo ""
echo "  Backend API : http://127.0.0.1:8765"
echo "  Admin UI    : http://127.0.0.1:5173/admin"
echo "  (Vite proxies /v1/* to the backend)"
echo ""
echo "Press Ctrl+C to stop both."
wait
