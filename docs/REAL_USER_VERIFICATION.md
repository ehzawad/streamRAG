# Real-user and concurrency verification record

**Date:** 2026-07-18
**Scope:** development data only; the unseen test split was not run.

## Final Playwright acceptance

The repaired 1,000-point application was started with the real FastAPI/Vite,
OpenAI, embedding, and Qdrant stack. No frontend, SSE, retrieval, controller, or
answer call was mocked. Headed Playwright typed the development-only Bad Bunny
question at 45 ms/character in Compare mode and allowed prefetch before Send; no
frozen-test query was used.

Both paths returned **Un Verano Sin Ti** and cited the same audited local source
chunk, `crag-global-a6deebaec508edb9daefce6e::c0000`. The final UI exposed the
locked model/role configuration and 1,000 chunks, plus TTFT, total time, partial
cost, accepted/retrieval-ready lead, evidence mode, cache, citations, controller /
retrieval / tool calls, and fallback count.

Visible diagnostic values were:

| Path | TTFT | Total | Cost | Evidence | Accepted lead | Calls (controller/retrieval/tool) |
|---|---:|---:|---:|---|---:|---:|
| Naive | 6,335 ms | 6,647 ms | $0.0113 | At Send | 0 ms | 1 / 1 / 0 |
| Stream | 2,062 ms | 2,460 ms | $0.0167 | Revalidated | 15,952 ms | 3 / 1 / 0 |

This single concurrent Compare view is a live diagnostic, not an accuracy score or
reportable benchmark. It demonstrates the intended early-stabilization experience:
the Stream path preserved the same answer/citation and exposed a 4,273 ms lower
TTFT at $0.0054 higher displayed cost. Formal claims come from the isolated runner.

Final viewport evidence:
[`final-verified-compare.png`](../output/playwright/final-verified-compare.png).
Earlier screenshots from superseded corpora/configurations are intentionally not
accepted.

## Independent browser-surface checks

After the Playwright journey, the same local stack was inspected through the
requested additional surfaces:

- **In-app Browser:** loaded health showing `gpt-5.6-sol`, medium answer / low
  trigger roles, and 1,000 points; observed typed prefetch/evidence ready before
  Send and completion of both Compare paths. This is flow/surface evidence only.
- **macOS Computer Use:** switched to and visually inspected the local application
  in Google Chrome. This confirms the native UI surface, not answer correctness or
  benchmark timing.
- **Chrome control (final browser action):** ran a full Compare on the same Bad
  Bunny development question. Both paths again returned and cited **Un Verano Sin
  Ti**. Naive showed 4,086 ms TTFT, 4,375 ms total, and $0.0108; Stream showed
  1,604 ms TTFT, 3,922 ms total, and $0.0109, with evidence ready before Send. The
  visible TTFT delta was -2,481 ms.

These live values vary with provider/network timing and concurrent Compare
execution. They are illustrative acceptance evidence only; they are not merged
with the isolated development benchmark and do not weaken the dataset approval
gate.

## Real API/index evidence already complete

- A clean real OpenAI embedding build indexed 1,000/1,000 chunks in 40.94 s using
  366,142 `text-embedding-3-large` tokens. No embedding or retrieval call was
  mocked.
- The five-question development comparison used two live, isolated backend
  processes and real model calls. Both paths achieved 100% expected-answer and
  supporting-citation correctness; the unseen split remained untouched.
- The final development result and its limitations are recorded in
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

- OpenAI/PydanticAI calls use explicit async clients, bounded retries, and role-
  specific timeouts: 4 s trigger, 6 s retrieval, 30 s answer, 8 s summary.
- The browser keeps one snapshot request active and one replaceable latest pending
  snapshot; Send aborts obsolete snapshot work instead of waiting behind it.
- SSE response collection is independent of input controls, and Cancel/New turn
  remain available during work.
- Per-session leases, idle-turn cleanup, terminal-event retention, and a maintenance
  lock prevent unbounded task/session growth and index mutation during live work.

These controls make the assessment UI non-blocking under its intended local load.
Production scaling would replace embedded Qdrant and SQLite and would require a
separate end-to-end concurrency/load campaign.
