# Typed StreamRAG

`stream/` owns Path B. It accepts changed cumulative text-box snapshots before
Send, runs the bounded low-reasoning trigger while intent evolves, and starts
exact-draft retrieval after the most recently delivered draft remains unchanged
for 500 ms. Evidence stays provisional and no answer can start before Send.

At commit, completed evidence is reusable only when its recorded source text is
literally equal to the committed text. The path may await an already-running
literal-exact retrieval; edits, mismatch, failure, or stale work take the same
committed-text fallback used by Naive.

The package imports `shared/` for the corpus/index, grounded answer agent, memory,
HTTP lifecycle, metrics envelope, and capability-driven single-path UI. It never
imports Naive or comparison code and remains an independently runnable full-stack
StreamRAG experience when those directories are absent.

## Run

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/stream/qdrant \
RUNTIME_DB=./var/stream/runtime.sqlite3 \
METRICS_LOG=./var/stream/requests.jsonl \
uv run uvicorn stream.api:app --host 127.0.0.1 --port 8002
```

For a fresh state directory, build the real local index once:

```bash
curl --fail --request POST http://127.0.0.1:8002/v1/data/sync
```

Open <http://127.0.0.1:8002> for the Stream UI and
<http://127.0.0.1:8002/docs> for its API schema. The candidate override is for
development only; omit it after the dataset becomes `approved_frozen`.

## Metrics and tests

Stream emits all common answer/citation, latency, reliability, usage, retrieval,
accounting, and cost fields. It additionally owns controller attempts/timeouts,
speculative and settled-draft retrievals, evidence lead/reuse/revalidation,
stale discard, cancellation, and commit-fallback diagnostics.

```bash
uv run pytest -q shared/tests stream/tests
```
