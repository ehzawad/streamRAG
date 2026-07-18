# Naive RAG

`naive/` owns Path A. It starts retrieval only after Send and searches with the
exact immutable committed text. It has no pre-Send snapshot endpoint, model
controller, speculative retrieval, or dependency on Stream/comparison code.

The package imports `shared/` for the corpus/index, grounded answer agent, memory,
HTTP lifecycle, metrics envelope, and capability-driven single-path UI. The app is
therefore independently runnable as a full-stack Naive experience when
`stream/` and `comparison/` are absent.

## Run

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/naive/qdrant \
RUNTIME_DB=./var/naive/runtime.sqlite3 \
METRICS_LOG=./var/naive/requests.jsonl \
uv run uvicorn naive.api:app --host 127.0.0.1 --port 8001
```

For a fresh state directory, build the real local index once:

```bash
curl --fail --request POST http://127.0.0.1:8001/v1/data/sync
```

Open <http://127.0.0.1:8001> for the Naive UI and
<http://127.0.0.1:8001/docs> for its API schema. The candidate override is for
development only; omit it after the dataset becomes `approved_frozen`.

## Metrics and tests

Naive records common answer/citation, TTFT/total, completion/failure, model usage,
retrieval, accounting-coverage, and observed-cost fields. Its retrieval record is
always committed-text work: one normal retrieval, no controller, no speculative
reuse, and no Stream-only diagnostic invented to fill the schema.

```bash
uv run pytest -q shared/tests naive/tests
```
