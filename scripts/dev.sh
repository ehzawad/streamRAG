#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  jobs -pr | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

uv run uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000 &
(cd frontend && npm run dev -- --host 127.0.0.1) &
wait
