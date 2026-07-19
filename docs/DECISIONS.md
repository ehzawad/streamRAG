# Product and architecture decisions

**Date:** 2026-07-19
**Dataset:** `candidate_pending_human_review`

## Scope

This full-stack assessment compares Naive RAG with StreamRAG for typed input over
the same local CRAG knowledge base. The StreamRAG paper motivates moving retrieval
into input time. This implementation adapts that scheduling idea; it does not
reproduce the paper's speech stack, trained trigger, reranker, or scale.

Answers use only the local corpus. Audio, public web search, model training,
managed vector infrastructure, and production deployment are outside scope.

## Ownership

| Directory | Responsibility | Depends on |
|---|---|---|
| `shared/` | corpus/index, agent/tool, memory, API lifecycle, single-path UI, common contracts | external libraries |
| `naive/` | retrieve the committed question after Send | `shared/` |
| `stream/` | typed snapshots, trigger, speculative retrieval, commit validation | `shared/` |
| `frontend/` | route homepage plus Naive, Stream, and side-by-side GUI | service HTTP/JSON/SSE contracts |
| `comparison/` | headless provisioning, replay, scoring, and reports | service HTTP/JSON/SSE contracts |

Deleting `frontend/` leaves both APIs and the comparison CLI working. Deleting
`comparison/` leaves both APIs and the GUI working. Deleting either path does not
break the other. Neither consumer imports application code.

The frontend is one browser origin: `/naive`, `/stream`, and `/compare` call
distinct same-origin proxy paths. Direct service ports remain available for
component development and the headless benchmark, not as end-user navigation.
Single-path routes discover only their selected API and retry bounded readiness
probes, so an unavailable peer cannot block an otherwise healthy product.

Conversation state belongs to the selected backend, not the frontend. The UI
reuses one opaque session ID for follow-ups and rotates it on **New chat**. In
Compare, each backend keeps a distinct session with the same visible turns; no
memory or cache state crosses implementations.

The provisioner is the only local deployment helper aware of both entrypoints. It
copies a stopped seed index before measurement; it does not run or score questions.

## Behavior contract

| Decision | Rule |
|---|---|
| Naive | Wait for Send, retrieve with the exact committed text, then answer. |
| Stream input | The side-by-side frontend and replay send changed drafts every 400 ms; the standalone UI uses a 400 ms trailing debounce. |
| Trigger | Use a bounded zero-shot trigger while text changes; allow exact-draft retrieval after 500 ms of stability. |
| Send | Freeze the final revision. No answer may start or appear before this point. |
| Reuse | Accept speculative evidence only when its recorded text exactly matches the commit. |
| Correction | Cancel or discard incompatible work and fall back to exact committed-text retrieval. |
| Fairness | Keep corpus, chunking, embeddings, search, answer model/prompt/tool, memory, and common metrics identical. |
| Isolation | Run different processes with separate Qdrant, SQLite, logs, sessions, and caches. |

The GUI keeps one snapshot request active and one replaceable latest draft so
typing stays responsive. Send aborts obsolete snapshot transport without waiting.

## Correctness safeguards

- Index sync verifies one immutable dataset snapshot and marks the index ready
  only when checksums, source, pipeline version, desired count, and physical point
  count match.
- Send is reserved before any readiness or context wait, preventing maintenance,
  cancellation, or late snapshots from changing the committed turn.
- The answer agent sees only accepted evidence. Questions, history, timestamps,
  and evidence are passed as untrusted user-role content.
- `search_local_crag` is strict, read-only, local, and limited to one dynamic call
  when the initial evidence is insufficient.
- Retrieval evidence is turn-local; only conversational text becomes memory.
- Post-answer compaction and saving share one absolute deadline. The visible
  answer does not wait for accounting to finish.

FastAPI and OpenAI work are async. Compose uses asynchronous clients to two
private Qdrant servers. Standalone and headless embedded-Qdrant work runs on a
dedicated worker instead of blocking the event loop.

## Models and index

- Answer model: `gpt-5.6-sol`, medium reasoning
- Trigger and summary: low reasoning
- Embeddings: `text-embedding-3-large`, 3,072 dimensions
- Search: cosine, 400/50-token chunks, 8 candidates, 5 answer-context chunks
- Structured agent: PydanticAI with strict local-corpus tool calling

Live evidence uses real OpenAI and isolated local Qdrant stores. Mocks are
limited to unit tests.

## Evaluation

The formal protocol is 10 sealed questions through two isolated services, one
measured pass, deterministic 70 WPM replay, changed-only 400 ms snapshots, a 5 s
post-typing dwell, and a 45 s case deadline. It remains blocked until human
dataset approval.

Both paths share latency, completion, correctness-proxy, citation, usage, and cost
metrics. Stream additionally reports trigger, speculation, evidence lead, reuse,
cancellation, and fallback diagnostics. Missing provider usage is labeled as a
cost lower bound, never priced as zero.

Gold is withheld from services and the runner. The scorer receives it only after
predictions are finalized. Human semantic review is reported separately from
automatic answer and citation proxies.

## Claim boundary

The retained five-question development run is real but non-final. It supports one
narrow claim: Stream preserved the measured correctness proxies and reduced TTFT
when intent stabilized before Send. It does not establish a universal speed,
accuracy, or cost improvement, and it does not transfer results from the paper.

## Reference

- [CRAG repository and license](https://github.com/facebookresearch/CRAG)
