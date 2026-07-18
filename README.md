# Typed StreamRAG assessment

A typed-first, full-stack comparison of Naive RAG and a hybrid model-validated
StreamRAG adaptation over a bounded CRAG corpus. The implementation transfers the
paper's scheduling idea to text; it does not claim to reproduce the paper's
post-trained trigger or speech stack.

> **Dataset gate:** there is one canonical dataset at
> [`data/crag_eval`](data/crag_eval): 5 development questions, 10 unseen test
> questions, 250 complete CRAG documents, and exactly 1,000 local-Qdrant
> points. Its status is `candidate_pending_human_review`. Development checks are
> allowed; the unseen test split has not run and no final benchmark is claimed.
> Review [`data/crag_eval/REVIEW_SHEET.md`](data/crag_eval/REVIEW_SHEET.md) before
> changing the status to `approved_frozen`.

## What is compared

- **Naive RAG:** Send commits the complete query; only then does query planning,
  retrieval, and grounded answer generation begin.
- **Typed StreamRAG:** while the user types, the browser sends cumulative dirty
  text snapshots. At an eligible boundary with a meaningful completed prefix, the
  deterministic scheduler may launch one raw retrieval concurrently with a bounded
  low-reasoning model controller. The model validates, refines, or rejects that
  candidate. Raw results remain provisional: the complete-input commit gate must
  reuse and revalidate, finish compatible in-flight work, or replace them. Only
  commit-validated evidence can reach the answer, and final answer generation never
  starts before Send.

Both paths share the corpus, chunker, embeddings, search policy, top-k, grounded
answer agent, prompt, memory policy, and scorer. Formal runs use separate backend
processes, local stores, cache namespaces, and sessions so neither path can warm
the other.

This is a text experiment. Send is the endpoint, partial typed words replace
partial ASR transcripts, and the benchmark excludes ASR, speech endpoint
detection, TTS, and trailing-silence gains.

## Stack and locked roles

- Python 3.14, FastAPI, buffered Server-Sent Events, and SQLite memory
- PydanticAI over OpenAI Responses, with strict `search_local_crag` function
  calling
- `gpt-5.6-sol`: medium reasoning for grounded answers; low for the latency-
  sensitive trigger and summary roles
- `text-embedding-3-large`, 3,072 dimensions
- embedded local Qdrant persistence; no Qdrant account or API key
- React/Vite one-page client with Naive, Stream, and Compare modes

The API and OpenAI clients are asynchronous. Embedded Qdrant's synchronous client
runs behind one dedicated worker thread, so vector work does not block the event
loop. The frontend keeps at most one snapshot request in flight and one replaceable
latest snapshot; Send/cancel stays responsive. `answer.ready` is the user-visible
terminal boundary after grounded generation, so the UI stops loading before
bounded post-answer persistence. The later `answer.completed` event carries final
accounting and maintenance status. Follow-up context reads use the same session
lease as persistence, so they cannot observe a half-saved turn; if optional
compaction fails, the completed turn is durably saved uncompressed. This is a
bounded local-assessment design, not a claim of unlimited production concurrency.

## Run locally

```bash
cp .env.example .env
# Put a real OPENAI_API_KEY in .env.
make setup
ALLOW_UNREVIEWED_DATASET=1 make dev
```

Open <http://localhost:5173>; the API schema is at
<http://localhost:8000/docs>. With the review-gated candidate, the override is
required for development only and must never be used to label a run final.

Build the real local index after the API starts:

```bash
curl --fail --request POST http://localhost:8000/v1/data/sync
```

Docker is an equivalent local path:

```bash
ALLOW_UNREVIEWED_DATASET=1 make docker-up
```

Both direct and Docker workflows publish on loopback only. The assessment has no
authentication, authorization, rate limiting, or multi-user isolation; do not
expose it to a LAN/public interface. CORS is not an authentication boundary. See
[`docs/SECURITY.md`](docs/SECURITY.md) before changing any bind address.

## Data and index behavior

The repository includes the checksum-bound, compressed 250-document corpus.
Normal reproduction does not download the 705 MiB upstream CRAG release. Source
download and deterministic regeneration remain available as an optional provenance
audit.

`POST /v1/data/sync` hashes the desired chunks and their embedding configuration,
embeds only missing or changed chunks, removes stale points, and advances a durable
index version. That version is part of bounded query/result cache keys. Local
Qdrant stores generated state under `data/qdrant/`; it is not committed.

A clean real-API build on the acceptance machine indexed 1,000/1,000 points with
`text-embedding-3-large`: 366,142 embedding tokens, 40.94 s, and $0.04759846 at
the recorded embedding price. The corpus pages themselves are complete after
HTML/script/style cleaning; no selected page is character- or token-truncated.

## Verification and measured development evidence

```bash
make verify-data
make check
```

To reproduce the real, isolated **development-only** comparison before human
approval, first validate and build two separate candidate-data indexes:

```bash
make benchmark-dev-services-check
make benchmark-dev-services-sync
```

The check is read-only. Sync uses the configured real embedding API twice—once
per isolated service—and never runs a question. Then keep the services in terminal
A and run/score only `dev_queries.jsonl` from terminal B:

```bash
# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

Development mode requires the exact `candidate_pending_human_review` dataset,
sets the override only in its child processes, and labels its artifact permanently
non-reportable. The smoke runner refuses any filename other than
`dev_queries.jsonl`; the ordinary final runner still requires an approved redacted
inference bundle and approved service status.

The retained real development comparison used exactly 5 checksum-bound development
questions × 2 isolated paths, deterministic 70-WPM typing, and one measured pass.
Both paths completed all five with 100% automatic expected-answer, evidence support,
supporting-citation, and false-premise correctness. Stream won TTFT on 4/5 pairs;
the median paired Stream-minus-Naive delta was -628.907 ms (-12.0846%) for TTFT
and -702.294 ms for total time. The paired p95 TTFT delta was +275.880 ms because
one tail case was slower. Negative latency deltas favor Stream.

This small live run does not prove causality or a general speedup, and it showed no
accuracy gain because both paths were already perfect on the automatic checks.
Stream's fallback, compatible post-commit overlap, and speculative-reuse rates were
40%, 40%, and 20%. Accepted evidence still had zero pre-Send lead on every case.
Stream used 20 model API calls, 23 controller calls, and 12 retrievals versus
Naive's 7, 5, and 5; neither path issued a dynamic function-tool call. Observed
costs were lower bounds—at least $0.11416807 for Stream and $0.06137975 for
Naive—because cancelled, failed, or timed-out calls did not all return provider
usage. Complete accounting covered 0/5 Stream and 2/5 Naive outputs, so no paired
cost delta is available. The artifact is `reportable: false`; the unseen test
split remains sealed.

The retained development run took 180.866 s. A clean-clone acceptance run at the
published commit completed setup, verification, all tests/builds, two fresh real
1,000-point indexes, service startup, and all 10 dev path runs plus scoring in
about 4 minutes 46 seconds on the acceptance machine. The workflow remains
designed to stay below 15–20 minutes on a normal connection and responsive OpenAI
service; the 45 s per-case deadline, first-time package downloads, and provider
variance are the main sources of variation.

See [`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md) for the non-final
development evidence and frozen-test protocol,
[`docs/REAL_USER_VERIFICATION.md`](docs/REAL_USER_VERIFICATION.md) for browser
acceptance, [`docs/DATASET.md`](docs/DATASET.md) for the approval boundary, and
[`docs/SECURITY.md`](docs/SECURITY.md) for the localhost-only trust boundary.
CRAG/data licensing is recorded in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
