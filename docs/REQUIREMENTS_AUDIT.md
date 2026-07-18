# Requirements audit

**Audit date:** 2026-07-18
**Overall status:** implementation, multi-surface browser acceptance, and real
development evidence are complete; human dataset freeze remains open. The unseen
benchmark has not run.

| Assessment requirement | Implemented artifact/evidence | Status |
|---|---|---|
| Minimal full-stack UI | one-page React/Vite Naive, Stream, and Compare interface | passed headed Playwright, in-app Browser, Computer Use, and final Chrome checks |
| Backend serving both paths | versioned FastAPI REST/SSE API | implemented and exercised by two live development services |
| Naive RAG | commit → controller → retrieval → grounded answer | real dev run complete |
| Typed StreamRAG | cumulative pre-Send snapshots → bounded meaningful-prefix prefetch + model validation → commit gate | real dev run complete |
| Working tool | strict PydanticAI `search_local_crag` read-only function | implemented and regression-tested |
| Cross-turn memory | async SQLite, shared read/write session lease, durable uncompressed save on compaction failure | implemented and tested |
| Context compression / skill | low-reasoning rolling-summary role after budget threshold | implemented and tested |
| Fixed 10–20-query test set | 10 unseen test questions plus 5 development questions | generated and checksummed; human approval pending |
| Same inputs for both paths | gold-free runner, separate services/stores, matching fingerprints | implemented; frozen run blocked by approval |
| Speed | TTFT, total, p95, paired deltas, pre-Send diagnostics | real dev report complete; final pending |
| Cost | provider usage, calls, partial-cost flags, USD | real dev lower-bound report complete; final pending |
| Accuracy | answer/alias, false-premise rejection, support/citation | real dev score complete; final pending |
| Performance | completions, failures, throughput, fallback/reuse/overlap | runner/scorer complete; final pending |
| Non-blocking local UX | async OpenAI, bounded snapshot queue, cancellable SSE, user-visible `answer.ready` boundary, Qdrant worker isolation | implemented; lifecycle tests and local ANN concurrency probe passed |
| One-command local/Docker run | `make dev`, `make docker-up` | implemented |
| Reproducible handoff | committed corpus, checksum verifier, real index sync, bounded 20-run protocol | designed for about 15–20 min |
| Deployment security boundary | loopback-only direct/Compose ports; no-auth threat model | local assessment only; public deployment explicitly unsupported |
| Optional low-level component | profile-driven decision | omitted; local ANN is not the latency bottleneck |

## Evidence boundary

The real five-question development comparison used `gpt-5.6-sol` (medium answer,
low trigger/summary), `text-embedding-3-large`, two isolated processes, and no
mocks. Both paths scored 100% expected-answer, evidence-support, supporting-
citation, and false-premise correctness, so scheduling showed no accuracy gain.
Stream won paired TTFT on 4/5 (80%): median Stream-minus-Naive TTFT was -628.907
ms (-12.0846%), paired p95 TTFT delta was +275.880 ms because one tail pair was
slower, and median total-time delta was -702.294 ms. Early, late, and
revision/ambiguity slices won 3/3, 0/1, and 1/1, respectively. This is a small
non-final live run, not a promise of universal speedup. Stream used more
model/retrieval work and had a higher observed lower-bound cost; incomplete
provider accounting leaves no paired cost delta.

This is implementation evidence, not the required final benchmark. Submission-
ready measurement still requires:

1. human review of all 15 rows and status/checksum freeze as `approved_frozen`;
2. both isolated paths to complete exactly the same 10 unseen queries;
3. automatic score/report generation with zero uncounted failures and complete or
   explicitly lower-bound provider accounting.

## Scope judgment

The application intentionally avoids audio, a managed Qdrant dependency, a
reranker, model training, and a native extension. Those would add variables without
improving the scheduling comparison. Embedded Qdrant is isolated from the event
loop and characterized under concurrent local ANN reads, but SQLite/local Qdrant
are not represented as production-scale infrastructure.
