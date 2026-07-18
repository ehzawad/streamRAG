# Real-user and concurrency verification record

**Date:** 2026-07-18
**Scope:** development data only; the unseen test split was not run.

The canonical dataset remains `candidate_pending_human_review`. All retained
answer runs use its five checksum-bound development questions only; the unseen
test queries remain sealed and no final benchmark is claimed.

## Browser surface acceptance

The application was exercised on the final source with the real FastAPI/Vite,
OpenAI, embedding, and 1,000-point Qdrant stack; no application path was mocked.
Headed Playwright filled the development stock-holding question in Compare mode,
held the complete draft for 5.5 seconds, and asserted that both answer panels still
read **No answer yet** before clicking Send. Stream reported **Evidence validated
and ready — press Send**. Network inspection showed the exact full text as dirty
revision 1 and the same text as commit revision 2 about 5.16 seconds later, with no
repeated unchanged snapshot. The server's locked 500 ms quiet timer, not repeated
browser traffic, started exact-draft retrieval.

After Send, both paths answered **more than one year** with the same exact IRS
chunk citation (`crag-global-9d22ffbe3ca22f9a9858bd6e::c0005`) and both reached
**Persistence: Saved**. The HTTP commit was accepted in 7 ms. In this live Compare
sample, Stream-minus-Naive TTFT was -1,087 ms and total time was -1,114 ms. The
browser console had zero errors. A trace and the final page were captured; the
retained screenshot is
[`final-verified-compare.png`](../output/playwright/final-verified-compare.png).

Playwright drove the real installed Google Chrome browser, and native Computer Use
inspected the resulting page. Event inspection confirmed exact evidence ready
before Send, followed by `answer.ready`, `answer.completed`, and the run terminal.

Those browser timings are UI acceptance evidence, not benchmark evidence, because
Compare mode runs both paths concurrently in one service. The authoritative A/B
measurement uses sequential isolated services. No browser journey used an unseen
test row.

## Clean-clone reproduction

The canonical clean-clone acceptance is setup, checksum verification, tests/build,
two fresh real indexes, two isolated services, and a scored real-API smoke query.
Its elapsed time is volatile operational evidence, not a benchmark metric.

```bash
make setup
make verify-data
make check
make benchmark-dev-services-check
make benchmark-dev-services-sync
# terminal A: make benchmark-dev-services-serve
# terminal B: make benchmark-smoke && make score-dev
```

The final clean reproduction completed in about 5 minutes 10 seconds: setup 2.01 s,
checksum verification 1.36 s, tests/build about 5.4 s, both fresh real indexes
94.83 s, the five-pair benchmark 203.91 s, scoring 0.20 s, plus service startup.
It is comfortably inside the provider-dependent 15–20 minute envelope.

## Real API/index evidence already complete

- A real OpenAI embedding build indexed 1,000/1,000 chunks using 366,142
  `text-embedding-3-large` tokens. No fixed index wall time is claimed because it
  varies with provider and cache state. No embedding or retrieval call was mocked.
  The current index path captures and verifies one exact dataset snapshot, indexes
  only those retained bytes, marks durable state unready before mutation, and
  requires source/version/desired/physical-count agreement before answering.
- The retained comparison used the five checksum-bound dev questions, two live
  isolated backend processes, and real model calls. Both paths completed 5/5 at
  100% automatic expected-answer/alias match, evidence support, valid citation,
  supporting-document citation, and false-premise rejection. Human semantic-
  adjudication coverage was 0%, so this is automatic-proxy parity rather than a
  100% semantic-correctness claim.
- The retained replay used deterministic 70-WPM typing and a fixed 5,000 ms pause
  after typing before Send. The changed-only sampler delivered one exact full-
  draft snapshot and sent no repeats while it remained unchanged. The server's
  locked 500 ms timer started deterministic exact-draft retrieval; final answer
  generation never began before Send.
- Stream won TTFT on 5/5 pairs. Median paired TTFT/total deltas were
  -2,167.428/-2,374.568 ms; paired p95 TTFT delta was -947.191 ms. Naive median
  TTFT/total was 3,663.915/4,268.152 ms and Stream's was
  1,175.745/2,047.255 ms. The five live pairs are too small to promise the same
  ordering on every rerun.
- Naive used 5 usage-accounted model calls, 0 controller attempts, and 5
  retrievals; Stream used 17, 18, and 13. Both paths made zero dynamic
  function-tool calls. Stream's observed run cost was at least $0.10140119 and
  mean at least $0.020280238; Naive's complete total/mean was
  $0.05899131/$0.011798262. Stream accounting was complete for 0/5 outputs and
  Naive for 5/5, so no pair had complete accounting and no final cost comparison
  is claimed. The run took 203.743 s. The artifact is
  `completed_non_reportable`; no unseen query was run.
- Stream reused completed exact-draft evidence in 5/5 cases, and all five were
  ready before commit. Any changed commit would have taken the exact-text fallback.
  Both paths share the exact committed-text fallback, grounded answer model, prompt, corpus,
  embedding, and ANN policy; Stream alone adds pre-Send trigger/speculative work.
- The retained development result and its limitations are recorded in
  [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md).

## Event-loop and embedded-Qdrant probe

Embedded Qdrant exposes a synchronous local client, so all local operations run
through one dedicated `ThreadPoolExecutor` worker. Dataset hashing/chunk loading
and metrics-log writes are also offloaded. Remote Qdrant retains its async client.

`scripts/probe_qdrant_concurrency.py` exercised the application-owned local worker
with real stored 3,072-dimensional vectors and ANN/payload reads:

- 1,000 indexed points;
- 256 requests at client concurrency 32;
- 0 errors and 347.431 requests/s;
- request latency including the serialized queue: p50 91.778 ms, p95 93.449 ms,
  max 94.185 ms;
- event-loop lag: p50 0.291 ms, p95 1.158 ms, max 1.298 ms;
- 50 ms event-loop-lag budget: passed.

The probe deliberately does not call the embedding API: it uses real vectors
already stored in the final local index and fails if an embedding call is
attempted. It characterizes this machine/corpus boundary; it is not an end-to-end
HTTP/OpenAI load test and does not justify an unlimited-concurrency production
claim.

## Non-blocking user path

- OpenAI/PydanticAI calls use explicit async clients. Responses roles use no SDK
  retry beneath their measured deadline; embeddings also use zero SDK retries.
  Role-specific timeouts are 4 s trigger, 6 s retrieval, 30 s answer, and 8 s
  summary; all post-answer persistence work shares one configured absolute lease.
- The browser samples every 400 ms, skips unchanged text, and keeps one changed
  snapshot request active plus one replaceable latest draft. The server owns the
  locked 500 ms unchanged timer. Send aborts obsolete snapshot transport without
  awaiting it. The background answer path cancels an unfinished model decision or
  mismatched retrieval, but may await a literal-exact retrieval already in flight.
- Send reserves the terminal turn boundary under the runtime lock before any index-
  readiness or context await, so idle reaping and index-sync admission cannot race
  an accepted commit setup.
- SSE response collection is independent of input controls, and Cancel/New turn
  remain available during work.
- `answer.ready` carries the grounded answer and ends visible loading for each
  requested path before bounded post-answer persistence. The stream remains open
  for the later `answer.completed` accounting/persistence event and closes only on
  `run.completed` or `run.error`.
- Follow-up context reads share the post-answer session lease, so they cannot race
  a pending save. Compaction, normal save, and raw-save fallback consume the same
  absolute deadline. Cancellation after `answer.ready` can use only the time left
  in that lease before propagating; it cannot create an additive timeout or hang
  indefinitely. Idle-turn cleanup,
  terminal-event retention, and atomic maintenance admission prevent unbounded
  task/session growth and index mutation during live work.

The grounded agent keeps privileged instructions static and sends question, query
time, summary, and evidence as one untrusted user-role JSON object. Generation and
summary gaps have explicit failed-ledger/unpriced counters; cost is labeled a lower
bound whenever provider usage is unavailable.

These controls make the assessment UI non-blocking under its intended local load.
Production scaling would replace embedded Qdrant and SQLite and would require a
separate end-to-end concurrency/load campaign.
