# Typed StreamRAG implementation decisions

**Date:** 2026-07-19
**Status:** isolated implementation; canonical dataset awaits human approval

## Scope

The assignment requires a small full-stack Naive RAG versus StreamRAG comparison
with a tool, memory/context handling, fixed expected answers, and speed, cost,
accuracy, and performance reporting. The Stream RAG paper supplies the scheduling
idea and explicitly permits typed input. This project does not claim its speech
pipeline, training, 100,000-document setup, or reranker.

Audio, managed vector infrastructure, model training, deployment, and native code
are outside the assessment. Public web search is outside both RAG paths; answers
use only the local checksum-bound corpus.

## Ownership and deletion boundaries

| Directory | Owns | May depend on |
|---|---|---|
| `shared/` | corpus/index integrity, embeddings/search, grounded agent/tool, memory, API lifecycle, single-path UI shell, versioned contracts | external libraries only |
| `naive/` | exact committed-text retrieval policy and Naive entrypoint | `shared/` |
| `stream/` | typed snapshot analysis, trigger, speculation, commit validation, Stream entrypoint | `shared/` |
| `comparison/` | external A/B UI, HTTP runner, two-service provisioning, scoring, reports | service HTTP/JSON/SSE contracts; local entrypoints and quiescent state layout only in its provisioner |

The shared UI shell is capability-driven and renders one service; it contains no
path selector or comparison logic. Consequently:

- removing `comparison/` leaves two independently runnable full-stack apps;
- removing `naive/` leaves the Stream app intact;
- removing `stream/` leaves the Naive app intact;
- neither implementation imports the other or `comparison/`;
- production comparison code imports no application package.

The comparison UI, replay runner, and scorer are black-box service consumers. The
local `comparison/services.py` provisioner is a deployment-only exception: it
starts the repository entrypoints and clones a stopped seed's Qdrant and SQLite
index state before measurement. It does not import either implementation or run
benchmark questions.

This duplication/ownership boundary is intentional. Code is shared only when its
behavior must be identical for fairness or is genuinely reusable by one path in
isolation.

## Locked behavior

| ID | Decision |
|---|---|
| D01 | Naive waits for the immutable commit, then retrieves with that exact text and answers. |
| D02 | Stream samples the real text box every 400 ms and sends only changed cumulative drafts before Send. |
| D03 | Evolving Stream prefixes use a bounded zero-shot model trigger; a delivered draft unchanged for 500 ms may start deterministic exact-draft retrieval. |
| D04 | Pre-Send evidence is provisional. Only Send may commit evidence or start grounded answer generation. |
| D05 | Corrections invalidate stale work. Completed evidence is reusable only when its source text literally equals the commit; otherwise Stream uses the same exact committed-text fallback as Naive. |
| D06 | Both paths use the same corpus, chunker, embedding/search policy, answer model/prompt/tool, memory, context policy, and common metric definitions. |
| D07 | Naive and Stream run as different processes with isolated Qdrant, SQLite, metrics, sessions, and cache namespaces. |
| D08 | One canonical dataset remains at `data/crag_eval`: 5 dev, 10 sealed test, 250 complete documents, and exactly 1,000 points. |
| D09 | Final evaluation remains blocked while status is `candidate_pending_human_review`; only explicit human approval may create `approved_frozen`. |
| D10 | Use `text-embedding-3-large` at 3,072 dimensions, cosine search, 400/50-token chunking, 8 candidates, and 5 answer-context chunks. |
| D11 | Use `gpt-5.6-sol` with medium answer reasoning and low trigger/summary reasoning. |
| D12 | Use PydanticAI typed output and strict local-corpus function calling; keep scheduling in explicit async application code. |
| D13 | Use real OpenAI and real local Qdrant in live verification; mocks are limited to isolated unit tests and never support a live-result claim. |
| D14 | Formal evaluation is 10 sealed questions × two paths × one measured pass, no warm-up, deterministic 70 WPM replay, changed-only 400 ms sampling, 5 s post-typing dwell, and a 45 s case deadline. |

## Typed-input and commit boundary

Typed input means the evolving contents of the actual input box, including partial
words—not a completed string replayed after Send. The browser sends monotonic,
changed-only drafts. It keeps one request active and one replaceable latest draft,
so typing remains non-blocking and unchanged text creates no repeated traffic.

Every changed Stream snapshot resets the 500 ms settled-draft timer. The trigger
may choose `wait`, `retrieve`, or `keep_previous` for eligible evolving prefixes.
Only one speculative retrieval is active, and edits cancel or discard incompatible
work. A quiet full draft may finish retrieval before Send, but no answer is shown.

Send freezes the final revision. Naive starts retrieval then. Stream promotes only
literal-exact completed evidence or awaits literal-exact work already in flight;
all other cases take bounded exact-commit retrieval. The answer agent sees only
accepted evidence. `answer.ready` is the visible-answer boundary; persistence and
complete accounting finish by `answer.completed`, and SSE remains open until the
run terminal event.

## Shared correctness and trust boundary

Index sync captures and verifies one immutable dataset snapshot, chunks those
captured bytes, marks durable metadata unready before mutation, and marks ready
only after source, version, checksum, desired-count, and physical-count metadata
agree. Answer admission rechecks dataset approval/checksums and the same index
identity. Maintenance and turns are atomically exclusive.

The grounded agent keeps privileged instructions static. Question text, query
time, conversation summary, and accepted evidence are serialized together as
untrusted user-role data. The strict `search_local_crag` tool is read-only, local,
and bounded to one dynamic call when primary evidence is insufficient. Retrieved
bodies and speculative evidence are not persisted as conversation memory.

FastAPI and OpenAI calls are async. Embedded Qdrant is synchronous, so local
vector work runs on a dedicated worker rather than the event loop. Follow-up
context reads and post-answer saves share the same session lease; compaction,
normal save, and fallback share one absolute persistence deadline.

`shared.config` loads and validates one immutable settings object per process.
Configuration changes require a restart. This makes each independently launched
service fail fast and lets comparison validate that common settings match.

## State provisioning

Each service can build an index independently through its own `/v1/data/sync`.
For a fair, fast A/B setup, `comparison/services.py sync` instead:

1. starts a temporary seed service with a fresh state directory;
2. builds one real OpenAI embedding index;
3. stops the seed and verifies no SQLite WAL/SHM state remains;
4. clones Qdrant plus matching SQLite index metadata into sibling Naive and
   Stream state directories;
5. starts and validates the two services against their roles, fingerprints, and
   physical point counts.

No process is live during the copy. After cloning, the services never share a
mutable file, cache, session, or vector store.

## Evaluation and metric ownership

Each service identifies its implementation, snapshot capability, source/config/
dataset fingerprints, and metric-contract version. The comparison runner refuses
role swaps, contract mismatch, shared-source/config/dataset drift, or shared state.

Common metrics are compared pairwise:

- submit-to-first-token and total response latency;
- completion, failure, timeout, and throughput;
- expected-answer/alias and false-premise proxies;
- retrieved support, exact cited-chunk validity, and supporting-document citation;
- model/retrieval calls, tokens, cost-accounting coverage, and observed cost.

Stream-only fields remain Stream diagnostics: trigger attempts and decisions,
speculative/settled retrievals, accepted evidence lead, reuse/revalidation,
discard/cancellation, and commit fallback. They are not fabricated for Naive.

Test gold is not provided to or read by the service and runner code paths. The
offline scorer receives it only after predictions are content-addressed. Human
semantic adjudication is separate from automatic answer/alias and citation
proxies.

## Honest claim boundary

The existing five-question development artifact used real OpenAI calls and local
Qdrant and is permanently non-final. It does not approve the dataset or disclose a
test result. Its source-bound result, isolated architecture, and browser behavior
were re-verified end to end for this revision.

The defensible target is correctness parity with lower perceived latency when the
retrieval intent stabilizes before Send. Late constraints and revisions can erase
the head start and add work. No universal latency, accuracy, or cost improvement is
claimed.

## References

- [CRAG repository and license](https://github.com/facebookresearch/CRAG)
