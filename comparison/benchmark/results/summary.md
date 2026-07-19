# Benchmark summary

Generated from frozen predictions and scorer-only golds. Negative paired latency
deltas favor StreamRAG. Automatic expected-answer/alias matching is a proxy, not a
semantic correctness judgment. Support additionally requires a valid exact-chunk
citation resolving to an acceptable gold document. Manual adjudication is optional.

| Path | Completed | Failures | Automatic match proxy | Support+valid citation | Manual semantic P/A | Median TTFT | p95 TTFT | Median total | Pre-Send reuse | In-flight overlap | Fallback | Usage-accounted calls/output | Cost/completed | Cost coverage | Accounting complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| naive | 10 | 0 | 100.0% | 100.0% | — | 2274 ms | 6818 ms | 2860 ms | 0.0% | 0.0% | 0.0% | 1.20 | $0.0125 | 100.0% | 100.0% |
| stream | 10 | 0 | 100.0% | 100.0% | — | 1185 ms | 6479 ms | 1814 ms | 100.0% | 0.0% | 0.0% | 4.10 | $0.0239 | 100.0% lower-bound/non-final | 0.0% |

Manual grounding gate: **not_requested** (0/20 completed outputs).
Run integrity gate: **complete** (0 issue(s)).
Adjudication integrity gate: **not_requested** (SHA-256: missing).
Final completeness gate: **complete** (0 failures; zero required).
Cost accounting gate: **lower_bound_non_final** (10 accounting-incomplete outputs).

## Paired StreamRAG deltas

- Completed A/B pairs: 10 / 10
- Stream TTFT win rate: 100.0%
- Median Stream minus Naive TTFT: -782 ms
- Median relative TTFT delta: -42.1%
- Median Stream minus Naive total time: -868 ms
- Mean Stream minus Naive cost (fully accounted pairs only): —
- Fully accounted cost pairs: 0 / 10
- Automatic-proxy discordance: {'stream_only_correct': 0, 'naive_only_correct': 0, 'same_outcome': 10}

## Stream evidence stages

Candidate retrieval lead is work moved before Send for the ultimately accepted
candidate. It is not accepted/safe evidence lead and not measured TTFT saved.

| Stage | Runs | Median TTFT | Median accepted-safe lead | Median / p95 candidate retrieval headroom | Automatic match proxy |
|---|---:|---:|---:|---:|---:|
| presubmit_reuse | 10 | 1185 ms | 3576 ms | 3576 ms / 4321 ms | 100.0% |

## Typed stabilization strata

Candidate classes are heuristic/manual-review labels assigned without seeing path outputs; they are not measured stabilization points.

| Class | Pairs | Naive TTFT | Stream TTFT | Stream reuse | Extra usage-accounted calls |
|---|---:|---:|---:|---:|---:|
| early_stabilization | 5 | 2362 ms | 1139 ms | 100.0% | 3.00 |
| late_stabilization | 4 | 1983 ms | 1225 ms | 100.0% | 2.00 |
| revision_or_ambiguity | 1 | 2397 ms | 1365 ms | 100.0% | 6.00 |
