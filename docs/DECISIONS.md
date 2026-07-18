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
| D01 | Path A waits for the complete committed query, then retrieves with that exact immutable text and answers. |
| D02 | Path B samples the real text box every 400 ms but sends only changed cumulative drafts before Send; it is not submit-then-replay. |
| D03 | Path B uses a zero-shot model trigger for evolving prefixes and a locked 500 ms server unchanged-text timer for deterministic exact-draft retrieval; it is not represented as the paper's post-trained policy. |
| D04 | Eligibility runs on bounded changed snapshots, never every keystroke. No trigger or pre-Send retrieval may authorize an answer before Send. |
| D05 | Keep at most one active speculative retrieval; every changed snapshot resets the unchanged timer, corrections invalidate stale work, and append-only updates coalesce. |
| D06 | Quarantine every speculative result until Send. Reuse completed literal-exact evidence, or await only literal-exact retrieval already in flight; any changed commit, mismatch, or failed exact in-flight retrieval uses the bounded exact committed-text fallback. HTTP Send acceptance is immediate/backgrounded, and no final answer starts before Send. |
| D07 | Use one canonical dataset at `data/crag_eval`: 5 dev, 10 unseen test, 250 complete documents, exactly 1,000 points. |
| D08 | Keep the dataset `candidate_pending_human_review`; final services fail closed until explicit `approved_frozen`. |
| D09 | Use `text-embedding-3-large` at 3,072 dimensions, cosine Qdrant search, 400/50-token chunking, 8 candidates, top 5 context chunks. |
| D10 | Use `gpt-5.6-sol` at medium reasoning for grounded answers; use low for the latency-sensitive trigger and summary roles. |
| D11 | Use the OpenAI default service tier, explicit async clients, `store=false`, zero Responses and embedding SDK retries, and role-specific timeouts/fallbacks. |
| D12 | Use PydanticAI for typed structured output and the strict local-corpus function; use visible `asyncio` orchestration for scheduling. |
| D13 | Give both paths the same exact committed-text fallback retriever, answer agent, prompt, memory/context policy, and scorer; Path B alone adds changed-draft triggering and settled-draft speculation. |
| D14 | Isolate formal path services, local stores, sessions, and cache namespaces so neither path warms the other. |
| D15 | Persist conversation text in SQLite; never persist retrieved bodies or speculative tool output as long-term memory. |
| D16 | Keep the one-page React/Vite client non-blocking with a bounded latest-snapshot queue, `answer.ready` as its visible-answer boundary, SSE through the run terminal event, and always-available Cancel/New turn controls. |
| D17 | Move synchronous embedded-Qdrant work to one dedicated worker so it cannot block the event loop. |
| D18 | Report paired TTFT/total deltas, automatic answer/alias proxy, exact-chunk citations, calls, cost coverage, failures, and stabilization strata; keep human semantic adjudication separate and do not cherry-pick. |
| D19 | After approval, run the final 10×2 comparison once with no dataset warm-up, deterministic 70-WPM changed-only sampling, a fixed 5 s post-typing dwell, the locked 500 ms server timer, and a 45 s per-case deadline. |
| D20 | Omit the optional native component: measured local ANN is not the dominant end-to-end latency and native code would add unjustified complexity. |

## 3. What typed input means

Typed input is the evolving content of the actual text box while the user types,
not merely a string delivered after clicking Send. The browser samples cumulative
dirty text every 400 ms, including partial words, but sends only when the normalized
draft changed. A five-second post-typing pause therefore delivers the complete
draft once at the next tick and sends no repeated unchanged snapshots. Commit
carries the complete text as a higher revision. The pause models a user who thinks
before pressing Send and gives the server's own unchanged-text timer a declared,
reproducible opportunity to move retrieval earlier. It never authorizes an answer.

The server does not call a model or retrieve on every character. Every changed
snapshot resets a locked 500 ms unchanged timer. While text is still evolving, a
snapshot becomes model-trigger eligible after five words and either three new words
or a terminal punctuation boundary, with at least 500 ms between decisions. A
four-call base budget grows modestly for long questions (at most three additional
ordinary calls plus a reserved terminal boundary). An in-flight decision absorbs
append-only snapshots and processes only the latest pending state next.

The structured model validator chooses exactly one action:

- `wait` when entity, relation, or constraints are incomplete;
- `retrieve` with a short standalone factual query when intent is stable enough;
- `keep_previous` when existing completed work still covers the draft.

This retains the paper's model-based trigger shape without post-training. If one
delivered draft stays byte-for-byte unchanged for 500 ms, the server treats that
quiet draft as settled and starts deterministic retrieval with its exact normalized
text. The retrieval remains quarantined; even a fully completed result cannot
produce an answer until Send.

## 4. Commit safety and correction handling

Every delivered changed snapshot has a monotonic revision and resets the quiet
timer. Normal append-only typing may preserve model-trigger context, while editing
existing text cancels trigger/retrieval tasks, drops prior query/evidence, and
creates a new decision boundary.

At Send, the server freezes the final revision and records commit time. HTTP Send
acceptance is immediate and answer work continues in the background. Path B
promotes completed evidence whose recorded source text literally equals the
committed text. It may also await a retrieval already in flight only when that
retrieval has the same literal source text; failure takes the bounded exact
committed-text fallback. An unfinished model decision and mismatched speculation
are cancelled. Prefix compatibility is deliberately insufficient: any appended,
deleted, corrected, or otherwise changed text takes exactly one retrieval with the
immutable commit. Path A uses the same exact committed-text retrieval after Send,
without a query-planning model call. The answer agent never sees unaccepted
evidence, and answer generation never begins before Send. Raw retrieval completion
is provisional, never a visible answer.

The runtime reserves the immutable terminal turn record under the global turn lock
before awaiting index readiness, context, or event-channel setup. Idle reaping and
index maintenance therefore observe commit setup immediately and cannot cancel or
overtake a Send that has already been admitted.

Metrics distinguish:

- accepted evidence ready before Send;
- retrieval already in flight at Send;
- explicit revalidation;
- commit fallback/retrieval;
- cancellations, stale discards, failures, and timeouts.

Candidate retrieval headroom is diagnostic; it is not automatically equal to
safe evidence lead or TTFT saved.

## 5. Shared retrieval and agent core

The corpus stores complete cleaned pages. A sync captures the checksum manifest and
every bound file once, verifies them, and chunks the exact retained corpus bytes.
Ingestion embeds title/domain plus chunk text, never query/gold metadata.
Content/model hashes enable incremental upserts and stale deletion. Sync marks
durable metadata unready before the first mutation and ready only after the source
fingerprint, desired count, checksum, and version are finalized. A failed sync
therefore stays unready across restart. Answer admission also checks approval and
checksums plus durable/in-memory readiness, source fingerprint, version, desired
count, and physical Qdrant point count. Query-vector and ranked-result caches are
bounded; ranked results are keyed by index version and path/session scope and are
cleared when sync begins.

The answer agent's privileged instructions are static. The question, query time,
rolling summary, and accepted top-five evidence are serialized into one user-role
JSON object and explicitly treated as untrusted data, so retrieved or remembered
text cannot become system instructions. Its PydanticAI function is declared
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

The OpenAI service tier is `default`. Responses and embedding clients both use zero
SDK retries so an inner transport retry cannot outlive an application deadline or
distort measured call counts. Timeouts are 4 s for a trigger decision, 6 s for
retrieval/local tool work, 30 s for the answer, 8 s for summary, one configured
absolute lease for all post-answer persistence work, and 45 s for embedding
batches. HTTP Send acceptance is immediate. The background answer path cancels an
active model trigger or mismatched retrieval, but may await a literal-exact
retrieval already in flight before bounded fallback. `answer.ready` ends visible
loading immediately after grounded generation. The event stream remains open for
`answer.completed`, which supplies final persistence status and accounting, and
closes only on `run.completed` or `run.error`. Generation failures/timeouts receive
schema-v2 failure ledgers; summary timeout and pre-usage cancellations have
explicit unpriced counters. Where provider usage is unavailable, reported cost is
a known lower bound rather than an invented zero.

Configuration is intentionally frozen once per process. `app.config` loads `.env`,
constructs the immutable singleton, and validates these benchmark locks during
import; environment changes require a restart. Components still accept explicit
`Settings` instances for tests, and the isolated benchmark services receive their
environment before their fresh process imports the application. This is deliberate
fail-fast reproducibility behavior, not support for hot-reloaded configuration.

## 7. Memory and context

SQLite stores an opaque session key, rolling conversation summary, and recent
user/assistant messages. Follow-up context reads and post-answer writes share the
same session lease, so a new turn cannot observe half-persisted history. Optional
compaction, the normal save, and any raw-save fallback all consume one absolute
post-answer deadline; cancellation or generator close cannot add a second timeout.
Persistence success is reported only when a save completes. The last four turns
stay raw; older text is summarized after the history budget is crossed. Compression occurs after the
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

The frontend samples on a fixed 400 ms clock, skips unchanged drafts, and permits
one snapshot request in flight plus one replaceable changed draft. Send aborts
obsolete snapshot transport without awaiting cleanup; answer SSE collection does
not disable Cancel or New turn. Loading ends when each requested path receives
`answer.ready` (or
`answer.error`), but the stream stays open for final `answer.completed` persistence
and cost data until `run.completed`/`run.error`.

Index sync admission is atomic with turn/commit admission. Existing turns, answer
tasks, or pending commit setup make sync return 409; once maintenance is admitted,
new turn work returns 503 rather than racing index mutation.

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

Verification and sync use the same exact-snapshot rule: the manifest is parsed once,
every checksummed file is read and hashed once, and later parsing/indexing operates
only on those captured bytes. This removes a check-then-reread window in which the
corpus could otherwise change after verification.

Question and evidence defects found during development were corrected or replaced
before this dataset was generated. That repair does not constitute human approval.
Until a reviewer explicitly freezes the status and checksums, the unseen test split
remains unavailable to the final launcher.

## 10. Evaluation contract and evidence

The formal protocol is 10 unseen questions × two isolated services × one measured
pass, no warm-up, deterministic 70-WPM typing, a fixed 5,000 ms post-typing dwell,
and a 45 s case deadline. The changed-only sampler delivers the exact full draft
once during the dwell; the locked 500 ms server timer detects that it stayed
unchanged and can start exact retrieval. Only Send commits the question and permits
answer generation. The runner has no gold path. An offline scorer later binds
predictions to the frozen full manifest and scorer-only gold.

Primary output includes:

- paired submit-to-first-token and total-time deltas, median and p95;
- automatic expected-answer/alias match and false-premise rejection proxies;
- exact cited-chunk validation and supporting/acceptable document resolution;
- input/cached/output/reasoning/embedding tokens, calls, and actual priced cost;
- completion/failure/timeout/throughput and cache/reuse/overlap/fallback state;
- early-, late-, and revision/ambiguity strata.

The retained run used all five checksum-bound dev questions and no unseen test
question. Both paths completed 5/5 with 100% automatic expected-answer/alias match,
evidence support, valid citation, supporting-document citation, and false-premise
rejection. Human semantic-adjudication coverage was 0%; these scores are automatic
proxies, not a semantic-correctness measurement. The automatic-proxy delta was
zero.

Stream won TTFT on 5/5 pairs. Median paired Stream-minus-Naive TTFT was
-2,167.428 ms (-59.1560%), paired p95 TTFT delta was -947.191 ms, and median
total-time delta was -2,374.568 ms. Naive medians were 3,663.915 ms TTFT and
4,268.152 ms total; Stream medians were 1,175.745 ms and 2,047.255 ms. The early
slice won 3/3 with median TTFT/total deltas of -2,167.428/-2,374.568 ms. The late-
stabilizing item won by -3,628.071/-3,511.659 ms, and the revision/ambiguity item
won by -863.922/-2,180.017 ms.

Naive used 5 usage-accounted model calls, 0 controller attempts, and 5 retrievals;
Stream used 17, 18, and 13. Neither path issued a dynamic function-tool call.
Observed run cost was a complete $0.05899131 for Naive and at least $0.10140119
for Stream; means were $0.011798262 and at least $0.020280238. Stream had complete
accounting on 0/5 outputs and Naive on 5/5, leaving no fully accounted pair. These
are not a final paired cost comparison. Stream reused completed exact-draft
evidence in 5/5 cases; all five were ready before commit and needed no fallback in
this run.

The manifest is `reportable: false` and `completed_non_reportable`. It records 10
completed outputs, zero failures/deadline failures, two distinct services, and
complete transport/timing/cleanup integrity gates. These are non-final development
results; summary status is `development_only_non_final`, `unseen_test_run` is
false, and the 20-run preregistered final-protocol gate is intentionally incomplete.
No test prediction has been generated.

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
> expose the extra calls/cost and possible losses on late or revised inputs.

The project does not claim the paper's speech latency, accuracy, training, AudioCRAG,
100,000-document, reranking, or model-call figures as its own.

## 12. Reproduction and honest claims

Normal reproduction uses the committed compressed dataset; the 705 MiB upstream
download is optional. The retained real five-question development A/B run took
203.743 s. The final clean reproduction took about 5 minutes 10 seconds end to end:
setup 2.01 s, checksum verification 1.36 s, tests/build about 5.4 s, both fresh
real indexes 94.83 s, benchmark 203.91 s, scoring 0.20 s, plus service startup.
This observed run is comfortably inside the provider-dependent 15–20 minute
reviewer envelope.

Before approval, `benchmark-dev-services-{check,sync,serve}` is the only supported
two-service provisioning route. It accepts exactly the candidate status, creates
isolated child state with the unreviewed flag, and leads only to
`benchmark-smoke`/`score-dev`. The final service targets never pass that flag, and
the final runner independently requires approved service/query-bundle identities.

We may claim now:

- genuine pre-Send typed snapshots and speculative local retrieval;
- raw evidence remains provisional, with safe commit validation/fallback and no
  final answer before Send;
- real development automatic-proxy parity and 5/5 observed TTFT wins under the
  declared changed-only five-second post-typing dwell, with zero human semantic
  adjudication
  and the retained lower-bound costs/calls disclosed;
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
