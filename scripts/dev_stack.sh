#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  jobs -pr | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

dev_state_root="${APP_STATE_ROOT:-var/dev-services}"
mkdir -p "$dev_state_root/naive" "$dev_state_root/stream"

ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH="$dev_state_root/naive/qdrant" \
RUNTIME_DB="$dev_state_root/naive/runtime.sqlite3" \
METRICS_LOG="$dev_state_root/naive/requests.jsonl" \
uv run uvicorn naive.api:app --reload --host 127.0.0.1 --port 8001 &

ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH="$dev_state_root/stream/qdrant" \
RUNTIME_DB="$dev_state_root/stream/runtime.sqlite3" \
METRICS_LOG="$dev_state_root/stream/requests.jsonl" \
uv run uvicorn stream.api:app --reload --host 127.0.0.1 --port 8002 &

(cd frontend && npm run dev -- --host 127.0.0.1) &
wait
