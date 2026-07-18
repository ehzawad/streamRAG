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
Stream may plan and retrieve from evolving typed text, then reuses, overlaps,
revalidates, or replaces that work at commit. Both wait for accepted evidence
before producing a grounded answer.

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
and embedded Qdrant. Wall time was 182.4 s.

The content-addressed predictions, run manifest, JSON summary, and rendered table
are retained under [`bench/results/dev-comparison`](../bench/results/dev-comparison)
as explicitly non-final development evidence.

| Path | Expected answer | Support + citation | Median TTFT | Median total | Model API calls | Controller calls | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 5,342 ms | 5,944 ms | 9 | 5 | 5 | ≥$0.06683183 |
| Stream | 100% | 100% | 6,276 ms | 6,913 ms | 22 | 23 | 5 | ≥$0.10177092 |

Costs are lower bounds because cancelled/timed-out calls do not return provider
usage and are not silently priced at zero. Mean observed lower-bound cost was
at least $0.013366 per Naive output and $0.020354 per Stream output. Neither path
needed a model-issued post-retrieval function call in this sample; controller and
primary retrieval calls are reported separately.

Paired results are the correct A/B comparison because the two path distributions
contain different queries at their medians:

- Stream won TTFT on 3/5 pairs (60%).
- Median paired Stream-minus-Naive TTFT: **-335.918 ms (-4.9328%)**.
- Median paired Stream-minus-Naive total time: **-357.691 ms**.
- Mean accuracy delta: **0 percentage points**.
- Stream commit fallback: 60%; in-flight post-commit overlap: 40%; accepted
  pre-Send reuse: 0% in this run.

Negative latency deltas favor StreamRAG. It is therefore possible for the paired
median to favor Stream while Stream's unpaired path median is higher; those are
different statistics, not a contradiction.

## Stabilization analysis

The preregistered candidate classes explain where the scheduling mechanism helped:

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Median paired total delta | Accuracy delta |
|---|---:|---:|---:|---:|---:|
| Early stabilization | 3 | 100% | -2,054.847 ms (-36.877%) | -2,071.687 ms | 0 pp |
| Late stabilization | 1 | 0% | +2,255.739 ms | +2,262.085 ms | 0 pp |
| Revision / ambiguity | 1 | 0% | +2,172.689 ms | +1,995.652 ms | 0 pp |

This is the intuitive result the typed adaptation should seek: a material latency
gain when enough intent arrives early, overhead when the decisive constraint comes
late, and no loss of grounded correctness. It does **not** establish that
StreamRAG is universally faster, more accurate, or cheaper.

## Relation to the Stream RAG paper

The paper's controlled sequential-RAG ablation reports comparable correctness
between post-trained sequential RAG (34.9%) and Stream RAG (34.2%). Its isolated
mechanism claim is reduced user-perceived latency while preserving correctness;
larger accuracy gains are principally relative to closed-book/no-tool systems.

This project follows that nuance. Its zero-shot typed controller is called on
bounded snapshots, not every keystroke. It predicts whether to wait, retrieve a
new query, or keep prior work; only a new accepted query starts retrieval, and at
most one speculative retrieval thread is active. The current development evidence
shows correctness parity plus query-dependent latency gains, while exposing the
extra calls and cost.

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

Before approval, `make benchmark-smoke` is limited to `dev_queries.jsonl` and
produces a permanently non-final artifact.
