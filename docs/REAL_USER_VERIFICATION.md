# Real-user and concurrency verification record

**Date:** 2026-07-18
**Scope:** development data only; the unseen test split was not run.

The canonical dataset remains `candidate_pending_human_review`. All retained
answer runs use its five checksum-bound development questions only; the unseen
test queries remain sealed and no final benchmark is claimed.

## Browser surface acceptance

The repaired 1,000-point application was started with the real FastAPI/Vite,
OpenAI, embedding, and Qdrant stack. No frontend, SSE, retrieval, controller, or
answer call was mocked. Headed Playwright typed the development-only Bad Bunny
question in Compare mode, paused for five seconds so prefetch could finish, and
then pressed Send; no frozen-test query was used.

Both paths returned **Un Verano Sin Ti** and cited the same audited local source
chunk, `crag-global-a6deebaec508edb9daefce6e::c0004`. The final UI exposed the
locked model/role configuration and 1,000 chunks, plus TTFT, total time, partial
cost, accepted/retrieval-ready lead, evidence mode, cache, citations, controller /
retrieval / tool calls, and fallback count.

The browser displayed raw prefetch activity before Send, but no final answer was
generated or shown before Send. Raw retrieved candidates were provisional; the
commit path still had to validate, finish compatible work, or retrieve again before
grounded generation. Concurrent Compare timings are intentionally not treated as
benchmark evidence.

Final viewport evidence:
[`final-verified-compare.png`](../output/playwright/final-verified-compare.png).
The screenshot is UI evidence, not the authoritative timing/cost record; formal
claims use the content-addressed isolated run below.

## Independent browser-surface checks

After the Playwright journey, the same local stack was inspected through the
requested additional surfaces:

- **In-app Browser:** loaded health showing `gpt-5.6-sol`, medium answer / low
  trigger roles, and 1,000 points; observed raw typed prefetch before Send, then
  post-commit grounded completion of both Compare paths. This is flow/surface
  evidence only.
- **macOS Computer Use (current-code Chrome acceptance):** health showed
  `gpt-5.6-sol`, medium answer / low trigger reasoning, and 1,000 chunks. After
  typing the Bad Bunny development question and pausing six seconds before Send,
  the UI showed **Evidence validated and ready — press Send** while both answer
  panels still showed **No answer yet**. After Send, both paths correctly returned
  **Un Verano Sin Ti** with one local citation each. Naive showed 5,945 ms TTFT /
  7,183 ms total; Stream showed 1,551 ms / 2,019 ms, for illustrative concurrent
  deltas of -4,393 ms TTFT and -5,164 ms total. This is current native UI evidence,
  not isolated benchmark evidence.
- **Chrome control (final browser action):** ran a full Compare on the same Bad
  Bunny development question. Both paths again returned and cited **Un Verano Sin
  Ti**, with raw prefetch visible before Send and the answer appearing only after
  commit.

The browser journeys are illustrative surface acceptance only. They are not merged
with the isolated development benchmark and do not weaken the dataset approval
gate.

## Clean-clone reproduction

The canonical clean-clone acceptance is setup, checksum verification, tests/build,
two fresh real indexes, two isolated services, and a scored real-API smoke query.
Its elapsed time is volatile operational evidence, not a benchmark metric. The
earlier pre-fix timing and source-commit claim have been removed rather than
carried forward as current evidence.

## Real API/index evidence already complete

- A real OpenAI embedding build indexed 1,000/1,000 chunks using 366,142
  `text-embedding-3-large` tokens. No fixed index wall time is claimed because it
  varies with provider and cache state. No embedding or retrieval call was mocked.
- The retained comparison used the five checksum-bound dev questions, two live
  isolated backend processes, and real model calls. Both paths completed 5/5 at
  100% automatic expected-answer, evidence-support, supporting-citation, and
  false-premise correctness. Stream won TTFT on 5/5 pairs with median paired
  TTFT/total deltas of -3,090.101/-1,251.090 ms; the paired p95 TTFT delta was
  -1,178.695 ms. Naive median TTFT/total was 6,199.342/6,846.266 ms and Stream's
  was 4,942.155/5,662.263 ms. This is
  correctness parity, not an accuracy gain, and the five live pairs are too small
  to promise the same ordering on every rerun.
- Stream used 19 model calls, 21 controller calls, and 12 retrievals versus
  Naive's 8, 5, and 5. Both paths made zero dynamic function-tool calls. Observed
  costs were lower bounds: at least $0.10848677 Stream and $0.06640001 Naive;
  complete accounting covered 0/5 and 3/5 outputs, respectively, so there is no
  paired cost delta. The run took 191.662 s. The artifact is `reportable: false`;
  no unseen query was run.
- All accepted evidence lead-at-commit measurements were zero. Raw candidates
  could exist earlier, but remained provisional until commit validation; Stream's
  fallback, compatible post-commit overlap, and speculative-reuse rates were 60%,
  20%, and 20%. The one ultimately reused candidate had 1,944.726 ms of
  provisional candidate headroom. Final answer generation never began before
  Send.
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
  retry beneath their measured deadline; embeddings retain one bounded retry.
  Role-specific timeouts are 4 s trigger, 6 s retrieval, 30 s answer, 8 s summary,
  and 10 s post-answer persistence.
- The browser keeps one snapshot request active and one replaceable latest pending
  snapshot; Send aborts obsolete snapshot work instead of waiting behind it.
- SSE response collection is independent of input controls, and Cancel/New turn
  remain available during work.
- `answer.ready` carries the grounded answer and ends visible loading for each
  requested path before bounded post-answer persistence. The later
  `answer.completed` event is accounting/maintenance telemetry and does not hold
  the user-visible run open.
- Follow-up context reads share the post-answer session lease, so they cannot race
  a pending save. If optional compaction fails, the completed turn is durably saved
  uncompressed before failure telemetry. Idle-turn cleanup, terminal-event
  retention, and a maintenance lock prevent unbounded task/session growth and
  index mutation during live work.

These controls make the assessment UI non-blocking under its intended local load.
Production scaling would replace embedded Qdrant and SQLite and would require a
separate end-to-end concurrency/load campaign.
