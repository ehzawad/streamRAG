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

- **Naive RAG:** Send commits the complete query; only then does retrieval with
  that exact immutable text and grounded answer generation begin.
- **Typed StreamRAG:** while the user types, the browser samples every 400 ms but
  sends only changed cumulative drafts. Evolving prefixes use the bounded low-
  reasoning model trigger. After any delivered draft remains unchanged for the
  locked 500 ms server interval, the server may retrieve with that exact full
  draft. Raw results remain provisional. HTTP Send acceptance is immediate and
  backgrounded. The answer path cancels an unfinished model decision or mismatched
  speculation; it may await only an in-flight retrieval whose source text literally
  equals the commit, then uses the bounded exact committed-text fallback if that
  retrieval fails. Any text change takes that fallback. Grounded answer generation
  never starts before Send.

Both paths share the corpus, chunker, embeddings, search policy, top-k, exact
committed-text fallback, grounded answer agent, prompt, memory policy, and scorer.
Stream adds only the pre-Send trigger and speculative work. Formal runs use
separate backend processes, local stores, cache namespaces, and sessions so neither
path can warm the other.

The answer agent's privileged instructions are static. Question text, query time,
conversation summary, and retrieved evidence travel in one user-role JSON object
and are explicitly treated as untrusted data. Both OpenAI Responses and embedding
clients use zero SDK retries; timeout, cancellation, provider, tool, and summary
accounting gaps remain explicit and force lower-bound cost labeling.

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
loop. The frontend keeps at most one changed snapshot request in flight and one
replaceable latest snapshot. Send cancels queued/in-flight snapshot transport and
does not await it. `answer.ready` is the user-visible
answer boundary after grounded generation, so the UI stops loading before bounded
post-answer persistence. The SSE connection remains open through
`answer.completed` and closes only on `run.completed` or `run.error`, preserving
final accounting and persistence status. Follow-up context reads use the same session
lease as persistence, so they cannot observe a half-saved turn. Compaction, normal
save, and cancellation fallback share one absolute configured post-answer lease;
cancellation cannot restart or extend it, and failure remains visible. Send atomically
reserves the terminal turn boundary before index-readiness or context awaits, so an
idle reaper or index-sync admission cannot overtake commit setup. This is a
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

`POST /v1/data/sync` first captures and verifies one immutable dataset snapshot,
then chunks and fingerprints those exact retained corpus bytes. It atomically
admits maintenance only while no turn, answer task, or commit setup is active.
Existing work blocks sync with 409; admitted maintenance rejects new turns with
503. Sync marks durable metadata unready before mutation, clears ranked-result
caches, embeds only missing or changed chunks, removes stale points, and marks the
index ready only after final metadata is committed. A failed or interrupted sync
therefore remains unready across restart.

Every answer checks the current dataset checksum/approval and requires the durable
ready flag, source fingerprint, version, desired point count, and physical Qdrant
point count to agree. Search also fails if readiness/version changes in flight.
The durable version remains part of bounded query/result cache keys. Local Qdrant
state under `data/qdrant/` is generated and not committed.

A real-API build indexed 1,000/1,000 points with `text-embedding-3-large` using
366,142 embedding tokens. The final clean reproduction built both fresh isolated
indexes in 94.83 seconds total; that volatile wall time is operational evidence,
not a benchmark metric. The corpus pages themselves are complete after
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

The automatic scorer accepts a citation only when the exact cited chunk is present
in that prediction's retrieved sources and resolves it to the frozen support map.
Optional human semantic labels are separate and must bind to the exact raw
prediction SHA-256; stale adjudications are rejected.

The retained real development comparison used exactly 5 checksum-bound development
questions × 2 isolated paths, deterministic 70-WPM typing, a fixed 5,000 ms pause
after typing and before Send, and one measured pass. The changed-only sampler
delivered the complete draft once during that pause; it did not resend unchanged
text. No answer was generated before Send. Both paths completed all five with
100% automatic expected-answer/alias match, evidence support, valid citation,
supporting-document citation, and false-premise rejection. These are automatic
proxies; human semantic-adjudication coverage was 0%, so this is not a claim of
measured semantic correctness.

Stream won TTFT on all 5/5 pairs. Median TTFT was 1,175.745 ms for Stream versus
3,663.915 ms for Naive; the paired median Stream-minus-Naive delta was
-2,167.428 ms (-59.1560%). Median total time was 2,047.255 ms versus 4,268.152 ms,
and the paired median total-time delta was -2,374.568 ms. Negative deltas favor
Stream. Paired p95 TTFT delta was -947.191 ms; all five pairs were faster.

This small live run does not prove causality or a universal speedup, and it showed
no automatic-proxy gain because both paths matched all expected answers. Stream
reused exact completed speculative evidence in 5/5 cases; all five were ready
before commit and no committed-text fallback was needed. Naive used 5 usage-
accounted model calls, 0 controller attempts, and 5 retrievals; Stream used 17,
18, and 13. Neither path issued a dynamic function-tool call. Observed run cost
was a complete $0.05899131 for Naive and at least $0.10140119 for Stream. Mean observed cost was
$0.011798262 and at least $0.020280238, respectively. Stream accounting was
complete on 0/5 outputs, so no pair supports a final cost comparison. The artifact
is `reportable: false`; the unseen test split remains sealed.

The retained development run took 203.743 s. The final clean reproduction took
about 5 minutes 10 seconds including setup, checksum verification, tests/build,
two fresh real indexes, the same five-pair real benchmark, scoring, and service
startup. That is comfortably inside the 15–20 minute reviewer envelope; provider
variance and first-time package downloads remain the main sources of variation.

See [`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md) for the non-final
development evidence and frozen-test protocol,
[`docs/REAL_USER_VERIFICATION.md`](docs/REAL_USER_VERIFICATION.md) for browser
acceptance, [`docs/DATASET.md`](docs/DATASET.md) for the approval boundary, and
[`docs/SECURITY.md`](docs/SECURITY.md) for the localhost-only trust boundary.
CRAG/data licensing is recorded in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
