# Typed StreamRAG assessment

A typed-first, full-stack comparison of Naive RAG and a model-triggered
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
  text snapshots. A bounded low-reasoning controller may start retrieval before
  Send. At commit, a freshness/relevance gate reuses, revalidates, overlaps, or
  replaces speculative work. Only accepted evidence can reach the answer.

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
latest snapshot; Send/cancel stays responsive. This is a bounded local-assessment
design, not a claim of unlimited production concurrency.

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

The real development comparison used 5 questions × 2 isolated paths, deterministic
70-WPM typing, and one measured pass. Both paths scored 100% expected-answer and
100% supporting-citation correctness. Across all five pairs, StreamRAG won TTFT
on 60%; the median paired Stream-minus-Naive delta was -336 ms (-4.93%) for TTFT
and -358 ms for total time. Negative latency deltas favor StreamRAG.

That aggregate hides the important mechanism: on the three preregistered
early-stabilizing development questions, StreamRAG won TTFT on all three with a
median paired delta of -2,055 ms (-36.88%) and no accuracy loss. It lost on the
single late-stabilizing and single revision/ambiguity questions. It also made more
model calls and had a higher observed lower-bound cost: at least $0.1018 versus
$0.0668 across five outputs. This matches the paper's core expectation: latency
can improve when useful retrieval starts early while correctness is preserved;
StreamRAG is not inherently more accurate or cheaper than the same RAG pipeline.

The development run took 182.4 s. The normal full reproduction is deliberately
assignment-sized: dependency setup, checksum verification, a roughly 41 s clean
index, and—after approval—20 path runs. It is designed to fit about 15–20 minutes
on a normal connection and responsive OpenAI service; the benchmark itself has a
45 s per-case deadline, so provider delays or first-time package downloads are the
main sources of variation.

See [`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md) for the non-final
development evidence and frozen-test protocol,
[`docs/REAL_USER_VERIFICATION.md`](docs/REAL_USER_VERIFICATION.md) for browser
acceptance, [`docs/DATASET.md`](docs/DATASET.md) for the approval boundary, and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for CRAG/data licensing.
