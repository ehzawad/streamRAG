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
| Typed StreamRAG | cumulative pre-Send snapshots → bounded model trigger → speculative retrieval → commit gate | real dev run complete |
| Working tool | strict PydanticAI `search_local_crag` read-only function | implemented and regression-tested |
| Cross-turn memory | async SQLite repository with session leases/rotation | implemented and tested |
| Context compression / skill | low-reasoning rolling-summary role after budget threshold | implemented and tested |
| Fixed 10–20-query test set | 10 unseen test questions plus 5 development questions | generated and checksummed; human approval pending |
| Same inputs for both paths | gold-free runner, separate services/stores, matching fingerprints | implemented; frozen run blocked by approval |
| Speed | TTFT, total, p95, paired deltas, pre-Send diagnostics | real dev report complete; final pending |
| Cost | provider usage, calls, partial-cost flags, USD | real dev lower-bound report complete; final pending |
| Accuracy | answer/alias, false-premise rejection, support/citation | real dev score complete; final pending |
| Performance | completions, failures, throughput, fallback/reuse/overlap | runner/scorer complete; final pending |
| Non-blocking local UX | async OpenAI, bounded snapshot queue, cancellable SSE, Qdrant worker isolation | implemented; local ANN concurrency probe passed |
| One-command local/Docker run | `make dev`, `make docker-up` | implemented |
| Reproducible handoff | committed corpus, checksum verifier, real index sync, bounded 20-run protocol | designed for about 15–20 min |
| Optional low-level component | profile-driven decision | omitted; local ANN is not the latency bottleneck |

## Evidence boundary

The real five-question development comparison used `gpt-5.6-sol` (medium answer,
low trigger/summary), `text-embedding-3-large`, two isolated processes, and no
mocks. Both paths scored 100% expected-answer and supporting-citation correctness.
Stream won paired TTFT on 60% overall and 100% of the three early-stabilizing
questions, at higher call count and observed lower-bound cost.

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
