# Naive RAG vs typed StreamRAG benchmark report

**Status: development evidence only; final unseen benchmark not run.**

The canonical dataset is still `candidate_pending_human_review`. The formal
launcher refuses the test split until it is `approved_frozen`, so the tables below
are real OpenAI/Qdrant measurements on the five checksum-bound development
questions, not a final assessment result.

## Experimental contract

Both paths use:

- the same 250 complete documents and exactly 1,000 Qdrant points;
- `text-embedding-3-large`, 3,072 dimensions, the same chunker/search/top-k;
- `gpt-5.6-sol`: medium reasoning for the grounded answer and low for trigger and
  summary roles;
- the same strict PydanticAI `search_local_crag` function, prompt, context budget,
  memory policy, and scorer;
- deterministic 70-WPM cumulative drafts sampled every 400 ms but sent only when
  changed, strictly before Send, with a fixed 5,000 ms post-typing dwell and locked
  500 ms server unchanged-text timer.

The privileged answer instructions are static. Question text, query time,
conversation summary, and pre-retrieved evidence are carried together in one
user-role JSON object and treated as untrusted context. Both Responses and
embedding clients use zero SDK retries; timeouts, provider failures, pre-usage
cancellations, local-tool gaps, and summary-timeout gaps are recorded explicitly,
so incomplete usage produces a lower bound instead of a false zero.

Both paths share the exact committed-text post-Send fallback, answer model, prompt,
corpus, embedding, and ANN policy. Naive always uses that fallback after commit.
Stream's treatment adds a pre-Send model trigger for evolving prefixes. Separately,
when one delivered draft remains unchanged for 500 ms, the server starts
deterministic retrieval with that exact draft text. Raw results remain provisional.
At Send, Stream reuses completed evidence whose source text literally equals the
commit. It may await only a literal-exact retrieval already in flight, then takes
the bounded exact-commit fallback if that retrieval fails. Any appended, deleted,
or corrected text uses the same fallback as Naive; unfinished model decisions and
mismatched speculation are cancelled. HTTP Send acceptance is immediate and the
answer path continues in the background. Both paths start final answer generation
only after commit and only with accepted evidence; neither emits a provisional
answer before Send.

`answer.ready` is the user-visible completion boundary: it carries the grounded
answer, sources, TTFT, and answer-generation total as soon as generation finishes.
The UI does not wait for bounded post-answer persistence. The later
`answer.completed` event adds persistence status and final accounting for the
benchmark record without changing the already delivered answer. SSE remains open
until `run.completed` or `run.error`, so that terminal telemetry is not lost.

Reportable A/B runs use two backend processes with different instance IDs,
Qdrant directories, SQLite databases, metrics logs, session/cache scopes, and no
shared warm-up. Their source, dataset, model, embedding, retrieval, and index
fingerprints must match. Query order is counterbalanced, and the runner executes
paths sequentially to avoid local CPU/network contention.

Each service indexes one checksum-verified snapshot: manifest and files are read
once, and chunking uses the captured corpus bytes. Sync marks durable metadata
unready before mutation and ready only after source/version/count metadata is
finalized. Benchmark admission requires that metadata, the current corpus
fingerprint, and the physical Qdrant point count agree.

The text replay excludes ASR, endpoint detection, TTS, and speech trailing silence.
Its declared five-second dwell represents a user pausing after finishing the text
but before pressing Send. The changed-only sampler delivers the exact complete
draft once near the beginning of that pause and does not resend unchanged text; the
server's 500 ms timer observes the quiet draft. Answers remain blocked until Send.
Submit-to-first-token (TTFT) is the primary user-perceived speed metric; pre-Send
headroom is diagnostic and is never relabeled as latency saved.

## Real development comparison

Configuration: five development questions × two isolated paths, no warm-up, one
measured pass, deterministic 70-WPM typing, fixed 5,000 ms post-typing dwell, real
OpenAI calls, real embeddings, and embedded Qdrant. Wall time was 203.7432 s.

The content-addressed predictions, run manifest, JSON summary, and rendered table
are retained under [`bench/results/dev-comparison`](../bench/results/dev-comparison)
as explicitly non-final development evidence.

| Path | Automatic answer/alias proxy | Support + valid citation | Median TTFT | Median total | Usage-accounted model calls | Controller attempts | Retrievals | Observed run cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 3,663.915 ms | 4,268.152 ms | 5 | 0 | 5 | $0.05899131 (complete) |
| Stream | 100% | 100% | 1,175.745 ms | 2,047.255 ms | 17 | 18 | 13 | ≥$0.10140119 (lower bound) |

Expected-answer/alias match, evidence support, citation marker, exact cited-chunk
validity, supporting-document citation, and false-premise rejection were all 100%
for both paths across applicable outputs. Optional human semantic-adjudication
coverage is 0%. These are automatic proxies, not a claim that semantic correctness
was measured at 100%.

Naive cost accounting was complete for 5/5 outputs. Stream accounting was complete
for 0/5; five cancelled controllers and one timed-out controller did not all return
provider usage. Its observed total $0.10140119 and mean $0.020280238 are therefore
known lower bounds; Naive's complete total was $0.05899131 and mean $0.011798262.
No pair had complete accounting on both sides, so no final paired cost comparison
is claimed.
Unknown usage is never silently priced at zero. Neither path needed a
model-issued `search_local_crag` call after primary retrieval; controller and
retrieval calls are reported separately. “Usage-accounted” counts calls for which
the provider returned usage; controller attempts are counted independently.

Paired results are the correct A/B comparison because the two path distributions
contain different queries at their medians:

- Stream won TTFT on 5/5 pairs (100%).
- Median paired Stream-minus-Naive TTFT: **-2,167.428 ms (-59.1560%)**.
- Paired p95 Stream-minus-Naive TTFT: **-947.191 ms**; all five pairs were faster.
- Median paired Stream-minus-Naive total time: **-2,374.568 ms**.
- Mean automatic answer/alias proxy delta: **0 percentage points**.
- Stream reused completed exact-draft evidence in 5/5 cases (100%). All five were
  ready before commit; post-commit overlap and exact-text fallback were both 0%.

Negative latency deltas favor StreamRAG. These are five development pairs under
live provider variance, not a causal estimate or a final benchmark. The manifest
is finalized as `completed_non_reportable`, `reportable: false`, with zero failures,
zero deadline failures, complete snapshot transport/cleanup gates, and a maximum
typing drift of 1.327 ms.

The summary status is `development_only_non_final` and `unseen_test_run` is false.
The preregistered final-protocol gate is intentionally incomplete here: this smoke
run has 5 development questions and 10 total path runs, not the required 10 unseen
questions and 20 path runs.

## Stabilization analysis

The preregistered candidate classes explain where the scheduling mechanism helped:

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Median paired total delta | Automatic-proxy delta |
|---|---:|---:|---:|---:|---:|
| Early stabilization | 3 | 100% | -2,167.428 ms (-59.1560%) | -2,374.568 ms | 0 pp |
| Late stabilization | 1 | 100% | -3,628.071 ms (-80.5365%) | -3,511.659 ms | 0 pp |
| Revision / ambiguity | 1 | 100% | -863.922 ms (-48.0917%) | -2,180.017 ms | 0 pp |

All five items were faster on TTFT. These tiny strata describe one live run; they
do not prove that the scheduling mechanism caused each delta or that every rerun
will preserve the ordering. The defensible result is automatic-proxy parity and
5/5 observed development TTFT wins under the declared changed-only dwell,
alongside higher work and higher lower-bound cost—not a claim that StreamRAG is
universally faster, more accurate, or cheaper.

## Relation to the Stream RAG paper

The paper's controlled sequential-RAG ablation reports comparable correctness
between post-trained sequential RAG (34.9%) and Stream RAG (34.2%). Its isolated
mechanism claim is reduced user-perceived latency while preserving correctness;
larger accuracy gains are principally relative to closed-book/no-tool systems.

This project follows that nuance. Its zero-shot typed controller is called on
bounded changed snapshots, not every keystroke. Evolving prefixes use that model
trigger; a separate locked 500 ms server timer starts exact retrieval only after a
delivered draft stays unchanged. At most one speculative retrieval is active. At
commit, Stream reuses completed literal-text evidence or awaits only a literal-
exact retrieval already in flight; failure or any text change takes the bounded
exact committed-text retrieval. The current development evidence
shows automatic-proxy parity and five of five TTFT wins while exposing extra
calls/retrievals and lower-bound cost.

## Frozen-test protocol (pending approval)

The final contract is 10 unseen questions × two isolated paths × one measured
pass = 20 path runs. There is no full-dataset warm-up. Replay is locked to 70 WPM
with changed-only 400 ms sampling, the same 5,000 ms post-typing dwell, and the
500 ms server unchanged timer before Send. Each case has a 45 s deadline
covering typed replay, commit, and answer collection; a timeout is a counted
failure. The sequential case bound is 15 minutes, and verification, index/report
overhead keeps the intended full reproduction at roughly 15–20 minutes under
normal package/network/provider conditions. The completed five-question clean
development reproduction took about 5 minutes 10 seconds end to end.

Final scoring requires:

- exactly one output for every query/path pair and zero uncounted failures;
- matching frozen query, corpus, config, prompt, service, and index fingerprints;
- planned versus actual Send drift within 100 ms and successful bounded cleanup;
- automatic expected-answer/alias match, false-premise rejection, an exact cited
  chunk that exists in the prediction's sources, and resolution to frozen
  supporting/acceptable document IDs;
- TTFT/total medians and p95, paired per-query deltas, calls/tokens/cost coverage,
  throughput, failures, fallback/reuse/overlap, and stabilization strata;
- honest lower-bound labeling whenever provider accounting is incomplete.

The optional four-class human semantic adjudication (`perfect`, `acceptable`,
`missing`, `incorrect`) is separate from the automatic proxy. Each label must carry
the exact `prediction_sha256` of the reviewed raw prediction row; a stale hash is
rejected rather than silently applied to changed output.

## Reproduce the non-final development comparison

The candidate-mode launcher provisions the same two-process isolation without
weakening the final approval gate:

```bash
make benchmark-dev-services-check
make benchmark-dev-services-sync
```

`check` only validates the candidate manifest and isolated paths. `sync` makes
real embedding calls for two separate Qdrant indexes but executes no question.
Keep the services open in terminal A, then run and score the dev-only artifact in
terminal B:

```bash
# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

The launcher accepts only `candidate_pending_human_review`, passes the unreviewed
override only to its child APIs, and reports `reportable: false`. The smoke runner
accepts only the adjacent checksummed `dev_queries.jsonl`, forces one repetition
and no warm-up, and refuses unseen test inputs. Pointing the ordinary final runner
at these services still fails because their status is not `approved_frozen`.

## Reproduction after approval

The following commands intentionally fail while the candidate remains unapproved:

```bash
make benchmark-inference-bundle
make benchmark-services-check
make benchmark-services-sync
```

Keep both isolated APIs running in terminal A:

```bash
make benchmark-services-serve
```

Then in terminal B:

```bash
make benchmark
make score
```

The inference-bundle builder exposes queries and documents but never opens or
hashes `test_gold.jsonl`. The runner receives no gold path. The offline scorer
later verifies the adjacent run manifest, exact key set, service/session binding,
full frozen manifest, and scorer-only gold before producing a final summary.

These final commands never enable the candidate override.
