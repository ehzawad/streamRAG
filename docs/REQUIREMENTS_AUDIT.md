# Requirements audit

**Audit date:** 2026-07-18
**Overall status:** implementation, final-source clean reproduction, fresh real
development evidence, and real-service Playwright/Chrome/Computer Use acceptance
are complete. Human dataset freeze remains open and the unseen benchmark has not
run.

| Assessment requirement | Implemented artifact/evidence | Status |
|---|---|---|
| Minimal full-stack UI | one-page React/Vite Naive, Stream, and Compare interface | final-source real-service Playwright passed; Chrome and native Computer Use also passed |
| Backend serving both paths | versioned FastAPI REST/SSE API | implemented and exercised by two live development services |
| Naive RAG | commit → exact committed-text retrieval → grounded answer | real dev run complete |
| Typed StreamRAG | changed-only 400 ms drafts → model trigger while evolving / exact retrieval after 500 ms unchanged → literal commit reuse or exact fallback | real dev run complete |
| Working tool | strict PydanticAI `search_local_crag` read-only function | implemented and regression-tested |
| Cross-turn memory | async SQLite, shared read/write lease, compaction/normal/raw fallback under one absolute post-answer deadline with observable status | implemented and tested |
| Context compression / skill | low-reasoning rolling-summary role after budget threshold | implemented and tested |
| Fixed 10–20-query test set | 10 unseen test questions plus 5 development questions | generated and checksummed; human approval pending |
| Same inputs for both paths | shared exact-text fallback/model/prompt/corpus/embedding/ANN; gold-free runner; separate services/stores; matching fingerprints | implemented; frozen run blocked by approval |
| Speed | TTFT, total, p95, paired deltas, pre-Send diagnostics | real dev report complete; final pending |
| Cost | provider usage, zero SDK retries, explicit unpriced-call counters, USD | Naive complete and Stream lower-bound dev report complete; final pending |
| Automatic answer proxy | answer/alias, false-premise rejection, exact-chunk validity, support resolution | real dev score complete; human semantic coverage 0%; final pending |
| Performance | completions, failures, throughput, fallback/reuse/overlap | runner/scorer complete; final pending |
| Non-blocking local UX | async OpenAI, bounded snapshot queue, cancellable SSE through run terminal, visible `answer.ready`, Qdrant worker isolation | implemented; lifecycle tests and local ANN concurrency probe passed |
| Index/turn integrity | exact verified corpus snapshot, atomic maintenance admission, pre-await terminal commit reservation, durable fail-closed readiness/source/version/count gates | implemented and regression-tested |
| Prompt trust boundary | static privileged instructions; question, time, summary, and evidence in untrusted user-role JSON | implemented and regression-tested |
| One-command local/Docker run | `make dev`, `make docker-up` | implemented |
| Reproducible handoff | committed corpus, checksum verifier, real index sync, bounded 20-run protocol at 70 WPM + changed-only 400 ms sampling + 500 ms server timer + fixed 5 s dwell | clean five-question flow observed at about 5 min 10 s; full protocol designed for 15–20 min |
| Deployment security boundary | loopback-only direct/Compose ports; no-auth threat model | local assessment only; public deployment explicitly unsupported |
| Optional low-level component | profile-driven decision | omitted; local ANN is not the latency bottleneck |

## Evidence boundary

The real five-question development comparison used `gpt-5.6-sol` (medium answer,
low trigger/summary), `text-embedding-3-large`, two isolated processes, and no
mocks. Both paths scored 100% on the automatic expected-answer/alias, evidence-
support, exact-chunk citation, supporting-document, and false-premise proxies.
Human semantic-adjudication coverage was 0%, so these results do not establish
100% semantic correctness or an accuracy gain.

The replay was fixed at 70 WPM plus a 5,000 ms post-typing dwell before Send.
Changed-only 400 ms sampling delivered the complete draft once; the locked 500 ms
server timer started exact-draft retrieval without repeat browser traffic. No answer
began during that pause. Stream won paired TTFT on 5/5 (100%): median
Stream-minus-Naive TTFT was -2,167.428 ms (-59.1560%), paired p95 TTFT delta was
-947.191 ms, and median total-time delta was -2,374.568 ms. Median TTFT/total was
1,175.745/2,047.255 ms for Stream and 3,663.915/4,268.152 ms for Naive. Every
stabilization slice won TTFT in this run. This is a small non-final live run, not a
promise of universal speedup.

Naive used 5 usage-accounted model calls, 0 controller attempts, and 5 retrievals;
Stream used 17, 18, and 13. Observed run cost was at least $0.10140119 Stream
versus a complete $0.05899131 Naive; means were at least $0.020280238 and
$0.011798262. Stream accounting was complete on 0/5 outputs while Naive was
complete on 5/5, so no pair supports a priced comparison and no final cost
conclusion is claimed. Stream reused completed literal full-draft evidence in 5/5
cases; all five were ready before commit. A changed commit would have fallen back.

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
