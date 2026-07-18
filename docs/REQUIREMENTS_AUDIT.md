# Requirements audit

**Audit date:** 2026-07-19
**Dataset status:** `candidate_pending_human_review`; unseen benchmark not run

| Assessment requirement | Implementation | Current gate |
|---|---|---|
| Minimal full-stack UI | capability-driven single-path UI on each API plus optional external Naive/Stream/Compare UI | headed Playwright, native Chrome, and Computer Use passed |
| Isolated Naive RAG | `naive/`: commit → exact committed-text retrieval → grounded answer | independently runnable on port 8001 |
| Isolated typed StreamRAG | `stream/`: changed drafts → model/settled trigger → literal-exact reuse or committed-text fallback | independently runnable on port 8002 |
| Removable comparison | `comparison/`: HTTP/JSON/SSE UI, runner, provisioner, scorer; no application imports | architecture tests enforce boundary |
| Truly shared core | `shared/`: corpus/index, agent/tool, memory, lifecycle, UI shell, contracts only | no implementation/comparison imports |
| Working tool | strict read-only PydanticAI `search_local_crag` over local corpus | regression-tested; live use may be zero when primary evidence is sufficient |
| Memory/context | async SQLite history, rolling summary, shared session lease, bounded persistence | common to both paths |
| Fixed 10–20 test set | 10 unseen test plus 5 development questions | generated/checksummed; human approval pending |
| Same evaluation inputs | same dataset/config/prompt/model/search/scorer; exact final text; role/fingerprint validation | final run blocked by approval |
| Independent state | different Qdrant, SQLite, logs, sessions, caches, and processes | one quiescent seed may be cloned before startup |
| Speed | TTFT, total, p95, paired deltas, Stream evidence-lead diagnostics | current-source development artifact retained |
| Cost | provider usage, calls/tokens, explicit unpriced counters and lower-bound labels | paired delta only with complete accounting |
| Correctness | answer/alias, false-premise, retrieved support, exact citation, optional hash-bound human adjudication | automatic proxies are not semantic accuracy |
| Performance | completions, failures, timeouts, throughput, retrieval/fallback/reuse | common and Stream-only metrics separated |
| Non-blocking UX | async HTTP/OpenAI, Qdrant worker isolation, bounded snapshot queue, cancellable SSE | local boundary; not production-scale claim |
| Reproducible handoff | committed corpus, checksum verifier, one real seed build, isolated clones, 5-dev smoke and 10-test final protocols | target 15–20 minutes; record observed time |
| Security boundary | loopback-only, key remains server-side, no-auth threat model | public deployment unsupported |

## Evidence boundary

The retained five-question development artifact used real OpenAI calls, real
embeddings, two service processes, and embedded Qdrant. Its manifest binds both
service source identities to the current tree. It recorded automatic
answer/citation parity and lower Stream TTFT, but no human semantic adjudication
and incomplete Stream cost accounting, so it remains non-final.

The submitted current-source verification includes:

1. all shared, Naive, Stream, comparison, frontend, and architecture tests pass;
2. one clean real seed index is cloned into two isolated, healthy 1,000-point
   stores;
3. Naive and Stream standalone UIs pass headed Playwright/Chrome and native
   Computer Use;
4. the external comparison UI passes the same live browser checks with no answer
   before Send and equal independent commits;
5. all five development questions complete through both services and score into
   a content-addressed non-reportable artifact;
6. the clean reproduction stays within the intended 15–20 minute reviewer budget,
   with actual elapsed time recorded.

Submission-ready final measurement additionally requires human review/freeze of
all 15 dataset rows, exactly the same 10 unseen questions through both services,
zero uncounted failures, and explicit cost-accounting and human-adjudication status.

## Scope judgment

The application intentionally omits audio, managed Qdrant, reranking, training,
and a native extension. Those would add variables without strengthening this typed
scheduling comparison. Embedded Qdrant and SQLite are appropriate for a local
assessment, not represented as production multi-tenant infrastructure.
