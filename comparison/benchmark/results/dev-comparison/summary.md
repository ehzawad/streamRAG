# Development comparison — non-final

> Uses checksummed development questions only. The unseen test split was not run.

Configuration: `gpt-5.6-sol` at answer `medium`, trigger `low`, and summary `low` reasoning with `text-embedding-3-large`.

| Path | Automatic answer/alias proxy | Support+valid citation | Median TTFT | Median total | Usage-accounted model calls | Controllers | Retrievals | Dynamic function tools | Observed run cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| naive | 100.0% | 100.0% | 1901 ms | 2601 ms | 5 | 0 | 5 | 0 | $0.0555 |
| stream | 100.0% | 100.0% | 1357 ms | 2088 ms | 16 | 18 | 14 | 0 | ≥$0.1013 |

## Paired outcome

- Stream TTFT wins: 80.0%
- Median Stream minus Naive TTFT: -677 ms
- Median Stream minus Naive total: -691 ms
- Mean Stream minus Naive automatic-proxy score: 0.0 percentage points
- Measured wall time: 198.3 s

Dynamic function tools means model-issued `search_local_crag` calls after the
shared primary retrieval. Controller and retrieval calls are reported separately.
A ≥ cost is an observed lower bound because cancelled/timed-out requests may not
return provider usage and are deliberately never estimated as zero.
Human semantic-adjudication coverage is 0%; the automatic proxy is not a claim of
100% semantic correctness.

## Stabilization slices

| Candidate class | Questions | Stream TTFT wins | Median paired TTFT delta | Automatic-proxy delta |
|---|---:|---:|---:|---:|
| early_stabilization | 3 | 100.0% | -677 ms | 0.0 pp |
| late_stabilization | 1 | 0.0% | 489 ms | 0.0 pp |
| revision_or_ambiguity | 1 | 100.0% | -1599 ms | 0.0 pp |
