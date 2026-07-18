# External comparison application

`comparison/` is a consumer of two independently deployed services. It owns the
React/Vite A/B UI, two-service provisioning, typed replay, result manifests,
offline scoring, and report rendering. The UI, replay runner, and scorer import no
`shared`, `naive`, or `stream` package; they communicate only through HTTP/JSON/SSE
and validate each service's advertised role, capability, contract version, and
fingerprints. The local provisioner is an explicit deployment-only exception: it
launches the two repository entrypoints and copies only stopped, quiescent index
state. It never executes or scores a benchmark question.

Deleting this directory does not affect either single-path full-stack app.
Deleting one implementation does not change the other; only A/B comparison then
becomes unavailable.

## UI

With the Naive and Stream APIs on ports 8001 and 8002:

```bash
make setup-comparison
cd comparison/frontend
VITE_NAIVE_API_URL=http://127.0.0.1:8001 \
VITE_STREAM_API_URL=http://127.0.0.1:8002 \
npm run dev
```

Open <http://127.0.0.1:5173>. Stream snapshots go only to the Stream service.
Send commits the same final text to each selected service; Compare mode starts the
two commits independently and never asks one backend to impersonate the other.

## Isolated benchmark services

The development-only sequence is:

```bash
uv run python -m comparison.services check --development-candidate \
  --dataset-dir data/crag_eval \
  --state-root comparison/benchmark/results/dev-services

uv run python -m comparison.services sync --development-candidate \
  --dataset-dir data/crag_eval \
  --state-root comparison/benchmark/results/dev-services

uv run python -m comparison.services serve --development-candidate \
  --dataset-dir data/crag_eval \
  --state-root comparison/benchmark/results/dev-services
```

`sync` embeds one temporary seed index with the real OpenAI API, stops the seed,
checks that its SQLite state is quiescent, and clones Qdrant plus matching index
metadata into sibling `naive/` and `stream/` state directories. Once started, the
services have separate Qdrant, SQLite, metrics, session, and cache state.

The benchmark runner uses the same question order and commit text for both paths.
The scorer receives gold only after prediction generation. Development mode reads
only `dev_queries.jsonl`; the final runner requires an approved, redacted inference
bundle and all 10 sealed questions.

## Metric ownership

Common comparison metrics are TTFT/total time, completion/failure/timeout,
automatic answer/alias and false-premise proxies, evidence support, exact citation
validity, calls/tokens, accounting coverage, and observed cost. Stream-specific
controller/speculation/reuse/cancellation/fallback diagnostics remain in the
Stream record and are summarized separately.

```bash
uv run pytest -q comparison/tests
cd comparison/frontend && npm test && npm run build
```
