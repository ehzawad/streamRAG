# Requirements audit

**Audit date:** 2026-07-19
**Dataset status:** `candidate_pending_human_review`; unseen benchmark not run

This table maps each assignment requirement to runnable code and reviewer-visible
evidence.

| Assessment requirement | Implementation | Current gate |
|---|---|---|
| Minimal full-stack UI | single-path UI on each API plus root `frontend/` with Naive, Stream, and Compare modes | headed Playwright, native Chrome, and Computer Use passed |
| Isolated Naive RAG | `naive/`: commit → exact committed-text retrieval → grounded answer | independently runnable on port 8001 |
| Isolated typed StreamRAG | `stream/`: changed drafts → model/settled trigger → literal-exact reuse or committed-text fallback | independently runnable on port 8002 |
| Removable frontend | `frontend/`: HTTP/SSE GUI only; no Python or benchmark ownership | APIs and CLI run without it |
| Headless comparison | `comparison/`: HTTP replay, provisioner, scorer, reports; no GUI or application imports | architecture tests enforce boundary |
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

## Evidence status

The retained five-question artifact used real OpenAI, real embeddings, two
service processes, and local Qdrant. Tests, isolated 1,000-point stores, all three
UIs, 10/10 development outputs, and the content-addressed report passed.

It remains non-final because the unseen split is sealed, human semantic review is
0%, and Stream cost accounting is incomplete.

Final measurement requires human review and freeze of all 15 rows, the same 10
unseen questions through both services, zero uncounted failures, and explicit
cost-accounting and human-review status.

## Deliberate omissions

Audio, managed Qdrant, reranking, training, and native extensions would add
variables without strengthening this typed comparison. Local Qdrant and SQLite
fit the assessment; they are not presented as production infrastructure.
