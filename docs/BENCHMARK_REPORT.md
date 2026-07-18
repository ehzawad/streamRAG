# Naive RAG vs typed StreamRAG benchmark

**Status:** development evidence only; final unseen benchmark not run.

The canonical dataset remains `candidate_pending_human_review`. Final commands
refuse the test split until it is `approved_frozen`. Any development result is
permanently non-reportable and cannot approve either the dataset or implementation.

## Isolation contract

The benchmark is an external client of two full-stack services:

| Service | Endpoint | Owned state |
|---|---|---|
| Naive | `naive.api:app`, port 8001 | Naive Qdrant, SQLite, metrics, sessions, caches |
| Stream | `stream.api:app`, port 8002 | Stream Qdrant, SQLite, metrics, sessions, caches |

Neither service contains the other implementation. The comparison runner imports
no application package and selects no in-process path; it uses HTTP/JSON/SSE only.
It validates the role, capability, metric-contract version, source/config/dataset
fingerprints, index readiness, and distinct instance/state identities before a run.

Both services use the same 250 complete documents, 1,000 points, chunker,
`text-embedding-3-large` configuration, top-k, answer model/prompt/tool, memory,
and scoring rules. Provisioning embeds one temporary seed index and copies it only
after shutdown and quiescence into two isolated stores. No mutable index, runtime
database, cache, or session is shared during measurement.

The runner counterbalances query order and executes the paths sequentially to
avoid local CPU/network contention. Its code path is not provided test gold and
does not read it. The offline scorer receives gold only after predictions and
their manifest are finalized.

## Treatment difference

Naive starts exact committed-text retrieval after Send. Stream receives changed
cumulative drafts sampled every 400 ms, runs its bounded trigger while the draft
evolves, and may start exact retrieval after a delivered draft stays unchanged for
500 ms. The fixed 5 s post-typing dwell gives that settled-draft mechanism a
declared opportunity to finish work before Send; unchanged text is not resent.

Speculative evidence remains private. Stream can reuse it only when its recorded
source text literally equals the commit, or await already-running literal-exact
work. Any correction, mismatch, or failed candidate takes the same exact-commit
fallback as Naive. Both paths begin grounded answer generation only after Send.

This text protocol excludes ASR, endpoint detection, TTS, and trailing-silence
gains. Submit-to-first-token (TTFT) is the primary perceived-latency metric;
pre-Send retrieval lead is diagnostic and is not automatically called latency
saved.

## Metric ownership

| Compared on both paths | Stream-specific diagnostics |
|---|---|
| submit-to-first-token and total response latency | trigger attempts, decisions, failures, and timeouts |
| completed/failed/timed-out outputs and throughput | speculative and settled-draft retrievals |
| expected-answer/alias and false-premise proxies | candidate/evidence lead at Send |
| retrieved support, exact cited-chunk validity, supporting-document citation | evidence reuse, revalidation, and in-flight overlap |
| answer, summary, tool, embedding, and retrieval calls/tokens | stale discard and trigger/retrieval cancellation |
| accounting coverage and observed provider cost | exact-commit fallback after speculation |

Stream-only fields are retained in its raw record and summarized separately. They
are never filled with invented Naive values or used as common accuracy metrics.
Automatic answer/alias checks are proxies; optional human semantic adjudication is
bound to the exact prediction hash and reported separately.

Unknown provider usage is not priced as zero. Cancelled or timed-out calls without
usage make observed cost a lower bound, and a paired cost delta is reported only
when both outputs have complete accounting.

## Current development artifact

The retained artifact under
[`comparison/benchmark/results/dev-comparison`](../comparison/benchmark/results/dev-comparison)
contains five development questions × two paths, one measured pass, deterministic
70 WPM input, and the fixed 5 s dwell. It records real OpenAI calls, real embeddings,
and local Qdrant; no live dependency was mocked.

The regenerated run completed all 5 development questions and all 10 path runs
with zero path-output failures. Its integrity status is `complete`: timing drift,
snapshot transport, deadlines, turn cleanup, and prediction-count gates all
passed. The shell-measured runner wall time was 208.13 seconds; the finalized
manifest interval was 207.796 seconds.

Its automatic results were:

| Path | Automatic answer/alias proxy | Support + valid citation | Median TTFT | Median total | Usage-accounted model calls | Controller attempts | Retrievals | Observed cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Naive | 100% | 100% | 3,040.524 ms | 3,793.676 ms | 5 | 0 | 5 | $0.05533131 complete |
| Stream | 100% | 100% | 1,338.771 ms | 2,114.917 ms | 14 | 17 | 13 | at least $0.08950950 |

Stream won TTFT on 5/5 pairs. Median paired Stream-minus-Naive TTFT was
-1,480.697 ms (-52.146%), paired p95 TTFT delta was -960.058 ms, and median
total-time delta was -1,678.759 ms. Every observed stabilization stratum favored
Stream TTFT, including the one `late_stabilization` question. Automatic-proxy
accuracy and retrieved-support deltas were both zero because both paths scored
100%. Stream reused speculative evidence in 5/5 cases, with a median evidence
lead at Send of 3,053.377 ms.

These five pairs are not a causal estimate or a universal speedup. Human semantic
adjudication coverage was 0%, so 100% here means the fixed automatic answer/alias
and support/citation proxies—not adjudicated semantic correctness. Stream did
more work: 17 controller attempts and 13 retrievals versus Naive's 5 retrievals,
and 14 versus 5 usage-accounted model calls. Five cancelled controller calls, one
cancelled retrieval, two controller timeouts, and one controller failure lacked
complete provider usage. Stream's cost is therefore a lower bound, the paired
cost-complete rate is 0%, and no valid cost delta exists.

The prediction manifest binds this development evidence to its recorded source,
configuration, corpus, service, query, and prediction hashes. It reports distinct
Naive and Stream backend instances and the expected capabilities, and is finalized
as `completed_non_reportable` with `reportable: false`. This does not turn a
development smoke run into final evaluation evidence.

## Development reproduction

```bash
make verify-data
make check
make benchmark-dev-services-check
make benchmark-dev-services-sync

# terminal A
make benchmark-dev-services-serve

# terminal B
make benchmark-smoke
make score-dev
```

The sync command uses the real embedding API once to create a quiescent seed, then
clones it into the two service-owned stores. The runner uses only
`data/crag_eval/dev_queries.jsonl`; the dev scorer marks the output non-reportable.
For this regeneration, complete seed provisioning took 48.28 seconds and produced
1,000 points from the 250-document corpus. The benchmark runner took 208.13 seconds
by shell wall clock; its finalized manifest interval was 207.796 seconds.

## Final protocol after human approval

After `approved_frozen` and checksum review:

1. create the redacted inference bundle;
2. check, sync, and start the two isolated final services;
3. run the same 10 sealed questions once through each path;
4. stop the services and score the finalized predictions offline;
5. require zero uncounted failures and label any incomplete cost accounting as a
   lower bound;
6. add hash-bound human semantic adjudication before calling semantic accuracy
   final.

```bash
make benchmark-inference-bundle
make benchmark-services-check
make benchmark-services-sync

# terminal A
make benchmark-services-serve

# terminal B
make benchmark
make score
```

The formal runner requires exactly 10 questions and 20 path outputs. It refuses
candidate data, a bundle containing gold, role/capability mismatch, shared service
identity, fingerprint drift, or incomplete run-integrity gates.

## Interpretation

The Stream RAG paper's relevant claim is moving useful retrieval work into input
time while preserving sequential-RAG correctness. A scheduling treatment does not
guarantee an accuracy gain. For typed text, an advantage is plausible only when
the evidence query stabilizes before Send; late constraints or revisions can erase
the head start and add controller work.

The appropriate conclusion is therefore conditional: measure whether Stream
preserves grounded correctness and improves perceived latency, while exposing any
extra calls, cost, fallbacks, and regressions. Do not claim the paper's speech,
training, accuracy, or scale results as this application's results.
