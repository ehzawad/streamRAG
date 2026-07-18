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
- deterministic cumulative dirty-text snapshots at 70 WPM and 400 ms ticks
  strictly before Send.

Only retrieval scheduling differs. Naive starts after the complete query commits.
Stream applies deterministic eligibility to a meaningful completed prefix and may
start one raw candidate retrieval concurrently with its model controller. The
model validates, refines, or rejects that query, but raw results remain provisional.
At Send, the complete-input gate reuses and revalidates, finishes compatible
in-flight work, or replaces the candidate. Both paths start final answer generation
only after commit and only with accepted evidence; neither emits a provisional
answer before Send.

`answer.ready` is the user-visible completion boundary: it carries the grounded
answer, sources, TTFT, and answer-generation total as soon as generation finishes.
The UI does not wait for bounded post-answer persistence. The later
`answer.completed` event adds persistence status and final accounting for the
benchmark record without changing the already delivered answer.

Reportable A/B runs use two backend processes with different instance IDs,
Qdrant directories, SQLite databases, metrics logs, session/cache scopes, and no
shared warm-up. Their source, dataset, model, embedding, retrieval, and index
fingerprints must match. Query order is counterbalanced, and the runner executes
paths sequentially to avoid local CPU/network contention.

The text replay excludes ASR, endpoint detection, TTS, and speech trailing silence.
Submit-to-first-token (TTFT) is the primary user-perceived speed metric; pre-Send
headroom is diagnostic and is never relabeled as latency saved.

## Real development comparison

Configuration: five development questions × two isolated paths, no warm-up, one
measured pass, deterministic 70-WPM typing, real OpenAI calls, real embeddings,
and embedded Qdrant. Wall time was 191.6624 s.

The content-addressed predictions, run manifest, JSON summary, and rendered table
are retained under [`bench/results/dev-comparison`](../bench/results/dev-comparison)
as explicitly non-final development evidence.

| Path | Expected answer | Support + citation | Median TTFT | Median total | Model API calls | Controller calls | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 6,199.342 ms | 6,846.266 ms | 8 | 5 | 5 | ≥$0.06640001 |
| Stream | 100% | 100% | 4,942.155 ms | 5,662.263 ms | 19 | 21 | 12 | ≥$0.10848677 |

Expected-answer, evidence-support, citation-marker, supporting-document citation,
and false-premise checks were all 100% for both paths across the applicable
outputs; optional manual-adjudication coverage is 0%. This is correctness parity,
not an accuracy improvement. Costs are lower bounds because cancelled, failed, or
timed-out calls do not always return provider usage and are not silently priced at
zero. Complete accounting was available for 3/5 Naive outputs and 0/5 Stream
outputs, leaving zero complete cost pairs, so no paired cost delta is claimed.
Mean observed lower-bound cost was at least $0.013280002 per Naive output and
$0.021697354 per Stream output. Neither path needed a model-issued
`search_local_crag` call after primary retrieval; controller and retrieval calls
are reported separately.

Paired results are the correct A/B comparison because the two path distributions
contain different queries at their medians:

- Stream won TTFT on 5/5 pairs (100%).
- Median paired Stream-minus-Naive TTFT: **-3,090.101 ms (-33.7579%)**.
- Paired p95 Stream-minus-Naive TTFT: **-1,178.695 ms**; all five pairs were faster.
- Median paired Stream-minus-Naive total time: **-1,251.090 ms**.
- Mean accuracy delta: **0 percentage points**.
- Stream commit fallback: 60%; compatible in-flight work completed after commit:
  20%; provisional work reused after commit revalidation: 20%.
- Accepted retrieval lead at commit: 0 ms on all five Stream cases. The one
  ultimately reused provisional candidate had 1,944.726 ms of candidate headroom,
  but provisional headroom is not accepted evidence lead or measured latency saved.

Negative latency deltas favor StreamRAG. These are five development pairs under
live provider variance, not a causal estimate or a final benchmark. The manifest
is finalized as `completed_non_reportable`, `reportable: false`, with zero failures,
zero deadline failures, complete snapshot transport/cleanup gates, and a maximum
typing drift of 2.269 ms.

## Stabilization analysis

The preregistered candidate classes explain where the scheduling mechanism helped:

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Median paired total delta | Accuracy delta |
|---|---:|---:|---:|---:|---:|
| Early stabilization | 3 | 100% | -3,128.927 ms (-33.7579%) | -1,251.090 ms | 0 pp |
| Late stabilization | 1 | 100% | -1,159.071 ms (-18.9623%) | -1,153.976 ms | 0 pp |
| Revision / ambiguity | 1 | 100% | -3,090.101 ms (-39.0957%) | -2,655.304 ms | 0 pp |

All three early-labeled items, the one late item, and the one revision/ambiguity
item were faster. With accepted pre-Send lead equal to zero, these tiny
strata describe one live run; they do not prove that the scheduling mechanism
caused each delta or that every rerun will preserve the ordering. The defensible
result is correctness parity and 5/5 observed development TTFT wins alongside
higher work and higher lower-bound cost—not a claim that StreamRAG is
universally faster, more accurate, or cheaper.

## Relation to the Stream RAG paper

The paper's controlled sequential-RAG ablation reports comparable correctness
between post-trained sequential RAG (34.9%) and Stream RAG (34.2%). Its isolated
mechanism claim is reduced user-perceived latency while preserving correctness;
larger accuracy gains are principally relative to closed-book/no-tool systems.

This project follows that nuance. Its zero-shot typed controller is called on
bounded snapshots, not every keystroke. Deterministic meaningful-prefix eligibility
may start one quarantined raw retrieval concurrently; the model predicts whether
to wait, retrieve/refine, or keep that work, while final acceptance remains a
complete-input commit-time decision. At most one speculative retrieval thread is
active. The current development evidence shows correctness parity and observed
favorable median latency with all five TTFT wins while exposing extra calls,
retrievals, and lower-bound cost.

## Frozen-test protocol (pending approval)

The final contract is 10 unseen questions × two isolated paths × one measured
pass = 20 path runs. There is no full-dataset warm-up. Each case has a 45 s
deadline covering typed replay, commit, and answer collection; a timeout is a
counted failure. The sequential case bound is 15 minutes, and verification,
index/report overhead keeps the intended full reproduction at roughly 15–20
minutes under normal package/network/provider conditions.

Final scoring requires:

- exactly one output for every query/path pair and zero uncounted failures;
- matching frozen query, corpus, config, prompt, service, and index fingerprints;
- planned versus actual Send drift within 100 ms and successful bounded cleanup;
- expected-answer/alias score, false-premise rejection, and a citation that
  resolves to frozen supporting/acceptable document IDs;
- TTFT/total medians and p95, paired per-query deltas, calls/tokens/cost coverage,
  throughput, failures, fallback/reuse/overlap, and stabilization strata;
- honest lower-bound labeling whenever provider accounting is incomplete.

The optional four-class human adjudication (`perfect`, `acceptable`, `missing`,
`incorrect`) is separate from the complete automatic score and is content-addressed
when supplied.

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
