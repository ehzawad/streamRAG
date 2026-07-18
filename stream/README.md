# Typed StreamRAG

`stream/` prepares evidence while the user types but answers only after Send.

Changed drafts pass through a bounded low-reasoning trigger. Retrieval may begin
after the latest delivered draft has been unchanged for 500 ms. At Send,
prepared evidence is reusable only when that draft exactly matches the committed
text. Mismatch, failure, or stale work takes the same committed-text fallback as
Naive. This is the main correctness boundary.

The app uses `shared/` for common data, answer, API, memory, metric, and UI
behavior. It never imports `naive/`, `frontend/`, or `comparison/` and runs
without them.

## Run

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/stream/qdrant \
RUNTIME_DB=./var/stream/runtime.sqlite3 \
METRICS_LOG=./var/stream/requests.jsonl \
uv run uvicorn stream.api:app --host 127.0.0.1 --port 8002
```

For fresh state, build the real local index once:

```bash
curl --fail --request POST http://127.0.0.1:8002/v1/data/sync
```

Open <http://127.0.0.1:8002>; its schema is at
<http://127.0.0.1:8002/docs>. `ALLOW_UNREVIEWED_DATASET` is development-only.

Stream reports the shared metrics plus controller, speculation, evidence lead
and reuse, stale-work, cancellation, and fallback diagnostics.

```bash
uv run pytest -q shared/tests stream/tests
```
