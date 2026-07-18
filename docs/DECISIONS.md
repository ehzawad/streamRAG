# Typed StreamRAG implementation decisions

**Status:** implementation complete; canonical dataset awaits human approval
**Date:** 2026-07-18

This is the current architecture record for the submitted implementation.

## 1. Scope and source hierarchy

1. [`Applied_AI_Engineer_Assessment.pdf`](references/Applied_AI_Engineer_Assessment.pdf)
   defines the deliverable: the smallest defensible full-stack Naive RAG versus
   StreamRAG comparison, with a tool, memory, context management, fixed expected
   answers, and speed/cost/accuracy/performance reporting.
2. [`18954_Stream_RAG_Instant_and_A.pdf`](references/18954_Stream_RAG_Instant_and_A.pdf)
   defines the primary scheduling mechanism and explicitly says it also applies
   to typed input.
3. [`streamrag-text-implementation-relevance.txt`](references/streamrag-text-implementation-relevance.txt)
   records the typed-input transfer boundary and implementation-specific reading
   of the paper.
4. [`streaming-tool-stabilization-relevance.txt`](references/streaming-tool-stabilization-relevance.txt)
   informs the early/late/revision strata and failure analysis.

The assessment wins if any source conflicts. Audio, model training, deployment,
a managed vector service, reranking, and native code are outside the submitted
minimum.

## 2. Locked decision register

| ID | Decision |
|---|---|
| D01 | Path A waits for the complete committed query, then plans, retrieves, and answers. |
| D02 | Path B receives real cumulative typed snapshots before Send; it is not submit-then-replay. |
| D03 | Path B is a latency-first hybrid: deterministic meaningful-prefix prefetch can launch one raw retrieval concurrently with a zero-shot model validator; it is not represented as the paper's post-trained policy. |
| D04 | Eligibility runs on bounded snapshots, never every keystroke. The model validates, refines, or rejects the raw candidate, but neither heuristic eligibility nor a pre-Send decision can authorize answer evidence. |
| D05 | Keep at most one active speculative retrieval; corrections cancel/invalidate stale work and append-only updates coalesce. |
| D06 | Quarantine every speculative result until the complete-input commit gate reuses and explicitly revalidates, completes compatible in-flight work, or replaces it; never generate a final answer before Send. |
| D07 | Use one canonical dataset at `data/crag_eval`: 5 dev, 10 unseen test, 250 complete documents, exactly 1,000 points. |
| D08 | Keep the dataset `candidate_pending_human_review`; final services fail closed until explicit `approved_frozen`. |
| D09 | Use `text-embedding-3-large` at 3,072 dimensions, cosine Qdrant search, 400/50-token chunking, 8 candidates, top 5 context chunks. |
| D10 | Use `gpt-5.6-sol` at medium reasoning for grounded answers; use low for the latency-sensitive trigger and summary roles. |
| D11 | Use the OpenAI default service tier, explicit async clients, `store=false`, no hidden Responses retries, and role-specific timeouts/fallbacks. |
| D12 | Use PydanticAI for typed structured output and the strict local-corpus function; use visible `asyncio` orchestration for scheduling. |
| D13 | Give both paths the same answer agent, prompt, memory/context policy, retriever, and scorer. |
| D14 | Isolate formal path services, local stores, sessions, and cache namespaces so neither path warms the other. |
| D15 | Persist conversation text in SQLite; never persist retrieved bodies or speculative tool output as long-term memory. |
| D16 | Keep the one-page React/Vite client non-blocking with a bounded latest-snapshot queue, `answer.ready` as its terminal success boundary, and always-available Cancel/New turn controls. |
| D17 | Move synchronous embedded-Qdrant work to one dedicated worker so it cannot block the event loop. |
| D18 | Report paired TTFT/total deltas, correctness, citations, calls, cost coverage, failures, and stabilization strata; do not cherry-pick. |
| D19 | Run the final 10×2 comparison once with no dataset warm-up and a 45 s per-case deadline after approval. |
| D20 | Omit the optional native component: measured local ANN is not the dominant end-to-end latency and native code would add unjustified complexity. |

## 3. What typed input means

Typed input is the evolving content of the actual text box while the user types,
not merely a string delivered after clicking Send. The browser samples cumulative
dirty text every 400 ms. Snapshots include partial words and are sent only at ticks
strictly before Send; commit carries the complete text as a higher revision.

The server does not call a model or retrieve on every character. A snapshot becomes
eligible after five words and either three new words or a terminal punctuation
boundary, with at least 500 ms between decisions. A four-call base budget grows
modestly for long questions (at most three additional ordinary calls plus a
reserved terminal boundary). At a new eligible boundary with no reusable work, one
raw meaningful-prefix retrieval may start concurrently with the model decision. An
in-flight decision absorbs append-only snapshots and processes only the latest
pending state next.

The structured model validator chooses exactly one action:

- `wait` when entity, relation, or constraints are incomplete;
- `retrieve` with a short standalone factual query when intent is stable enough;
- `keep_previous` when existing completed work still covers the draft.

This retains the paper's model-based trigger shape without post-training, while
adding a latency-first deterministic prefetch that the model cannot directly
authorize for answering. Some eligible boundaries can therefore spend one raw
retrieval even if the model later chooses `wait`; that work is quarantined and
discarded unless later validation and the complete-input commit gate accept it.

## 4. Commit safety and correction handling

Every snapshot has a monotonic revision. Normal append-only typing may preserve
compatible work. Editing existing text cancels trigger/retrieval tasks, drops the
prior query and evidence, and creates a new decision boundary.

At Send, the server freezes the final revision and records commit time before
awaiting any work. The complete-input controller produces the canonical retrieval
query. Speculative evidence can be promoted only when its revision/text/query is
compatible or the complete-input controller explicitly revalidates it. Otherwise
Path B performs the same complete-query retrieval required by Path A. The answer
agent never sees unaccepted evidence, and answer generation never begins before
Send. Raw retrieval completion is therefore provisional work, not a user-visible
answer or automatically safe grounding.

Metrics distinguish:

- accepted evidence ready before Send;
- retrieval already in flight at Send;
- explicit revalidation;
- commit fallback/retrieval;
- cancellations, stale discards, failures, and timeouts.

Candidate retrieval headroom is diagnostic; it is not automatically equal to
safe evidence lead or TTFT saved.

## 5. Shared retrieval and agent core

The corpus stores complete cleaned pages. Ingestion embeds title/domain plus chunk
text, never query/gold metadata. Content/model hashes enable incremental upserts,
stale deletion, and a durable index version. Query-vector and ranked-result caches
are bounded and keyed by index version and path/session scope.

The answer agent receives accepted top-five evidence, query time, rolling summary,
and recent turns. Its PydanticAI function is declared
`search_local_crag(..., strict=True)`, searches only the local Qdrant corpus, and
may be called at most once if primary evidence is insufficient. Parallel function
calls are disabled. Public web search is not part of either RAG path.

PydanticAI supplies typed schema validation and usage accounting; it does not own
the Stream scheduler. The latter remains ordinary auditable async code.

## 6. Model roles and deadlines

| Role | Locked configuration | Reason |
|---|---|---|
| Grounded answer/tool loop | `gpt-5.6-sol`, medium reasoning, low verbosity | quality/latency balance for the user-visible answer |
| Typed trigger/controller | `gpt-5.6-sol`, low reasoning, max 120 output tokens | small latency-sensitive structured decision |
| Conversation summary | `gpt-5.6-sol`, low reasoning | infrequent compression task |
| Embedding | `text-embedding-3-large`, 3,072 dimensions | highest-quality locked dense representation |

The OpenAI service tier is `default`. Responses roles use zero SDK retries so an
inner transport retry cannot outlive the application's measured deadline; the
embedding client retains at most one retry. Timeouts are 4 s for a trigger
decision, 6 s for retrieval/local tool work, 30 s for the answer, 8 s for summary,
10 s for post-answer persistence, and 45 s for embedding batches. Answer delivery
uses `answer.ready` as the user-visible terminal event immediately after grounded
generation and before bounded persistence/summary maintenance. The later
`answer.completed` event supplies final persistence status and accounting, so
maintenance failure is observable without rewriting or holding open a successful
answer. Failures remain visible and unpriced calls make cost a lower bound rather
than an invented zero.

Configuration is intentionally frozen once per process. `app.config` loads `.env`,
constructs the immutable singleton, and validates these benchmark locks during
import; environment changes require a restart. Components still accept explicit
`Settings` instances for tests, and the isolated benchmark services receive their
environment before their fresh process imports the application. This is deliberate
fail-fast reproducibility behavior, not support for hot-reloaded configuration.

## 7. Memory and context

SQLite stores an opaque session key, rolling conversation summary, and recent
user/assistant messages. Follow-up context reads and post-answer writes share the
same session lease, so a new turn cannot observe half-persisted history. If optional
compaction fails, the completed turn is durably saved in its uncompressed form
before the failure is reported. The last four turns stay raw; older text is
summarized after the history budget is crossed. Compression occurs after the
current answer so it does not delay that answer's TTFT.

Retrieved documents, speculative queries/evidence, and tool bodies remain turn-
local. Idle turns are reaped after 120 s, sessions after 24 h, and terminal event
streams after five minutes. This is appropriate for a local reviewer instance;
production would replace SQLite/local Qdrant with multi-tenant external services.

## 8. Non-blocking full-stack behavior

FastAPI routes and OpenAI/PydanticAI calls are async. The local Qdrant client is
synchronous, so one application-owned `ThreadPoolExecutor` serializes its calls
off the event loop. Remote Qdrant retains native async behavior. Dataset file
hashing/chunk loading and metrics-log writes are also offloaded.

The frontend permits one snapshot request in flight and one replaceable pending
latest snapshot. Send aborts obsolete snapshot transport; answer SSE collection
does not disable Cancel or New turn. Index sync takes a maintenance lock and live
work receives a clear 503 instead of racing index mutation. Loading ends when each
requested path receives `answer.ready` (or `answer.error`); `answer.completed` is
accounting and maintenance telemetry rather than a second user-visible completion.

The real local worker probe used 256 ANN/payload reads at client concurrency 32
over 1,000 3,072-dimensional points: zero errors, p95 request latency 93.449 ms,
p95 event-loop lag 1.158 ms, and max event-loop lag 1.298 ms. This supports the
local non-blocking claim, not unlimited production concurrency.

Direct development and Docker Compose publish only on `127.0.0.1`. The API has no
authentication, authorization, rate limit, or tenant isolation, and CORS is not a
security boundary. It must not be exposed to another interface without the controls
listed in [`SECURITY.md`](SECURITY.md).

## 9. Dataset and freeze boundary

The canonical corpus contains 15 manually audited evidence pages and 235 complete
distractors. All 250 pages remain complete after markup/script/style cleaning and
chunk to exactly 1,000 points. Test answers live only in scorer-side
`test_gold.jsonl`; retrievable rows expose no answer/query/split wrapper fields.

Selection is an explicit manual mapping validated against the pinned official
source checksum and evidence phrases. It never reads path outputs, latency, cost,
or retrieval rank. The review sheet covers wording, aliases, timestamps, evidence,
split role, and low-confidence stabilization label for every question.

Question and evidence defects found during development were corrected or replaced
before this dataset was generated. That repair does not constitute human approval.
Until a reviewer explicitly freezes the status and checksums, the unseen test split
remains unavailable to the final launcher.

## 10. Evaluation contract and evidence

The formal protocol is 10 unseen questions × two isolated services × one measured
pass, no warm-up, deterministic 70-WPM typing, and a 45 s case deadline. The runner
has no gold path. An offline scorer later binds predictions to the frozen full
manifest and scorer-only gold.

Primary output includes:

- paired submit-to-first-token and total-time deltas, median and p95;
- expected-answer/alias and false-premise rejection;
- supporting/acceptable document citation resolution;
- input/cached/output/reasoning/embedding tokens, calls, and actual priced cost;
- completion/failure/timeout/throughput and cache/reuse/overlap/fallback state;
- early-, late-, and revision/ambiguity strata.

The retained run used all five checksum-bound dev questions and no unseen test
question. Both paths completed 5/5 with 100% automatic expected-answer, evidence-
support, supporting-citation, and false-premise correctness. Stream won TTFT on
4/5 pairs; median paired Stream-minus-Naive TTFT was -628.907 ms (-12.0846%),
paired p95 TTFT delta was +275.880 ms because one tail pair was slower, and median
total-time delta was -702.294 ms. The early slice won 3/3 at -3,104.281 ms median
TTFT, the one late item lost at +464.497 ms, and the revision/ambiguity item won at
-478.590 ms. All accuracy deltas were zero: scheduling produced no accuracy gain
on these already-correct outputs.

Naive medians were 5,906.720 ms TTFT and 6,290.444 ms total; Stream medians were
4,575.290 ms and 5,083.832 ms. Stream used 20 model API calls, 23 controller
calls, and 12 retrievals versus Naive's 7, 5, and 5. Neither path issued a dynamic
function-tool call. Observed costs were at least $0.11416807 and $0.06137975,
respectively. They are lower bounds: complete accounting covered 0/5 Stream and
2/5 Naive outputs, leaving zero complete cost pairs, so no paired cost delta is
available. All accepted retrieval lead-at-commit values were zero; 20% reused
provisional work after commit revalidation, 40% completed compatible work after
commit, and 40% fell back at commit. The one ultimately reused raw candidate had
2,226.851 ms of provisional headroom. These facts prevent attributing every
observed latency delta to safe pre-Send evidence.

The manifest is `reportable: false` and `completed_non_reportable`. It records 10
completed outputs, zero failures/deadline failures, two distinct services, and
complete transport/timing/cleanup integrity gates. These are non-final development
results; no test prediction has been generated.

## 11. Paper interpretation

The Stream RAG paper's sequential-RAG ablation reports 34.9% for post-trained
sequential RAG and 34.2% for Stream RAG: comparable correctness, not a systematic
accuracy gain from scheduling alone. Its central isolated benefit is lower
user-perceived latency while useful tool work overlaps incoming input. Accuracy
improvements elsewhere are primarily against closed-book/no-tool baselines.

For typed text, the same benefit exists only when the evidence query becomes
determinate before Send. Late constraints, comparisons, negation, or revisions can
erase the head start and add controller overhead. Therefore the correct target is:

> preserve grounded correctness, improve latency on early-stabilizing inputs, and
> expose the extra calls/cost and losses on late or revised inputs.

The project does not claim the paper's speech latency, accuracy, training, AudioCRAG,
100,000-document, reranking, or model-call figures as its own.

## 12. Reproduction and honest claims

Normal reproduction uses the committed compressed dataset; the 705 MiB upstream
download is optional. A clean real index took 40.94 s and the retained real
five-question development A/B run took 180.866 s. After approval, the bounded
20-case final runner is designed to keep the full normal workflow around 15–20
minutes on a normal connection and responsive provider.

Before approval, `benchmark-dev-services-{check,sync,serve}` is the only supported
two-service provisioning route. It accepts exactly the candidate status, creates
isolated child state with the unreviewed flag, and leads only to
`benchmark-smoke`/`score-dev`. The final service targets never pass that flag, and
the final runner independently requires approved service/query-bundle identities.

We may claim now:

- genuine pre-Send typed snapshots and speculative local retrieval;
- raw evidence remains provisional, with safe commit validation/fallback and no
  final answer before Send;
- real development correctness parity and observed query-dependent latency
  differences, with the retained lower-bound costs/calls disclosed;
- a responsive event loop under the measured local Qdrant probe.

We may not claim now:

- dataset approval or any unseen-test/final benchmark result;
- universal StreamRAG speed, accuracy, or cost improvement;
- an exact reproduction of the paper's trained trigger or speech system;
- production-scale concurrency from embedded Qdrant/SQLite;
- a full CRAG benchmark result.

## 13. External references

- CRAG repository and dataset licensing: <https://github.com/facebookresearch/CRAG>
- OpenAI GPT-5.6 Sol model: <https://developers.openai.com/api/docs/models/gpt-5.6-sol>
- OpenAI `text-embedding-3-large` model: <https://developers.openai.com/api/docs/models/text-embedding-3-large>
