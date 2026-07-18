# Naive RAG

`naive/` is the baseline product. It waits for Send, then retrieves with the
exact committed text. It has no draft endpoint, trigger model, speculative work,
or dependency on `stream/`, `frontend/`, or `comparison/`.

It uses `shared/` for common data, answer, API, memory, metric, and UI behavior,
so it remains a complete standalone app when the other product and consumer
directories are absent.

## Run

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/naive/qdrant \
RUNTIME_DB=./var/naive/runtime.sqlite3 \
METRICS_LOG=./var/naive/requests.jsonl \
uv run uvicorn naive.api:app --host 127.0.0.1 --port 8001
```

For fresh state, build the real local index once:

```bash
curl --fail --request POST http://127.0.0.1:8001/v1/data/sync
```

Open <http://127.0.0.1:8001>; its schema is at
<http://127.0.0.1:8001/docs>. `ALLOW_UNREVIEWED_DATASET` is development-only.

Naive reports the shared answer, citation, latency, reliability, usage,
retrieval, accounting, and observed-cost metrics. It never fabricates
Stream-only diagnostics.

```bash
uv run pytest -q shared/tests naive/tests
```
