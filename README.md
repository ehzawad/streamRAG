# Typed StreamRAG assessment

A typed-input, full-stack comparison of Naive RAG and StreamRAG over a fixed
local CRAG corpus. The implementation transfers the paper's scheduling idea to
text; it does not claim to reproduce its speech stack or post-trained trigger.

> **Dataset gate:** [`data/crag_eval`](data/crag_eval) is the only dataset. It
> contains 5 development questions, 10 sealed test questions, 250 complete
> documents, and exactly 1,000 chunks/Qdrant points. Its current status is
> `candidate_pending_human_review`. Development checks are allowed; the final
> unseen benchmark remains blocked until a reviewer changes the status and
> checksums to `approved_frozen`.

## Architecture

The repository has four explicit ownership boundaries:

```text
shared/       reusable API lifecycle, agent, corpus/index, memory, and contracts
naive/        Path A service; imports shared, never Stream or comparison
stream/       Path B service; imports shared, never Naive or comparison
comparison/   external HTTP/JSON client, UI, benchmark runner, and scorer
```

- `naive.api:app` is an independently runnable FastAPI service and same-origin
  single-path UI on port 8001.
- `stream.api:app` is an independently runnable FastAPI service and same-origin
  single-path UI on port 8002.
- Neither implementation imports or calls the other.
- `comparison/` imports no application package. It treats both services as
  versioned HTTP/JSON/SSE endpoints.
- Deleting `comparison/` leaves both single-path full-stack apps runnable and
  testable. Deleting either implementation leaves the other implementation and
  `shared/` as a complete single-path app.
- `shared/` contains mechanisms that are genuinely identical: dataset and index
  integrity, embeddings/search, grounded answer generation, memory, event
  lifecycle, and the versioned telemetry envelope. It contains no path selector
  and no comparison orchestration.

Formal A/B runs use separate processes, Qdrant directories, SQLite databases,
metrics logs, sessions, and cache namespaces. Provisioning embeds one temporary
seed index, stops that process, verifies quiescence, then copies its Qdrant data
and matching SQLite index metadata into two isolated stores. The running services
never share mutable state. Each service can also build its own index through
`POST /v1/data/sync`.

See [`shared/README.md`](shared/README.md), [`naive/README.md`](naive/README.md),
[`stream/README.md`](stream/README.md), and
[`comparison/README.md`](comparison/README.md) for the individual boundaries.

## Compared behavior

Naive RAG begins exact committed-text retrieval only after Send. Typed StreamRAG
accepts changed cumulative drafts before Send, uses a bounded low-reasoning model
trigger while the text evolves, and may start exact retrieval after the latest
delivered draft remains unchanged for 500 ms. Speculative evidence stays private.
Only Send can commit evidence and start the grounded answer.

At Send, Stream may reuse completed evidence only when its source text literally
equals the commit, or await an already-running literal-exact retrieval. A changed
or failed candidate takes the same bounded committed-text fallback as Naive. Both
paths therefore share the answer model, prompt, corpus, chunker, embeddings,
search policy, memory, and scorer; Stream alone owns draft analysis, triggering,
speculation, cancellation, reuse, and fallback diagnostics.

The stack uses Python 3.14, FastAPI/SSE, PydanticAI over OpenAI Responses,
`gpt-5.6-sol` (medium answer reasoning; low trigger/summary reasoning),
`text-embedding-3-large` at 3,072 dimensions, SQLite, embedded Qdrant, and a
React/Vite comparison client. Live indexing and answer verification use a real
OpenAI key and real local vector search. No live-path dependency is mocked.

## Run each implementation independently

```bash
cp .env.example .env
# Set a real OPENAI_API_KEY in .env.
make setup
```

The candidate override is development-only. Use distinct state paths when both
services run on one workstation.

Naive only:

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/naive/qdrant \
RUNTIME_DB=./var/naive/runtime.sqlite3 \
METRICS_LOG=./var/naive/requests.jsonl \
uv run uvicorn naive.api:app --host 127.0.0.1 --port 8001

# In another terminal, once per fresh state directory:
curl --fail --request POST http://127.0.0.1:8001/v1/data/sync
```

Stream only:

```bash
ALLOW_UNREVIEWED_DATASET=1 \
QDRANT_PATH=./var/stream/qdrant \
RUNTIME_DB=./var/stream/runtime.sqlite3 \
METRICS_LOG=./var/stream/requests.jsonl \
uv run uvicorn stream.api:app --host 127.0.0.1 --port 8002

# In another terminal, once per fresh state directory:
curl --fail --request POST http://127.0.0.1:8002/v1/data/sync
```

Their single-path UIs are available at <http://127.0.0.1:8001> and
<http://127.0.0.1:8002>; schemas are at `/docs` on the same ports. The shareable
UI shell reads each service's capabilities: Naive never exposes or sends
snapshots, while Stream enables its pre-Send snapshot flow.

## Run the comparison application

The normal development launcher starts both isolated APIs and the external
comparison UI:

```bash
make setup-comparison
make verify-data
make benchmark-dev-services-sync
make dev-comparison-stack
```

Open <http://127.0.0.1:5173>. The client sends Stream snapshots only to port
8002 and, in Compare mode, commits the same final text concurrently to ports 8001
and 8002. It never asks one backend to execute the other path. The one-time sync
uses the real embedding API and prepares the exact isolated state directories used
by the launcher; a fresh checkout is not ready until that step completes.

For an explicit two-service development benchmark:

```bash
make verify-data
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

`benchmark-dev-services-sync` makes one real embedding build and clones only the
stopped, quiescent seed state. The smoke runner uses the 5 development questions
and remains permanently non-reportable. The final runner uses all 10 test
questions only after dataset approval and a redacted inference bundle is created.

## Metrics

Each service emits its own telemetry and identifies its implementation and metric
contract version. The external comparison retains the full records, then compares
only metrics with the same meaning on both paths:

- submit-to-first-token and total response time;
- completion, failure, and timeout counts;
- expected-answer/alias and false-premise proxies;
- evidence support and exact citation validity;
- model/retrieval calls, token usage, accounting coverage, and observed cost.

Stream-only diagnostics—controller decisions, speculative retrievals, evidence
lead, reuse, stale discards, cancellation, and commit fallback—are reported as
Stream behavior, never synthesized for Naive or used as a false shared metric.
See [`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md).

## Verification and limits

```bash
make verify-data
make check
```

The committed corpus makes normal reproduction independent of the 705 MiB
upstream CRAG download. Embedded Qdrant needs no account or API key. The services
are asynchronous at the HTTP/OpenAI layer; synchronous local-Qdrant work runs off
the event loop. This is a bounded local assessment, not a production multi-user
service.

All ports bind to loopback. There is no authentication, authorization, rate
limiting, or tenant isolation, so do not expose either API or the UI to a LAN or
public interface. See [`docs/SECURITY.md`](docs/SECURITY.md),
[`docs/DATASET.md`](docs/DATASET.md), and
[`docs/REAL_USER_VERIFICATION.md`](docs/REAL_USER_VERIFICATION.md).
